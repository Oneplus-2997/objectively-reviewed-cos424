"""Shared CSV schemas, review identity, and conservative matching rules."""

from collections import Counter
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import unicodedata
from urllib.parse import urlsplit, urlunsplit


OUTPUTS = Path(__file__).resolve().parent.parent / "outputs"
EXTRACTION_OUTPUTS = OUTPUTS / "extracting_text"
IDENTITY_FIELDS = [
    "source_id", "artist", "album", "reviewdate", "reviewauthor", "score"
]
REVIEW_FIELDS = IDENTITY_FIELDS + ["description", "review"]
MATCH_FIELDS = ["artist", "album", "reviewdate", "reviewauthor", "score"]
MAP_FIELDS = ["source_id", "html_file_name", "text_file_name"] + IDENTITY_FIELDS[1:]
QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "″": '"',
})


def normalize_name(value):
    """Normalize Unicode, quotes, whitespace, and case without losing punctuation."""
    return " ".join(
        unicodedata.normalize("NFKC", value or "").translate(QUOTE_MAP).split()
    ).casefold()


def review_date(value):
    return date.fromisoformat((value or "").strip()[:10]).isoformat()


def review_score(value):
    try:
        score = Decimal((value or "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid review score: {value!r}") from exc
    if not score.is_finite() or not 0 <= score <= 10:
        raise ValueError(f"invalid review score: {value!r}")
    return score


def match_key(row):
    artist, album = (normalize_name(row.get(field)) for field in ("artist", "album"))
    if not artist or not album:
        raise ValueError("missing artist or album")
    return artist, album, review_date(row.get("reviewdate"))


def duplicate_keys(rows):
    counts = Counter()
    for row in rows:
        try:
            counts[match_key(row)] += 1
        except ValueError:
            continue
    return {key for key, count in counts.items() if count > 1}


# The later Prince review has no HTML; the earlier review must remain eligible.
REQUESTED_EXCLUSIONS = {("prince", "1999", "2019-12-02")}


def canonical_url(value):
    parts = urlsplit((value or "").strip())
    if parts.hostname not in {"pitchfork.com", "www.pitchfork.com"}:
        raise ValueError("missing or invalid canonical Pitchfork URL")
    if not parts.path.startswith("/reviews/albums/"):
        raise ValueError("canonical URL is not an album review")
    return urlunsplit(("https", "pitchfork.com", parts.path.rstrip("/") + "/", "", ""))


def source_id_for_url(value):
    return hashlib.sha256(canonical_url(value).encode("utf-8")).hexdigest()[:32]


def read_csv(path, required=()):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        missing = set(required) - set(fields)
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        if len(set(fields)) != len(fields):
            raise ValueError(f"{path}: duplicate column names")
        return fields, list(reader)


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
