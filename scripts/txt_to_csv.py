#!/usr/bin/env python3
"""Create review_text.csv using source-ID text files and html_text_match.csv.

    python3 scripts/txt_to_csv.py

The CSV contains source_id, artist, album, reviewdate, reviewauthor, score,
description, and review. Original names come from the extraction manifest.
The first text paragraph is the description; the remainder is the review body.
Files listed in missing_descriptions.txt are excluded. Only Python's standard
library is required, and default paths are relative to this script.
The manifest and missing descriptions list are read from outputs/extracting_text/.
"""

import argparse
from pathlib import Path
import re
import sys

from review_common import (
    EXTRACTION_OUTPUTS, IDENTITY_FIELDS, MAP_FIELDS, OUTPUTS, REVIEW_FIELDS,
    read_csv, write_csv,
)


def review_row(path, metadata):
    text = path.read_text(encoding="utf-8-sig").strip()
    paragraphs = re.split(r"\n[ \t]*\n", text, maxsplit=1)
    if len(paragraphs) != 2 or not all(part.strip() for part in paragraphs):
        raise ValueError("expected a description followed by review paragraphs")
    return {
        **{field: metadata[field] for field in IDENTITY_FIELDS},
        "description": paragraphs[0].strip(), "review": paragraphs[1].strip(),
    }


def build_review_csv(input_dir, manifest_path, missing_descriptions_path, output_path):
    """Validate text files and write the review CSV; return rows and exclusions."""
    if not input_dir.is_dir():
        raise FileNotFoundError(f"input directory does not exist: {input_dir}")
    _, manifest = read_csv(manifest_path, MAP_FIELDS)
    excluded = set(missing_descriptions_path.read_text(encoding="utf-8-sig").splitlines())
    rows, seen = [], set()
    skipped = 0
    for metadata in manifest:
        source_id = metadata["source_id"]
        name = metadata["text_file_name"]
        if not re.fullmatch(r"[0-9a-f]{32}", source_id) or name != source_id + ".txt":
            raise ValueError(f"invalid source ID/text filename in manifest: {name}")
        if source_id in seen:
            raise ValueError(f"duplicate source ID in manifest: {source_id}")
        seen.add(source_id)
        if name in excluded:
            skipped += 1
            continue
        rows.append(review_row(input_dir / name, metadata))
    assert len(manifest) == len(rows) + skipped
    # Validate all input files before replacing the previous CSV.
    write_csv(output_path, REVIEW_FIELDS, rows)
    return rows, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=OUTPUTS / "text_reviews")
    parser.add_argument("--manifest", type=Path, default=EXTRACTION_OUTPUTS / "html_text_match.csv")
    parser.add_argument("--missing-descriptions", type=Path, default=EXTRACTION_OUTPUTS / "missing_descriptions.txt")
    parser.add_argument("-o", "--output", type=Path, default=OUTPUTS / "review_text.csv")
    args = parser.parse_args()
    try:
        rows, skipped = build_review_csv(
            args.input_dir, args.manifest, args.missing_descriptions, args.output,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Wrote {len(rows)} review(s) to {args.output}; excluded {skipped} without descriptions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
