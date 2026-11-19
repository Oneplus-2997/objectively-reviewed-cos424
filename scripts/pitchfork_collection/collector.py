"""Notebook-callable author selection, discovery, collection, and exports."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import tempfile

from .authors import AuthorResolver, rank_authors
from .client import HtmlClient
from .common import (
    AUTHOR_FIELDS, DEFAULT_OUTPUT, LISTING_URL, METADATA_FIELDS, REPO_ROOT,
    REVIEW_FIELDS, canonical_url, today, write_csv, write_json,
)
from .parsing import parse_listing, parse_review


@dataclass
class CollectionConfig:
    data_path: Path = REPO_ROOT / "data.csv"
    output_dir: Path = DEFAULT_OUTPUT
    top_n: int = 10
    min_reviews: int = 0
    authors: tuple[str, ...] = ()
    aliases_path: Path | None = None
    end_date: date = field(default_factory=today)
    max_pages: int = 200
    max_reviews: int | None = None
    delay: float = 2.0
    timeout: float = 30.0
    retries: int = 2
    offline: bool = False
    refresh: bool = False
    review_urls: tuple[str, ...] = ()
    html_inputs: tuple[Path, ...] = ()


@dataclass
class CollectionResult:
    output_dir: Path
    records: list[dict]
    summary: dict

    @property
    def run_dir(self):
        """Compatibility for notebooks using the previous result attribute."""
        return self.output_dir


def _remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _publish(staged, output):
    """Replace this collector's generated files and directories as one result set.

    Keep the standalone author_counts.csv and unrelated files in output_dir.
    Staging lets local/offline inputs be read before their old caches are removed.
    """
    output.mkdir(parents=True, exist_ok=True)
    for relative in ("raw_html", "cache/listings"):
        source, target = staged / relative, output / relative
        source.mkdir(parents=True, exist_ok=True)
        _remove(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
    for source in staged.iterdir():
        if source.is_file():
            source.replace(output / source.name)
    _remove(output / "runs")
    _remove(output / "author_period_counts.csv")


def _period(value, end_date):
    parsed = date.fromisoformat(value)
    if date(2023, 1, 1) <= parsed < date(2024, 1, 1):
        return "2023"
    if date(2024, 1, 1) <= parsed <= end_date:
        return "2024_present"
    return None


def collect_reviews(config=None, *, fetcher=None, progress=None):
    """Collect a fixed historical author cohort for two publication-date sets.

    Pass a callable ``fetcher(url) -> html`` to supply a different transport.
    ``html_inputs`` parses local files without networking. ``review_urls`` skips
    discovery but still filters by the selected authors and publication dates.
    Replace the latest results directly in output_dir. Online collections use
    fresh caches; offline/local collections retain only the pages used this time.
    """
    config = config or CollectionConfig()
    if config.end_date < date(2024, 1, 1):
        raise ValueError("end_date must be 2024-01-01 or later")
    if config.max_pages < 1 or (config.max_reviews is not None and config.max_reviews < 1):
        raise ValueError("page and review limits must be positive")
    if config.delay < 0 or config.timeout <= 0 or config.retries < 0:
        raise ValueError("invalid delay, timeout, or retry count")
    if config.review_urls and config.html_inputs:
        raise ValueError("choose review URLs or local HTML inputs, not both")
    resolver = AuthorResolver.from_file(config.aliases_path)
    output = Path(config.output_dir).resolve()
    rankings = rank_authors(config.data_path, resolver=resolver, top_n=config.top_n,
                            min_reviews=config.min_reviews, authors=config.authors)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pitchfork_collection_", dir=output.parent) as temporary:
        return _collect(config, rankings, resolver, output, Path(temporary), fetcher, progress)


def _collect(config, rankings, resolver, output, staged, fetcher, progress):
    selected = {resolver.key(r["author"]): r["author"] for r in rankings if r["selected"]}
    client = HtmlClient(staged, delay=config.delay, timeout=config.timeout,
                        retries=config.retries, offline=config.offline, refresh=config.refresh)
    previous_cache = HtmlClient(output, offline=True) if config.offline else None
    events, discovery, records = [], [], {}
    seen_urls, seen_sources = set(), set()
    attempted = 0
    pages = 0
    stop_reason = "provided_urls" if config.review_urls else "local_html" if config.html_inputs else ""
    interrupted = False

    def log(kind, url, status, *, reason="", count=0, raw_html=""):
        if raw_html:
            raw_html = output / "raw_html" / Path(raw_html).name
        events.append({"kind": kind, "url": str(url), "status": status,
                       "record_count": count, "raw_html": str(raw_html), "reason": reason})
        if progress:
            progress(f"{kind}: {status}: {url}" + (f" ({reason})" if reason else ""))

    def load(url, *, listing=False):
        if fetcher or previous_cache:
            html = fetcher(url) if fetcher else previous_cache.fetch(url, listing=listing)
            path = client.cache_path(url, listing=listing)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
            return html
        return client.fetch(url, listing=listing)

    def process(html, url=None, local_path=None):
        parsed = parse_review(html, url)
        first = parsed[0]
        sid = first["source_id"]
        if sid in seen_sources:
            log("review", first["review_url"], "duplicate", reason="canonical URL already processed")
            return
        seen_sources.add(sid)
        path = staged / "raw_html" / f"{sid}.html"
        if not path.exists() or config.refresh or local_path or fetcher:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
        byline = json.loads(first["author_names"])
        matches = list(dict.fromkeys(selected[resolver.key(a)] for a in byline if resolver.key(a) in selected))
        period = _period(first["reviewdate"], config.end_date)
        if not matches or not period:
            log("review", first["review_url"], "excluded", raw_html=path,
                reason="author_not_selected" if not matches else "outside_publication_periods")
            return
        for row in parsed:
            row["selected_authors"] = json.dumps(matches, ensure_ascii=False)
            records[row["record_id"]] = row
        log("review", first["review_url"], "collected", count=len(parsed), raw_html=path)

    def review(url):
        nonlocal attempted
        try:
            url = canonical_url(url)
            if url in seen_urls:
                return
            seen_urls.add(url)
            attempted += 1
            process(load(url), url)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log("review", url, "error", reason=str(exc))

    def limit_reached():
        return config.max_reviews is not None and attempted >= config.max_reviews

    try:
        if config.html_inputs:
            files = set()
            for entry in map(Path, config.html_inputs):
                if entry.is_dir():
                    files.update(p.resolve() for p in entry.rglob("*.html"))
                elif entry.is_file():
                    files.add(entry.resolve())
                else:
                    log("local_html", entry, "error", reason="input does not exist")
            if not files:
                log("local_html", "", "error", reason="no HTML files found")
            for path in sorted(files):
                if limit_reached():
                    stop_reason = "review_limit"
                    break
                attempted += 1
                try:
                    process(path.read_text(encoding="utf-8-sig"), local_path=path)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    log("review", path, "error", reason=str(exc))
        elif config.review_urls:
            for url in config.review_urls:
                if limit_reached():
                    stop_reason = "review_limit"
                    break
                review(url)
        else:
            next_url, visited_pages, fingerprints, old_pages = LISTING_URL, set(), set(), 0
            while next_url:
                if pages >= config.max_pages:
                    stop_reason = "page_limit"
                    break
                if next_url in visited_pages:
                    log("listing", next_url, "error", reason="pagination cycle")
                    stop_reason = "pagination_cycle"
                    break
                visited_pages.add(next_url)
                pages += 1
                try:
                    issues = []
                    cards, following = parse_listing(load(next_url, listing=True), issues=issues)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    log("listing", next_url, "error", reason=str(exc))
                    stop_reason = "listing_error"
                    break
                fingerprint = tuple(sorted(c["url"] for c in cards))
                if fingerprint in fingerprints:
                    log("listing", next_url, "error", reason="pagination returned repeated review cards")
                    stop_reason = "repeated_page"
                    break
                fingerprints.add(fingerprint)
                log("listing", next_url, "parsed", count=len(cards))
                for issue in issues:
                    log("listing_card", issue["url"], "error", reason=issue["reason"])
                dated = [c["reviewdate"] for c in cards if c["reviewdate"]]
                old_pages = old_pages + 1 if len(dated) == len(cards) and max(dated) < "2023-01-01" else 0
                for card in cards:
                    decision = "candidate"
                    if card["reviewdate"] and not _period(card["reviewdate"], config.end_date):
                        decision = "outside_publication_periods"
                    elif card["authors"] and not any(resolver.key(a) in selected for a in card["authors"]):
                        decision = "author_not_selected"
                    discovery.append({"review_url": card["url"], "listing_url": next_url,
                                      "reviewdate": card["reviewdate"],
                                      "authors": json.dumps(card["authors"], ensure_ascii=False),
                                      "decision": decision})
                    if decision == "candidate":
                        if limit_reached():
                            stop_reason = "review_limit"
                            break
                        review(card["url"])
                if stop_reason == "review_limit":
                    break
                # Two fully dated older pages reduce sensitivity to a single
                # pinned/out-of-order card. Completeness still needs auditing.
                if old_pages >= 2:
                    stop_reason = "date_boundary"
                    break
                next_url = following
            if not stop_reason:
                stop_reason = "end_of_pagination"
    except KeyboardInterrupt:
        interrupted, stop_reason = True, "interrupted"

    rows = sorted(records.values(), key=lambda r: (r["reviewdate"], r["review_url"], r["record_id"]))
    author_pages = defaultdict(set)
    counts = {}
    for period in ("2023", "2024_present"):
        subset = [r for r in rows if _period(r["reviewdate"], config.end_date) == period]
        write_csv(staged / f"reviews_{period}.csv", REVIEW_FIELDS, subset)
        write_csv(staged / f"metadata_{period}.csv", METADATA_FIELDS,
                  [{k: row[k] for k in METADATA_FIELDS} for row in subset])
        counts[period] = {"album_rows": len(subset), "review_pages": len({r["source_id"] for r in subset})}
        for row in subset:
            for author in json.loads(row["selected_authors"]):
                author_pages[(author, period)].add(row["source_id"])
    write_csv(staged / "author_counts_collected.csv",
              AUTHOR_FIELDS + ["reviews_2023", "reviews_2024_to_present"], [
                  {**row, "reviews_2023": len(author_pages[(row["author"], "2023")]),
                   "reviews_2024_to_present": len(author_pages[(row["author"], "2024_present")])}
                  for row in rankings if row["selected"]
              ])
    event_fields = ["kind", "url", "status", "record_count", "raw_html", "reason"]
    failures = [e for e in events if e["status"] == "error"]
    write_csv(staged / "manifest.csv", event_fields, events)
    write_csv(staged / "failures.csv", event_fields, failures)
    write_csv(staged / "discovered_reviews.csv",
              ["review_url", "listing_url", "reviewdate", "authors", "decision"], discovery)
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "publication_periods": {"2023": ["2023-01-01", "2023-12-31"],
                                "2024_present": ["2024-01-01", config.end_date.isoformat()]},
        "selected_authors": list(selected.values()), "selection": "explicit" if config.authors else "top_n",
        "top_n": config.top_n, "min_reviews": config.min_reviews,
        "historical_data": str(Path(config.data_path).resolve()), "author_aliases": resolver.aliases,
        "scope": "local_html" if config.html_inputs else "provided_urls" if config.review_urls else "album_archive",
        "stop_reason": stop_reason, "pages_scanned": pages, "reviews_attempted": attempted,
        "period_counts": counts, "failure_count": len(failures), "interrupted": interrupted,
        "archive_boundary_reached": stop_reason in {"date_boundary", "end_of_pagination"},
        "needs_coverage_review": True,
        "limits": {"max_pages": config.max_pages, "max_reviews": config.max_reviews},
        "offline": config.offline, "refresh": config.refresh,
        "local_inputs": [str(p) for p in config.html_inputs], "provided_urls": list(config.review_urls),
        "raw_html_dir": str(output / "raw_html"),
        "output_dir": str(output),
        "runtime": {"python_version": platform.python_version(), "third_party_dependencies": []},
    }
    write_json(staged / "summary.json", summary)
    _publish(staged, output)
    if progress:
        progress(f"Saved {len(rows)} album rows to {output}")
    return CollectionResult(output, rows, summary)
