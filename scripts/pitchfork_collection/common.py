"""Paths, schemas, dates, and identity shared by the collection helpers."""

import csv
from datetime import date, datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "outputs" / "pitchfork_collection"
BASE_URL = "https://pitchfork.com"
LISTING_URL = BASE_URL + "/reviews/albums/"
METADATA_FIELDS = [
    "artist", "album", "reviewauthor", "score", "releaseyear", "reviewdate",
    "recordlabel", "genre",
]
REVIEW_FIELDS = METADATA_FIELDS + [
    "record_id", "source_id", "review_url", "author_names", "selected_authors",
    "description", "review", "accolade", "artwork_url", "multi_album_review",
]
AUTHOR_FIELDS = ["rank", "author", "dataset_rows", "unique_reviews", "selected", "variants"]


def today():
    """Use the user's client timezone rather than the execution host timezone."""
    return datetime.now(ZoneInfo("America/New_York")).date()


def clean_name(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def name_key(value):
    return clean_name(value).translate(str.maketrans({"’": "'", "‘": "'"})).casefold()


def parse_date(value):
    value = clean_name(value)
    try:
        return date.fromisoformat(value)
    except ValueError:
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (instant.astimezone(ZoneInfo("America/New_York")) if instant.tzinfo else instant).date()
        except ValueError:
            pass
        for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                pass
    raise ValueError(f"invalid publication date: {value!r}")


def canonical_url(value, *, listing=False):
    parts = urlsplit(urljoin(LISTING_URL if listing else BASE_URL, str(value or "").strip()))
    if parts.hostname not in {"pitchfork.com", "www.pitchfork.com"}:
        raise ValueError("URL must belong to pitchfork.com")
    if parts.scheme not in {"http", "https"} or parts.username or parts.password:
        raise ValueError("invalid Pitchfork URL")
    path = parts.path.rstrip("/") + "/"
    if not path.startswith("/reviews/albums/"):
        raise ValueError("URL is not an album review")
    if not listing and path == "/reviews/albums/":
        raise ValueError("expected a review, got the listing")
    if listing and path != "/reviews/albums/":
        raise ValueError("pagination must stay on the album listing")
    return urlunsplit(("https", "pitchfork.com", path, parts.query if listing else "", ""))


def source_id(url):
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:32]


def write_csv(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    with staged.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    staged.replace(path)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    staged.replace(path)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"br", "p", "div"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div"}:
            self.parts.append(" ")

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def html_text(value):
    parser = _Text()
    parser.feed(str(value or ""))
    parser.close()
    return clean_name("".join(parser.parts))
