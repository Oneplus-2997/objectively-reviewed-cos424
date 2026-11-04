#!/usr/bin/env python3
"""Extract Pitchfork reviews into artist___album.txt UTF-8 text files.

The meta description is included as the first paragraph when present.

Run without arguments to convert raw-html, or pass individual files/directories:
    python3 scripts/html_to_txt.py
    python3 scripts/html_to_txt.py path/to/review.html

Paths used by default are relative to this script, regardless of the working
directory. Only Python's standard library is required.
Skipped file paths and error reasons are saved to outputs/skipped_htmls.txt,
replacing the previous run's list (empty when no files were skipped).
Successful HTML/text filename pairs are saved to outputs/html_text_match.csv,
replacing the previous run's mapping.
Text filenames for reviews with no nonempty meta description are saved to
outputs/missing_descriptions.txt, replacing the previous run's list.
"""

import argparse
import csv
from html.parser import HTMLParser
from pathlib import Path
import re
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR.parent / "pitchfork-scrape" / "raw-html"
DEFAULT_OUTPUT = SCRIPT_DIR.parent / "outputs" / "text_reviews"
SKIPPED_LOG = SCRIPT_DIR.parent / "outputs" / "skipped_htmls.txt"
MATCH_CSV = SCRIPT_DIR.parent / "outputs" / "html_text_match.csv"
MISSING_DESCRIPTION_LOG = SCRIPT_DIR.parent / "outputs" / "missing_descriptions.txt"


class ReviewParser(HTMLParser):
    """Read the title, meta description, and Pitchfork review body paragraphs."""

    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
    IGNORED_TAGS = {"script", "style", "noscript", "iframe", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.title_parts = []
        self.description = ""
        self.paragraphs = []
        self.paragraph_parts = None
        self.review_depth = None

    def finish_paragraph(self):
        if self.paragraph_parts is not None:
            # Joining without added spaces preserves punctuation around inline
            # links/emphasis. Split/join normalizes whitespace, including &nbsp;.
            paragraph = " ".join("".join(self.paragraph_parts).split())
            if paragraph:
                self.paragraphs.append(paragraph)
            self.paragraph_parts = None

    def handle_starttag(self, tag, attrs):
        ignored = any(name in self.IGNORED_TAGS for name in self.stack)
        attrs = dict(attrs)
        if not ignored and tag == "meta" and not self.description:
            if (attrs.get("name") or "").lower() == "description":
                self.description = " ".join((attrs.get("content") or "").split())
        if tag == "br" and self.paragraph_parts is not None and not ignored:
            self.paragraph_parts.append(" ")
        if tag in self.VOID_TAGS:
            return
        self.stack.append(tag)
        classes = (attrs.get("class") or "").split()
        if not ignored and tag == "div" and "contents" in classes:
            if self.review_depth is None:
                self.review_depth = len(self.stack)
        if self.review_depth is not None and tag == "p" and not ignored:
            self.finish_paragraph()
            self.paragraph_parts = []

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        # Match the nearest open tag; void elements never enter the stack.
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        ignored = any(name in self.IGNORED_TAGS for name in self.stack)
        if tag == "p" and not ignored:
            self.finish_paragraph()
        if self.review_depth is not None and index < self.review_depth:
            self.finish_paragraph()
            self.review_depth = None
        del self.stack[index:]

    def handle_data(self, data):
        if any(tag in self.IGNORED_TAGS for tag in self.stack):
            return
        if "title" in self.stack:
            self.title_parts.append(data)
        if self.paragraph_parts is not None:
            self.paragraph_parts.append(data)


def extract_review(html):
    """Return the title, combined review text, and normalized meta description."""
    parser = ReviewParser()
    parser.feed(html)
    parser.close()
    parser.finish_paragraph()
    title = " ".join("".join(parser.title_parts).split())
    if not title:
        raise ValueError("no nonempty <title> tag found")
    if not parser.paragraphs:
        raise ValueError("no review paragraphs found in a div.contents container")
    paragraphs = ([parser.description] if parser.description else []) + parser.paragraphs
    return title, "\n\n".join(paragraphs) + "\n", parser.description


def output_name(title, number=1):
    title = re.sub(r"\s+Album Review\s*\|\s*Pitchfork\s*$", "", title)
    artist, separator, album = title.partition(":")
    stem = f"{artist.strip()}___{album.strip()}" if separator else title.strip()
    # Replace path separators and characters forbidden on common filesystems.
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    stem = stem or "review"
    suffix = "" if number == 1 else f" ({number})"
    # Most filesystems limit a filename component to 255 bytes, not characters.
    budget = 255 - len((suffix + ".txt").encode("utf-8"))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return stem.rstrip(" .") + suffix + ".txt"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", nargs="*", type=Path,
        help="HTML files or directories (default: pitchfork-scrape/raw-html)",
    )
    parser.add_argument(
        "-o", "--output-dir", type=Path, default=DEFAULT_OUTPUT,
        help="output directory (default: ../outputs/text_reviews beside script)",
    )
    args = parser.parse_args()
    files = set()
    for source in args.inputs or [DEFAULT_INPUT]:
        if source.is_dir():
            files.update(
                path.resolve() for path in source.rglob("*")
                if path.is_file() and path.suffix.lower() in {".html", ".htm"}
            )
        elif source.is_file():
            files.add(source.resolve())
        else:
            parser.error(f"input does not exist: {source}")
    if not files:
        parser.error("no HTML files found")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    SKIPPED_LOG.parent.mkdir(parents=True, exist_ok=True)
    skipped = []
    missing_descriptions = []
    matches = []
    used_names = set()
    converted = failed = 0
    for source in sorted(files):
        try:
            title, review, description = extract_review(
                source.read_text(encoding="utf-8-sig")
            )
            number = 1
            name = output_name(title, number)
            # Distinct pages can have identical titles or sanitize to the same
            # filename. Keep both instead of overwriting within this batch.
            while name.casefold() in used_names:
                number += 1
                name = output_name(title, number)
            (args.output_dir / name).write_text(review, encoding="utf-8")
            if not description:
                missing_descriptions.append(name)
            used_names.add(name.casefold())
            matches.append((source.name, name))
            converted += 1
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"Skipping {source.name}: {exc}", file=sys.stderr)
            skipped.append(f"{source}\t{exc}\n")
            failed += 1
    SKIPPED_LOG.write_text("".join(skipped), encoding="utf-8")
    MISSING_DESCRIPTION_LOG.write_text(
        "".join(f"{name}\n" for name in missing_descriptions), encoding="utf-8"
    )
    with MATCH_CSV.open("w", encoding="utf-8", newline="") as mapping:
        writer = csv.writer(mapping)
        writer.writerow(["html_file_name", "text_file_name"])
        writer.writerows(matches)
    print(f"Converted {converted} file(s) to {args.output_dir}; {failed} failed.")
    print(f"Filename mapping saved to {MATCH_CSV}")
    print(
        f"{len(missing_descriptions)} file(s) with review text but no description; "
        f"text filenames saved to {MISSING_DESCRIPTION_LOG}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
