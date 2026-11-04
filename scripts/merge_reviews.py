#!/usr/bin/env python3
"""Link reviews using normalized artist, album, and publication date.

    python3 scripts/merge_reviews.py

Accept only a unique candidate with matching author and numeric score. Output
full_review.csv retains cleaned metadata and adds source_id, description, and
review. Unresolved rows, with reasons, go to missed_on_merge/. Requested
exclusions and every member of repeated metadata key/date groups go to
discarded_on_merge/. Raw inputs are preserved. Only the standard library is
required; default paths are relative to this script.
"""

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys

from review_common import (
    EXTRACTION_OUTPUTS, MATCH_FIELDS, OUTPUTS, REQUESTED_EXCLUSIONS, REVIEW_FIELDS,
    duplicate_keys, match_key, normalize_name, read_csv, review_score, write_csv,
)


def consistency_issues(data_row, text_row):
    issues = []
    data_author = normalize_name(data_row.get("reviewauthor"))
    text_author = normalize_name(text_row.get("reviewauthor"))
    if not data_author or not text_author:
        issues.append("missing_author")
    elif data_author != text_author:
        issues.append("author_mismatch")
    try:
        if review_score(data_row.get("score")) != review_score(text_row.get("score")):
            issues.append("score_mismatch")
    except ValueError:
        issues.append("invalid_score")
    return ";".join(issues)


def merge_reviews(data_path, text_path, output_dir, discarded_html_path=None):
    data_fields, data_rows = read_csv(data_path, MATCH_FIELDS)
    text_fields, text_rows = read_csv(text_path, REVIEW_FIELDS)
    if {"source_id", "description", "review"} & set(data_fields):
        raise ValueError("cleaned metadata already contains output-only review columns")
    repeated_data_keys = duplicate_keys(data_rows)
    discarded_html_keys = set()
    if discarded_html_path is not None:
        _, html_rows = read_csv(discarded_html_path, MATCH_FIELDS)
        discarded_html_keys = {match_key(row) for row in html_rows}

    data_discarded, text_discarded = [], []
    data_unmatched, text_unmatched = [], []
    eligible_data, eligible_text = [], []
    id_counts = Counter(row["source_id"] for row in text_rows)
    for rows, eligible, discarded, unmatched, is_text in (
        (data_rows, eligible_data, data_discarded, data_unmatched, False),
        (text_rows, eligible_text, text_discarded, text_unmatched, True),
    ):
        for row in rows:
            try:
                key = match_key(row)
            except ValueError:
                unmatched.append({**row, "merge_reason": "invalid_match_fields"})
                continue
            reason = None
            if key in repeated_data_keys:
                reason = "duplicate_metadata_key"
            elif key in REQUESTED_EXCLUSIONS:
                reason = "requested_exclusion_no_source_review"
            elif key in discarded_html_keys:
                reason = "duplicate_html_review"
            elif is_text and id_counts[row["source_id"]] > 1:
                reason = "duplicate_source_id"
            if reason:
                discarded.append({**row, "discard_reason": reason})
            else:
                eligible.append((key, row))

    text_by_key = defaultdict(list)
    for key, row in eligible_text:
        text_by_key[key].append(row)
    text_pairs = {key[:2] for key, _ in eligible_text}
    data_pairs = {key[:2] for key, _ in eligible_data}
    merged = []
    matched_ids = set()
    failed_keys = {}
    for key, data_row in eligible_data:
        candidates = text_by_key.get(key, [])
        if not candidates:
            reason = "review_date_mismatch" if key[:2] in text_pairs else "no_matching_review"
        elif len(candidates) != 1:
            reason = "ambiguous_review_match"
        else:
            text_row = candidates[0]
            reason = consistency_issues(data_row, text_row)
            if not reason:
                source_id = text_row["source_id"]
                if source_id in matched_ids:
                    raise ValueError(f"source ID would be linked more than once: {source_id}")
                matched_ids.add(source_id)
                merged.append({
                    **data_row, "source_id": source_id,
                    "description": text_row["description"], "review": text_row["review"],
                })
                continue
        failed_keys[key] = reason
        data_unmatched.append({**data_row, "merge_reason": reason})

    for key, text_row in eligible_text:
        if text_row["source_id"] in matched_ids:
            continue
        reason = failed_keys.get(key)
        if not reason:
            reason = "review_date_mismatch" if key[:2] in data_pairs else "no_matching_metadata"
        text_unmatched.append({**text_row, "merge_reason": reason})

    write_csv(output_dir / "full_review.csv", data_fields + ["source_id", "description", "review"], merged)
    for filename, fields, rows in (
        ("data_clean_unmatched.csv", data_fields, data_unmatched),
        ("review_text_unmatched.csv", text_fields, text_unmatched),
    ):
        write_csv(output_dir / "missed_on_merge" / filename, fields + ["merge_reason"], rows)
    for filename, fields, rows in (
        ("data_clean_discarded.csv", data_fields, data_discarded),
        ("review_text_discarded.csv", text_fields, text_discarded),
    ):
        write_csv(output_dir / "discarded_on_merge" / filename, fields + ["discard_reason"], rows)

    assert len(data_rows) == len(merged) + len(data_unmatched) + len(data_discarded)
    assert len(text_rows) == len(merged) + len(text_unmatched) + len(text_discarded)
    return {
        "merged": len(merged),
        "data_unmatched": len(data_unmatched), "text_unmatched": len(text_unmatched),
        "data_discarded": len(data_discarded), "text_discarded": len(text_discarded),
        "duplicate_metadata_groups": len(repeated_data_keys),
        "data_unmatched_reasons": dict(Counter(row["merge_reason"] for row in data_unmatched)),
        "data_discard_reasons": dict(Counter(row["discard_reason"] for row in data_discarded)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-clean", type=Path, default=OUTPUTS / "data_clean.csv")
    parser.add_argument("--review-text", type=Path, default=OUTPUTS / "review_text.csv")
    parser.add_argument("--discarded-html", type=Path, default=EXTRACTION_OUTPUTS / "discarded_html.csv")
    parser.add_argument("--output-dir", type=Path, default=OUTPUTS)
    args = parser.parse_args()
    try:
        stats = merge_reviews(args.data_clean, args.review_text, args.output_dir, args.discarded_html)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Wrote {stats['merged']} verified reviews to {args.output_dir / 'full_review.csv'}")
    print(f"Unresolved: {stats['data_unmatched']} metadata rows, {stats['text_unmatched']} text rows.")
    print(f"Discarded: {stats['data_discarded']} metadata rows, {stats['text_discarded']} text rows.")
    print(f"Duplicate metadata groups discarded: {stats['duplicate_metadata_groups']}")
    print(f"Unresolved reasons: {stats['data_unmatched_reasons']}")
    print(f"Discard reasons: {stats['data_discard_reasons']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
