#!/usr/bin/env python3
"""Extract Pitchfork reviews into source_id.txt files and a metadata manifest.

    python3 scripts/html_to_txt.py

Names come from the first artist link and album heading, retaining punctuation.
The first text paragraph is the meta description, followed by the review body.
Stable IDs are derived from canonical URLs. All members of duplicate source-ID
or artist/album/date groups are discarded and recorded in discarded_html.csv.
Files without descriptions remain text files and appear in missing_descriptions.txt.
The manifest and support reports are saved in outputs/extracting_text/.
The previous text directory is archived when replacing it. Default paths are
relative to this script, and only Python's standard library is required.
"""

import argparse
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile

from review_common import (
    EXTRACTION_OUTPUTS, MAP_FIELDS, OUTPUTS, canonical_url, duplicate_keys, match_key,
    review_date, review_score, source_id_for_url, write_csv,
)


DEFAULT_INPUT = OUTPUTS.parent / "pitchfork-scrape" / "raw-html"


class ReviewParser(HTMLParser):
    """Read visible identity fields, JSON-LD, description, and review paragraphs."""

    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
    IGNORED_TAGS = {"script", "style", "noscript", "iframe", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.values = {}
        self.captures = {}
        self.description = ""
        self.url = ""
        self.og_url = ""
        self.json_blocks = []
        self.json_parts = None
        self.json_depth = None
        self.artist_list_depth = None
        self.paragraphs = []
        self.paragraph_parts = None
        self.review_depth = None

    def capture(self, name):
        if name not in self.values and name not in self.captures:
            self.captures[name] = (len(self.stack), [])

    def finish_paragraph(self):
        if self.paragraph_parts is not None:
            paragraph = " ".join("".join(self.paragraph_parts).split())
            if paragraph:
                self.paragraphs.append(paragraph)
            self.paragraph_parts = None

    def handle_starttag(self, tag, attrs):
        ignored = any(name in self.IGNORED_TAGS for name in self.stack)
        attrs = dict(attrs)
        if not ignored:
            if tag == "meta":
                name = (attrs.get("name") or attrs.get("property") or "").lower()
                if name == "description" and not self.description:
                    self.description = " ".join((attrs.get("content") or "").split())
                elif name == "og:url":
                    self.og_url = attrs.get("content") or ""
            if tag == "link" and "canonical" in (attrs.get("rel") or "").split():
                self.url = attrs.get("href") or ""
        if tag == "br" and not ignored:
            if self.paragraph_parts is not None:
                self.paragraph_parts.append(" ")
            for _, parts in self.captures.values():
                parts.append(" ")
        if tag in self.VOID_TAGS:
            return
        self.stack.append(tag)
        if ignored:
            return
        classes = (attrs.get("class") or "").split()
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.json_parts = []
            self.json_depth = len(self.stack)
        if tag == "ul" and "artist-links" in classes and "artist" not in self.values:
            self.artist_list_depth = len(self.stack)
        if tag == "li" and self.artist_list_depth is not None:
            self.capture("artist")
        if tag == "h1" and "single-album-tombstone__review-title" in classes:
            self.capture("album")
        if tag == "span" and "score" in classes:
            self.capture("score")
        if tag == "div" and "contents" in classes and self.review_depth is None:
            self.review_depth = len(self.stack)
        if tag == "p" and self.review_depth is not None:
            self.finish_paragraph()
            self.paragraph_parts = []

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        ignored = any(name in self.IGNORED_TAGS for name in self.stack)
        for name, (depth, parts) in list(self.captures.items()):
            if index < depth:
                value = " ".join("".join(parts).split())
                # An empty first heading must not select a later album's name.
                self.values[name] = value
                del self.captures[name]
        if self.json_depth is not None and index < self.json_depth:
            self.json_blocks.append("".join(self.json_parts))
            self.json_parts = self.json_depth = None
        if self.artist_list_depth is not None and index < self.artist_list_depth:
            self.artist_list_depth = None
        if tag == "p" and not ignored:
            self.finish_paragraph()
        if self.review_depth is not None and index < self.review_depth:
            self.finish_paragraph()
            self.review_depth = None
        del self.stack[index:]

    def handle_data(self, data):
        if self.json_parts is not None:
            self.json_parts.append(data)
            return
        if any(tag in self.IGNORED_TAGS for tag in self.stack):
            return
        for _, parts in self.captures.values():
            parts.append(data)
        if self.paragraph_parts is not None:
            self.paragraph_parts.append(data)


def json_objects(value):
    """Support plain JSON-LD objects, lists, and @graph documents."""
    if isinstance(value, list):
        for item in value:
            yield from json_objects(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from json_objects(value["@graph"])


def extract_review(html):
    parser = ReviewParser()
    parser.feed(html)
    parser.close()
    parser.finish_paragraph()
    if not parser.paragraphs:
        raise ValueError("no review paragraphs found in a div.contents container")
    for field in ("artist", "album", "score"):
        if not parser.values.get(field):
            raise ValueError(f"no nonempty {field} field found in the review heading")
    article = None
    for block in parser.json_blocks:
        try:
            candidates = json_objects(json.loads(block))
            article = next((obj for obj in candidates if obj.get("datePublished")), None)
        except json.JSONDecodeError:
            continue
        if article:
            break
    if not article:
        raise ValueError("no publication metadata found in JSON-LD")
    author = article.get("author") or article.get("creator") or {}
    if isinstance(author, list):
        author = author[0] if len(author) == 1 else {}
    author = author.get("name", "") if isinstance(author, dict) else author
    author = " ".join((author or "").split())
    if not author:
        raise ValueError("no review author found")
    url = canonical_url(parser.url or parser.og_url or article.get("url"))
    return {
        "source_id": source_id_for_url(url),
        "artist": parser.values["artist"],
        "album": parser.values["album"],
        "reviewdate": review_date(article.get("datePublished")),
        "reviewauthor": author,
        "score": str(review_score(parser.values["score"])),
        "description": parser.description,
        "review": "\n\n".join(parser.paragraphs) + "\n",
    }


def write_outputs(records, skipped, output_dir, reports_dir):
    """Discard entire duplicate groups and replace the text directory together."""
    duplicate_ids = {
        value for value, count in Counter(row["source_id"] for row in records).items()
        if count > 1
    }
    duplicated_keys = duplicate_keys(records)
    kept, discarded = [], []
    for record in records:
        if record["source_id"] in duplicate_ids or match_key(record) in duplicated_keys:
            discarded.append({
                **{field: record[field] for field in MAP_FIELDS},
                "discard_reason": "duplicate_html_review",
            })
        else:
            kept.append(record)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    with tempfile.TemporaryDirectory(prefix=".review_stage_", dir=output_dir.parent) as tmp:
        stage = Path(tmp) / "text_reviews"
        stage.mkdir()
        for record in kept:
            text = record["review"]
            if record["description"]:
                text = record["description"] + "\n\n" + text
            (stage / record["text_file_name"]).write_text(text, encoding="utf-8")
        if output_dir.exists():
            backup = Path(tempfile.mkdtemp(prefix="previous_text_reviews_", dir=output_dir.parent))
            output_dir.rename(backup / "text_reviews")
        try:
            stage.rename(output_dir)
        except OSError:
            if backup is not None:
                (backup / "text_reviews").rename(output_dir)
            raise

    write_csv(
        reports_dir / "html_text_match.csv", MAP_FIELDS,
        [{field: row[field] for field in MAP_FIELDS} for row in kept],
    )
    write_csv(reports_dir / "discarded_html.csv", MAP_FIELDS + ["discard_reason"], discarded)
    (reports_dir / "missing_descriptions.txt").write_text(
        "".join(row["text_file_name"] + "\n" for row in kept if not row["description"]),
        encoding="utf-8",
    )
    (reports_dir / "skipped_htmls.txt").write_text("".join(skipped), encoding="utf-8")
    return len(kept), len(discarded), backup


def find_html_files(inputs):
    """Return unique, sorted source paths from HTML files or directories."""
    files = set()
    for source in map(Path, inputs):
        if source.is_dir():
            files.update(path.resolve() for path in source.rglob("*")
                         if path.is_file() and path.suffix.lower() in {".html", ".htm"})
        elif source.is_file():
            files.add(source.resolve())
        else:
            raise FileNotFoundError(f"input does not exist: {source}")
    if not files:
        raise ValueError("no HTML files found")
    return sorted(files)


def parse_html_files(files, progress_every=2000):
    """Extract records and collect failures without replacing existing outputs."""
    records, skipped = [], []
    for index, source in enumerate(files, 1):
        try:
            record = extract_review(source.read_text(encoding="utf-8-sig"))
            records.append({
                **record, "html_file_name": source.name,
                "text_file_name": record["source_id"] + ".txt",
            })
        except (OSError, UnicodeError, ValueError) as exc:
            skipped.append(f"{source}\t{exc}\n")
            print(f"Skipping {source.name}: {exc}", file=sys.stderr)
        if progress_every and index % progress_every == 0:
            print(f"Parsed {index}/{len(files)} HTML files.", flush=True)
    if not records:
        raise ValueError("no reviews could be extracted; previous outputs were preserved")
    return records, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, default=OUTPUTS / "text_reviews")
    parser.add_argument("--reports-dir", type=Path, default=EXTRACTION_OUTPUTS)
    args = parser.parse_args()
    try:
        files = find_html_files(args.inputs or [DEFAULT_INPUT])
        records, skipped = parse_html_files(files)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    converted, discarded, backup = write_outputs(
        records, skipped, args.output_dir, args.reports_dir
    )
    print(f"Wrote {converted} text files; discarded {discarded} duplicate HTMLs; skipped {len(skipped)}.")
    if backup:
        print(f"Previous text files archived in {backup}")
    print(f"Metadata manifest and exclusion reports saved to {args.reports_dir}")
    # Invalid source HTMLs are recorded; downstream stages can process valid ones.
    return 0


if __name__ == "__main__":
    sys.exit(main())
