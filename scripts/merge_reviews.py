#!/usr/bin/env python3
"""Inner-join cleaned data and review text on the exact (artist, album) pair.

Run with Python's standard library only:
    python3 scripts/merge_reviews.py

Defaults are relative to this script, regardless of the working directory.
Creates outputs/full_review.csv and two files in outputs/missed_on_merge/:
data_clean_unmatched.csv and review_text_unmatched.csv. Unmatched files retain
their source columns. Duplicate keys retain all matching row combinations.
"""

import argparse
from collections import defaultdict
import csv
from pathlib import Path
import sys


OUTPUTS = Path(__file__).resolve().parent.parent / "outputs"
KEY_COLUMNS = ("artist", "album")


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if not all(column in fields for column in KEY_COLUMNS):
            raise ValueError(f"{path} must contain artist and album columns")
        return fields, list(reader)


def row_key(row):
    return tuple(row[column] for column in KEY_COLUMNS)


def merge_reviews(data_path, text_path, output_dir):
    data_fields, data_rows = read_csv(data_path)
    text_fields, text_rows = read_csv(text_path)
    overlap = (set(data_fields) & set(text_fields)) - set(KEY_COLUMNS)
    if overlap:
        raise ValueError(f"non-key columns appear in both inputs: {sorted(overlap)}")
    text_extra_fields = [field for field in text_fields if field not in KEY_COLUMNS]

    text_by_key = defaultdict(list)
    for row in text_rows:
        text_by_key[row_key(row)].append(row)

    missed_dir = output_dir / "missed_on_merge"
    missed_dir.mkdir(parents=True, exist_ok=True)
    merged_count = data_unmatched = text_unmatched = 0
    matched_keys = set()
    with (
        (output_dir / "full_review.csv").open(
            "w", encoding="utf-8", newline=""
        ) as merged_handle,
        (missed_dir / "data_clean_unmatched.csv").open(
            "w", encoding="utf-8", newline=""
        ) as data_handle,
        (missed_dir / "review_text_unmatched.csv").open(
            "w", encoding="utf-8", newline=""
        ) as text_handle,
    ):
        merged_writer = csv.DictWriter(
            merged_handle, fieldnames=data_fields + text_extra_fields
        )
        data_writer = csv.DictWriter(data_handle, fieldnames=data_fields)
        text_writer = csv.DictWriter(text_handle, fieldnames=text_fields)
        for writer in (merged_writer, data_writer, text_writer):
            writer.writeheader()

        for data_row in data_rows:
            key = row_key(data_row)
            matches = text_by_key.get(key, [])
            if not matches:
                data_writer.writerow(data_row)
                data_unmatched += 1
                continue
            matched_keys.add(key)
            for text_row in matches:
                merged_writer.writerow({
                    **data_row,
                    **{field: text_row[field] for field in text_extra_fields},
                })
                merged_count += 1

        for text_row in text_rows:
            if row_key(text_row) not in matched_keys:
                text_writer.writerow(text_row)
                text_unmatched += 1

    return merged_count, data_unmatched, text_unmatched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-clean", type=Path, default=OUTPUTS / "data_clean.csv"
    )
    parser.add_argument(
        "--review-text", type=Path, default=OUTPUTS / "review_text.csv"
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUTS)
    args = parser.parse_args()
    try:
        merged, data_unmatched, text_unmatched = merge_reviews(
            args.data_clean, args.review_text, args.output_dir
        )
    except (OSError, UnicodeError, ValueError, csv.Error) as exc:
        parser.error(str(exc))
    print(f"Wrote {merged} merged row(s) to {args.output_dir / 'full_review.csv'}")
    print(
        f"Unmatched rows: {data_unmatched} from data_clean.csv; "
        f"{text_unmatched} from review_text.csv. "
        f"Saved to {args.output_dir / 'missed_on_merge'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
