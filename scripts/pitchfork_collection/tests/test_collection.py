import csv
from datetime import date
from io import BytesIO
from email.message import Message
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.pitchfork_collection import (
    AuthorResolver, CollectionConfig, ParseError, collect_reviews, parse_listing,
    parse_review, rank_authors, write_author_counts,
)
from scripts.pitchfork_collection.client import FetchError, HtmlClient
from scripts.pitchfork_collection.__main__ import main
from scripts.pitchfork_collection.common import LISTING_URL, canonical_url, parse_date, write_csv


def page(slug="sample", author="A. Writer", published="2024-01-02", *, multi=False):
    url = f"https://pitchfork.com/reviews/albums/{slug}/"
    rating = {"score": 8.2, "isBestNewMusic": True}
    header = {
        "dangerousHed": "<em>Album</em>", "dangerousDek": "An <em>original</em> description.",
        "artists": [{"name": "Artist"}],
        "contributors": {"author": {"items": [{"name": author}]}},
        "infoSliceFields": {"genre": "Rock", "label": "Label / Other", "releaseYear": "1998"},
        "musicRating": rating,
        # Modified dates must never control dataset selection.
        "modifiedDate": "2026-01-01", "publishDate": "January 1, 2026",
    }
    review = {
        "headerProps": header, "rating": 0.0,
        "body": ["div", ["p", "First ", ["em", "paragraph"], "."],
                 ["cm-unit", ["p", "Advertisement."]],
                 ["p", "Second paragraph."], ["script", "Hidden."]],
    }
    if multi:
        review["multiReviewHeaderProps"] = {"itemsReviewed": [
            {"dangerousHed": "One", "publisher": "First", "releaseYear": "2023", "musicRating": rating},
            {"dangerousHed": "Two", "publisher": "Second", "releaseYear": "2024", "musicRating": {"score": 7.1}},
        ]}
    ld = {"@type": "Review", "datePublished": published + "T00:01:00-05:00",
          "dateModified": "2026-01-01T01:00:00Z", "author": [{"name": author}]}
    return (
        f'<link rel="canonical" href="{url}">'
        '<span class="reader-score">0.0</span>'
        f'<script type="application/ld+json">{json.dumps(ld)}</script>'
        '<script>window.__PRELOADED_STATE__ = ' +
        json.dumps({"transformed": {"review": review}}) + ';</script>'
    )


def listing(cards, following=None):
    items = [{"url": f"/reviews/albums/{slug}/", "date": published,
              "contributors": {"author": {"items": [{"name": author}]}},
              "ratingValue": {"score": 1.0}}
             for slug, author, published in cards]
    data = {"transformed": {"bundle": {"containers": [{"items": items}]},
                             "head.nextPage": following}}
    return '<script>window.__PRELOADED_STATE__ = ' + json.dumps(data) + ';</script>'


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data.csv"
        rows = [
            {"artist": "Old Artist", "album": "Old", "reviewauthor": "A. Writer", "reviewdate": "January 1 2019"},
            {"artist": "Old Artist", "album": "Old", "reviewauthor": "A.  Writer", "reviewdate": "2019-01-01"},
            {"artist": "Other", "album": "Older", "reviewauthor": "A. Writer", "reviewdate": "2018-02-01"},
            {"artist": "Other", "album": "Old", "reviewauthor": "B. Writer", "reviewdate": "2019-01-01"},
        ]
        write_csv(self.data, list(rows[0]), rows)

    def config(self, **updates):
        return CollectionConfig(data_path=self.data, output_dir=self.root / "output",
                                end_date=date(2026, 11, 12), delay=0, **updates)

    def read(self, path):
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def test_author_ranking_normalizes_spacing_and_counts_review_identities(self):
        rows = rank_authors(self.data, top_n=1)
        self.assertEqual((rows[0]["author"], rows[0]["dataset_rows"], rows[0]["unique_reviews"]),
                         ("A. Writer", 3, 2))
        self.assertEqual(sum(r["selected"] for r in rows), 1)
        self.assertEqual(len(json.loads(rows[0]["variants"])), 2)
        resolver = AuthorResolver({"Another Name": "A. Writer"})
        self.assertEqual(resolver.key("Another Name"), resolver.key("A. Writer"))
        with self.assertRaises(ValueError):
            AuthorResolver({"One": "Two", "Two": "One"})

    def test_authors_command_saves_all_by_default_or_only_requested_count(self):
        output = self.root / "rankings"
        with patch("sys.stdout"):
            self.assertEqual(main(["authors", "--data", str(self.data), "--output-dir", str(output)]), 0)
        self.assertEqual([r["author"] for r in self.read(output / "author_counts.csv")],
                         ["A. Writer", "B. Writer"])
        with patch("sys.stdout"):
            self.assertEqual(main(["authors", "--data", str(self.data), "--output-dir", str(output),
                                   "--top-n", "1"]), 0)
        selected = self.read(output / "author_counts.csv")
        self.assertEqual([row["author"] for row in selected], ["A. Writer"])
        self.assertFalse((output / "all_author_counts.csv").exists())
        with patch("sys.stdout"):
            main(["authors", "--data", str(self.data), "--output-dir", str(output),
                  "--author", "B. Writer"])
        self.assertEqual([row["author"] for row in self.read(output / "author_counts.csv")], ["B. Writer"])

    def test_collect_ranks_data_independently_of_limited_or_missing_saved_counts(self):
        output = self.root / "output"
        write_author_counts(output, rank_authors(self.data, authors=("B. Writer",)))
        result = collect_reviews(self.config(top_n=2, review_urls=(LISTING_URL + "sample/",)),
                                 fetcher=lambda _: page())
        self.assertEqual(result.summary["selected_authors"], ["A. Writer", "B. Writer"])
        self.assertEqual(result.summary["historical_data"], str(self.data.resolve()))
        self.assertEqual(len(result.records), 1)
        self.assertEqual([r["author"] for r in self.read(output / "author_counts.csv")], ["B. Writer"])
        (output / "author_counts.csv").unlink()
        result = collect_reviews(self.config(top_n=1, review_urls=(LISTING_URL + "sample/",)),
                                 fetcher=lambda _: page())
        self.assertEqual(result.summary["selected_authors"], ["A. Writer"])
        self.assertFalse((output / "author_counts.csv").exists())

    def test_collect_command_uses_data_without_an_authors_export(self):
        output = self.root / "cli_output"
        local = self.root / "html"
        local.mkdir()
        (local / "sample.html").write_text(page(), encoding="utf-8")
        with patch("sys.stdout"):
            code = main(["collect", "--data", str(self.data), "--output-dir", str(output),
                         "--top-n", "2", "--html-dir", str(local)])
        self.assertEqual(code, 0)
        self.assertFalse((output / "author_counts.csv").exists())
        self.assertFalse((output / "runs").exists())
        self.assertEqual([r["author"] for r in self.read(output / "author_counts_collected.csv")],
                         ["A. Writer", "B. Writer"])

    def test_parser_uses_critic_score_and_publication_date_and_excludes_embeds(self):
        row = parse_review(page(published="2023-12-31"))[0]
        self.assertEqual((row["score"], row["reviewdate"], row["releaseyear"]), ("8.2", "2023-12-31", "1998"))
        self.assertEqual(row["album"], "Album")
        self.assertEqual(row["recordlabel"], "Label / Other")
        self.assertEqual(row["review"], "First paragraph.\n\nSecond paragraph.")
        with self.assertRaises(ParseError):
            parse_review("<html>Access unavailable</html>")

    def test_multiple_albums_keep_distinct_records_and_shared_review_identity(self):
        rows = parse_review(page(multi=True))
        self.assertEqual([r["album"] for r in rows], ["One", "Two"])
        self.assertEqual([r["score"] for r in rows], ["8.2", "7.1"])
        self.assertEqual(len({r["source_id"] for r in rows}), 1)
        self.assertEqual(len({r["record_id"] for r in rows}), 2)

    def test_timestamps_use_publication_calendar_date_in_new_york(self):
        self.assertEqual(parse_date("2024-01-01T01:00:00Z"), date(2023, 12, 31))
        self.assertEqual(parse_date("2024-01-01T05:00:00Z"), date(2024, 1, 1))

    def test_coauthored_review_is_one_page_but_counts_for_both_selected_authors(self):
        html = page().replace('"items": [{"name": "A. Writer"}]',
                              '"items": [{"name": "A. Writer"}, {"name": "B. Writer"}]')
        result = collect_reviews(self.config(top_n=2, review_urls=(LISTING_URL + "sample/",)),
                                 fetcher=lambda _: html)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(len(json.loads(result.records[0]["selected_authors"])), 2)
        rows = self.read(result.output_dir / "author_counts_collected.csv")
        self.assertEqual([r["reviews_2024_to_present"] for r in rows], ["1", "1"])

    def test_interrupted_run_exports_already_collected_records(self):
        calls = []
        def fetch(url):
            if calls:
                raise KeyboardInterrupt
            calls.append(url)
            return page()
        result = collect_reviews(self.config(top_n=1, review_urls=(LISTING_URL + "sample/", LISTING_URL + "two/")),
                                 fetcher=fetch)
        self.assertTrue(result.summary["interrupted"])
        self.assertEqual(len(self.read(result.output_dir / "reviews_2024_present.csv")), 1)

    def test_supplied_urls_filter_exact_dates_deduplicate_and_preserve_failures(self):
        base = "https://pitchfork.com/reviews/albums/"
        urls = [base + slug + "/" for slug in ["old", "last2023", "first2024", "future", "other", "bad"]]
        htmls = dict(zip(urls, [
            page("old", published="2022-12-31"), page("last2023", published="2023-12-31"),
            page("first2024", published="2024-01-01"), page("future", published="2026-11-13"),
            page("other", author="B. Writer"), "<html>Blocked</html>",
        ]))
        result = collect_reviews(self.config(top_n=1, review_urls=tuple(urls + [urls[1] + "?track=1"])),
                                 fetcher=htmls.__getitem__)
        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.summary["reviews_attempted"], 6)
        self.assertEqual(result.summary["failure_count"], 1)
        self.assertEqual(len(self.read(result.output_dir / "reviews_2023.csv")), 1)
        self.assertEqual(len(self.read(result.output_dir / "reviews_2024_present.csv")), 1)
        self.assertTrue((result.output_dir / "summary.json").exists())
        self.assertEqual(result.summary["archive_boundary_reached"], False)

    def test_discovery_prefilters_and_stops_after_two_complete_older_pages(self):
        second, third = LISTING_URL + "?page=2", LISTING_URL + "?page=3"
        target = LISTING_URL + "target/"
        htmls = {
            LISTING_URL: listing([("wrong", "B. Writer", "2024-01-01"),
                                  ("target", "A. Writer", "2023-06-01")], second),
            second: listing([("older", "A. Writer", "2022-12-01")], third),
            third: listing([("oldest", "A. Writer", "2022-11-01")], LISTING_URL + "?page=4"),
            target: page("target", published="2023-06-01"),
        }
        calls = []
        def fetch(url):
            calls.append(url)
            return htmls[url]
        result = collect_reviews(self.config(top_n=1), fetcher=fetch)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(calls, [LISTING_URL, target, second, third])
        self.assertEqual(result.summary["stop_reason"], "date_boundary")

    def test_page_limits_and_repeated_pages_are_reported(self):
        content = listing([("target", "B. Writer", "2024-01-01")], LISTING_URL + "?page=2")
        limited = collect_reviews(self.config(top_n=1, max_pages=1), fetcher=lambda _: content)
        self.assertEqual(limited.summary["stop_reason"], "page_limit")
        repeated = collect_reviews(self.config(top_n=1), fetcher=lambda _: content)
        self.assertEqual(repeated.summary["stop_reason"], "repeated_page")
        self.assertEqual(repeated.summary["failure_count"], 1)

    def test_collection_overwrites_results_and_replaces_generated_directories(self):
        output = self.root / "output"
        for relative in ["raw_html/deep/stale.html", "cache/listings/deep/stale.html", "runs/old/summary.json"]:
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Old data.")
        (output / "reviews_2024_present.csv").write_text("Old CSV.")
        (output / "author_period_counts.csv").write_text("Old report.")
        (output / "notes.txt").write_text("Keep this.")
        write_author_counts(output, rank_authors(self.data, authors=("B. Writer",)))
        first = collect_reviews(self.config(top_n=2, review_urls=(LISTING_URL + "first/",)),
                                fetcher=lambda _: page("first"))
        self.assertEqual(first.output_dir, output.resolve())
        self.assertFalse((output / "runs").exists())
        self.assertFalse((output / "raw_html/deep").exists())
        self.assertFalse((output / "cache/listings/deep").exists())
        self.assertFalse((output / "author_period_counts.csv").exists())
        self.assertEqual((output / "notes.txt").read_text(), "Keep this.")
        first_raw = output / "raw_html" / (first.records[0]["source_id"] + ".html")
        self.assertTrue(first_raw.exists())
        second = collect_reviews(self.config(top_n=1, review_urls=(LISTING_URL + "second/",)),
                                 fetcher=lambda _: page("second", published="2023-06-01"))
        self.assertEqual(second.output_dir, first.output_dir)
        self.assertEqual(len(self.read(output / "reviews_2024_present.csv")), 0)
        self.assertEqual(len(self.read(output / "reviews_2023.csv")), 1)
        self.assertFalse(first_raw.exists())
        self.assertEqual(len(list((output / "raw_html").glob("*.html"))), 1)
        self.assertEqual([r["author"] for r in self.read(output / "author_counts.csv")], ["B. Writer"])

    def test_local_html_inside_output_is_read_before_its_directory_is_replaced(self):
        local = self.root / "output/raw_html"
        local.mkdir(parents=True)
        (local / "one.html").write_text(page(), encoding="utf-8")
        result = collect_reviews(self.config(top_n=1, html_inputs=(local,)))
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.summary["scope"], "local_html")
        self.assertFalse((local / "one.html").exists())
        self.assertEqual(len(list(local.glob("*.html"))), 1)
        repeated = collect_reviews(self.config(top_n=1, html_inputs=(local,)))
        self.assertEqual(len(repeated.records), 1)
        self.assertEqual(result.output_dir, repeated.output_dir)

    def test_collected_author_counts_include_historical_fields_and_unique_pages(self):
        old, recent = LISTING_URL + "old/", LISTING_URL + "recent/"
        htmls = {old: page("old", published="2023-05-01", multi=True),
                 recent: page("recent", published="2024-05-01")}
        result = collect_reviews(self.config(top_n=2, review_urls=(old, recent)), fetcher=htmls.__getitem__)
        rows = self.read(result.output_dir / "author_counts_collected.csv")
        self.assertEqual([r["author"] for r in rows], ["A. Writer", "B. Writer"])
        self.assertEqual((rows[0]["dataset_rows"], rows[0]["unique_reviews"]), ("3", "2"))
        self.assertEqual((rows[0]["reviews_2023"], rows[0]["reviews_2024_to_present"]), ("1", "1"))
        self.assertEqual((rows[1]["reviews_2023"], rows[1]["reviews_2024_to_present"]), ("0", "0"))
        self.assertEqual(result.summary["period_counts"]["2023"]["album_rows"], 2)

    def test_offline_collection_keeps_only_the_cached_pages_it_used(self):
        output = self.root / "output"
        client = HtmlClient(output, offline=True)
        first, second = LISTING_URL + "first/", LISTING_URL + "second/"
        for url, slug in [(first, "first"), (second, "second")]:
            cache = client.cache_path(url)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(page(slug), encoding="utf-8")
        listing_cache = client.cache_path(LISTING_URL, listing=True)
        listing_cache.parent.mkdir(parents=True)
        listing_cache.write_text("Unused listing cache.")
        with patch("scripts.pitchfork_collection.client.urlopen") as network:
            result = collect_reviews(self.config(top_n=1, review_urls=(first,), offline=True))
            network.assert_not_called()
        self.assertEqual(len(result.records), 1)
        self.assertTrue(client.cache_path(first).exists())
        self.assertFalse(client.cache_path(second).exists())
        self.assertFalse(listing_cache.exists())

    def test_online_collection_downloads_fresh_pages_without_refresh_flag(self):
        output = self.root / "output"
        url = LISTING_URL + "sample/"
        cache = HtmlClient(output).cache_path(url)
        cache.parent.mkdir(parents=True)
        cache.write_text(page().replace('"score": 8.2', '"score": 1.0'), encoding="utf-8")
        headers = Message()
        headers["Content-Type"] = "text/html; charset=utf-8"
        with patch("scripts.pitchfork_collection.client.urlopen") as network:
            response = network.return_value.__enter__.return_value
            response.geturl.return_value = url
            response.headers = headers
            response.read.return_value = page().encode("utf-8")
            result = collect_reviews(self.config(top_n=1, review_urls=(url,)))
            network.assert_called_once()
        self.assertEqual(result.records[0]["score"], "8.2")
        self.assertIn('"score": 8.2', cache.read_text())

    def test_jsonld_fallback_defers_unknown_author_and_date_to_detail(self):
        html = '<script type="application/ld+json">' + json.dumps({
            "@type": "ItemList", "itemListElement": [
                {"@type": "ListItem", "url": LISTING_URL + "sample/"},
            ]}) + '</script><a href="?page=2">Next</a>'
        cards, following = parse_listing(html)
        self.assertEqual(cards[0]["authors"], [])
        self.assertEqual(cards[0]["reviewdate"], "")
        self.assertEqual(following, LISTING_URL + "?page=2")

    def test_cached_pages_work_offline_and_long_retry_after_is_not_ignored(self):
        url = LISTING_URL + "sample/"
        client = HtmlClient(self.root, offline=True)
        cache = client.cache_path(url)
        cache.parent.mkdir(parents=True)
        cache.write_text(page(), encoding="utf-8")
        with patch("scripts.pitchfork_collection.client.urlopen") as network:
            self.assertIn("__PRELOADED_STATE__", client.fetch(url))
            network.assert_not_called()
        client = HtmlClient(self.root, delay=0, refresh=True)
        error = HTTPError(url, 429, "limit", {"Retry-After": "120"}, BytesIO())
        with patch("scripts.pitchfork_collection.client.urlopen", side_effect=error):
            with self.assertRaisesRegex(FetchError, "resume later"):
                client.fetch(url)
        with self.assertRaises(ValueError):
            canonical_url("https://example.com/reviews/albums/sample/")


if __name__ == "__main__":
    unittest.main()
