"""Regression checks for review identity, exclusions, and safe linking."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from html_to_txt import extract_review, write_outputs
from merge_reviews import merge_reviews
from review_common import (
    IDENTITY_FIELDS, MAP_FIELDS, REVIEW_FIELDS, canonical_url, read_csv,
    source_id_for_url, write_csv,
)


def html_review(slug="sample", artist="Artist", album="Album", description="Description."):
    url = f"https://pitchfork.com/reviews/albums/{slug}/"
    article = {
        "@type": "NewsArticle", "url": url,
        "datePublished": "2019-01-02T06:00:00.000Z",
        "author": {"name": "A. Writer"},
    }
    return (
        '<title>Unreliable:  Album Review | Pitchfork</title>'
        f'<link rel="canonical" href="{url}"/>'
        f'<meta name="description" content="{description}"/>'
        f'<script type="application/ld+json">{json.dumps(article)}</script>'
        '<ul class="artist-links"><li><a>' + artist + '</a></li><li>Other Artist</li></ul>'
        '<h1 class="single-album-tombstone__review-title"><i>' + album + '</i></h1>'
        '<span class="score">8.0</span>'
        '<h1 class="single-album-tombstone__review-title">Other Album</h1>'
        '<span class="score">2.0</span>'
        '<div class="contents"><p>First <em>review</em> paragraph.</p>'
        '<script>Hidden text.</script><p>Second paragraph.</p></div>'
    )


def source_row(source_id, artist="Artist", album="Album", date="2019-01-02", **updates):
    return {
        "source_id": source_id, "artist": artist, "album": album,
        "reviewdate": date, "reviewauthor": "A. Writer", "score": "8.0",
        "description": 'Description, "quoted".', "review": "First.\n\nSecond.",
        **updates,
    }


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_identity_comes_from_headings_and_source_url(self):
        row = extract_review(html_review(
            artist="Björk / Artist", album="Album: Where? / Here.",
            description="The artist&#x27;s description.",
        ))
        self.assertEqual(row["artist"], "Björk / Artist")
        self.assertEqual(row["album"], "Album: Where? / Here.")
        self.assertEqual(row["score"], "8.0")
        self.assertEqual(row["description"], "The artist's description.")
        self.assertEqual(row["review"], "First review paragraph.\n\nSecond paragraph.\n")
        self.assertEqual(row["source_id"], source_id_for_url(
            "http://www.pitchfork.com/reviews/albums/sample?tracking=1#fragment"
        ))
        self.assertEqual(canonical_url("http://www.pitchfork.com/reviews/albums/sample"),
                         "https://pitchfork.com/reviews/albums/sample/")

    def test_missing_heading_does_not_silently_use_combined_title(self):
        html = html_review().replace('<i>Album</i>', '')
        with self.assertRaisesRegex(ValueError, "album"):
            extract_review(html)

    def test_duplicate_html_groups_are_discarded_in_full_and_previous_files_archived(self):
        records = []
        # Same canonical URL, then different URLs for the same artist/album/date.
        for index, (slug, album) in enumerate([
            ("same", "One"), ("same", "One"), ("two-a", "Two"), ("two-b", "Two"),
            ("keep", "Keep"), ("missing", "Missing"),
        ]):
            row = extract_review(html_review(slug=slug, album=album,
                                            description="" if slug == "missing" else "Description."))
            records.append({**row, "html_file_name": f"{index}.html",
                            "text_file_name": row["source_id"] + ".txt"})
        output = self.root / "text_reviews"
        output.mkdir()
        (output / "Old___Name.txt").write_text("Previous text.")
        kept, discarded, backup = write_outputs(records, [], output, self.root)
        self.assertEqual((kept, discarded), (2, 4))
        self.assertEqual((backup / "text_reviews/Old___Name.txt").read_text(), "Previous text.")
        self.assertEqual(len(list(output.glob("*.txt"))), 2)
        missing = records[-1]["text_file_name"]
        self.assertEqual((self.root / "missing_descriptions.txt").read_text(), missing + "\n")
        _, manifest = read_csv(self.root / "html_text_match.csv")
        self.assertEqual(len(manifest), 2)

    def test_text_csv_uses_manifest_and_excludes_missing_descriptions(self):
        folder = self.root / "texts"
        folder.mkdir()
        rows = []
        for index, missing in enumerate((False, True)):
            row = source_row(source_id_for_url(f"https://pitchfork.com/reviews/albums/{index}/"),
                             artist="Björk", album='Album: "One"?')
            name = row["source_id"] + ".txt"
            rows.append({**{field: row[field] for field in IDENTITY_FIELDS},
                         "html_file_name": f"{index}.html", "text_file_name": name})
            text = row["review"] if missing else row["description"] + "\n\n" + row["review"]
            (folder / name).write_text(text, encoding="utf-8")
        write_csv(self.root / "manifest.csv", MAP_FIELDS, rows)
        (self.root / "missing.txt").write_text(rows[1]["text_file_name"] + "\n")
        command = [sys.executable, str(Path(__file__).with_name("txt_to_csv.py")),
                   "--input-dir", str(folder), "--manifest", str(self.root / "manifest.csv"),
                   "--missing-descriptions", str(self.root / "missing.txt"),
                   "-o", str(self.root / "review_text.csv")]
        subprocess.run(command, cwd=self.root, check=True, capture_output=True, text=True)
        fields, result = read_csv(self.root / "review_text.csv")
        self.assertEqual(fields, REVIEW_FIELDS)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["album"], 'Album: "One"?')
        self.assertEqual(result[0]["review"], "First.\n\nSecond.")

    def test_matching_dates_validation_and_all_duplicate_metadata_members_discarded(self):
        fields = ["review_id", "artist", "album", "reviewdate", "reviewauthor", "score", "recordlabel"]
        data, text = [], []
        for index, album in enumerate(["Good", "Bad author", "Bad score", "Duplicate", "Duplicate", "Ambiguous"]):
            row = source_row(str(index), album=album)
            data.append({field: row.get(field, str(index)) for field in fields})
            if album != "Duplicate" or index == 3:
                text.append(row)
        data[0]["artist"] = " ARTIST "
        data[0]["score"] = "8"
        text[1]["reviewauthor"] = "Different Writer"
        text[2]["score"] = "7.9"
        text.append(source_row("another", album="Ambiguous"))
        # Same artist/album, distinct dates: preserve the earlier review only.
        for index, date in enumerate(["2016-04-30", "2019-12-02"]):
            row = source_row(f"prince-{index}", artist="Prince", album="1999", date=date)
            data.append({field: row.get(field, str(10 + index)) for field in fields})
        text.append(source_row("prince-early", artist="Prince", album="1999", date="2016-04-30"))
        # An ordinary date mismatch must also stay unresolved.
        data.append({field: source_row("late", album="Date mismatch", date="2019-01-03").get(field, "late") for field in fields})
        text.append(source_row("old", album="Date mismatch"))
        write_csv(self.root / "data.csv", fields, data)
        write_csv(self.root / "text.csv", REVIEW_FIELDS, text)
        stats = merge_reviews(self.root / "data.csv", self.root / "text.csv", self.root / "out")
        self.assertEqual(stats["merged"], 2)
        self.assertEqual(stats["data_discarded"], 3)
        self.assertEqual(stats["text_discarded"], 1)
        _, merged = read_csv(self.root / "out/full_review.csv")
        self.assertEqual({row["source_id"] for row in merged}, {"0", "prince-early"})
        _, unresolved = read_csv(self.root / "out/missed_on_merge/data_clean_unmatched.csv")
        self.assertEqual({row["merge_reason"] for row in unresolved}, {
            "author_mismatch", "score_mismatch", "ambiguous_review_match", "review_date_mismatch",
        })

    def test_discarded_html_identity_is_excluded_on_both_sides(self):
        row = source_row("discard")
        fields = ["artist", "album", "reviewdate", "reviewauthor", "score"]
        write_csv(self.root / "data.csv", fields, [{field: row[field] for field in fields}])
        write_csv(self.root / "text.csv", REVIEW_FIELDS, [row])
        write_csv(self.root / "discarded.csv", fields, [{field: row[field] for field in fields}])
        stats = merge_reviews(self.root / "data.csv", self.root / "text.csv", self.root / "out", self.root / "discarded.csv")
        self.assertEqual((stats["merged"], stats["data_discarded"], stats["text_discarded"]), (0, 1, 1))


if __name__ == "__main__":
    unittest.main()
