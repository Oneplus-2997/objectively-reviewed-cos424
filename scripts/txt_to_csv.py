#!/usr/bin/env python3
"""Create outputs/review_text.csv from outputs/text_reviews/*.txt.

Run with Python's standard library only:
    python3 scripts/txt_to_csv.py

Filenames supply artist and album, separated by ___. The first paragraph is
the description; the remaining paragraphs form the review. Files listed in
outputs/missing_descriptions.txt are excluded. Paths default to locations
relative to this script, regardless of the working directory.
Missing artist or album names in a filename remain empty in the CSV.
"""

import argparse
import csv
from pathlib import Path
import re
import sys


OUTPUTS = Path(__file__).resolve().parent.parent / "outputs"


def review_row(path):
    """Read one artist___album.txt file into the four CSV columns."""
    artist, separator, album = path.stem.partition("___")
    if not separator:
        raise ValueError("filename must have the format artist___album.txt")

    text = path.read_text(encoding="utf-8-sig").strip()
    paragraphs = re.split(r"\n[ \t]*\n", text, maxsplit=1)
    if len(paragraphs) != 2 or not all(part.strip() for part in paragraphs):
        raise ValueError("expected a description followed by review paragraphs")

    return {
        "artist": artist.strip(),
        "album": album.strip(),
        "description": paragraphs[0].strip(),
        "review": paragraphs[1].strip(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir", type=Path, default=OUTPUTS / "text_reviews",
        help="directory containing artist___album.txt files",
    )
    parser.add_argument(
        "--missing-descriptions", type=Path,
        default=OUTPUTS / "missing_descriptions.txt",
        help="file listing text filenames to exclude, one per line",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=OUTPUTS / "review_text.csv",
        help="destination CSV file (default: outputs/review_text.csv)",
    )
    args = parser.parse_args()
    if not args.input_dir.is_dir():
        parser.error(f"input directory does not exist: {args.input_dir}")
    try:
        excluded = {
            name.strip()
            for name in args.missing_descriptions.read_text(
                encoding="utf-8-sig"
            ).splitlines()
            if name.strip()
        }
    except (OSError, UnicodeError) as exc:
        parser.error(f"cannot read missing descriptions list: {exc}")

    files = sorted(path for path in args.input_dir.glob("*.txt") if path.is_file())
    if not files:
        parser.error("no text files found")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    written = skipped = failed = 0
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["artist", "album", "description", "review"]
        )
        writer.writeheader()
        for path in files:
            if path.name in excluded:
                skipped += 1
                continue
            try:
                row = review_row(path)
            except (OSError, UnicodeError, ValueError) as exc:
                print(f"Skipping {path.name}: {exc}", file=sys.stderr)
                failed += 1
                continue
            writer.writerow(row)
            written += 1

    print(
        f"Wrote {written} review(s) to {args.output}; "
        f"excluded {skipped} without descriptions; {failed} failed."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
