"""Tests for the recent-review authorship and time-period analysis."""

import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from review_authorship_analysis import (
    compare_candidate_style_terms,
    compare_review_period_style,
    compare_style_signals,
    evaluate_historical_score_transfer,
    load_recent_review_archive,
)


def review_row(index, *, multi_album="0", author="Writer A", date="2023-01-01"):
    return {
        "artist": f"Artist {index}",
        "album": f"Album {index}",
        "reviewauthor": author,
        "score": 7.0 + (index % 3) / 10,
        "reviewdate": date,
        "review_url": f"https://example.test/review/{index}",
        "review": f"Common words describe album {index} with a distinct sound.",
        "multi_album_review": multi_album,
    }


class RecentReviewAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive_path = Path(self.temp.name) / "reviews.zip"

    def write_archive(self, reviews_2023, reviews_2024):
        with zipfile.ZipFile(self.archive_path, "w") as archive:
            for name, rows in (
                ("reviews_2023.csv", reviews_2023),
                ("reviews_2024_present.csv", reviews_2024),
            ):
                csv_data = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
                archive.writestr(f"pitchfork_collection/{name}", csv_data)

    def test_archive_loader_excludes_multi_album_rows(self):
        self.write_archive(
            [review_row(1), review_row(2, multi_album="1")],
            [review_row(3, author="Writer B", date="2024-01-01")],
        )
        reviews, audit, schema = load_recent_review_archive(self.archive_path)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(int(audit["multi_album_rows_excluded"].sum()), 1)
        self.assertEqual(schema.iloc[0]["ai_authorship_or_provenance_fields"], "None found")

    def test_style_summary_has_historical_and_recent_cohorts(self):
        historical = pd.DataFrame(
            [review_row(i, date="2018-01-01") for i in range(10)]
        )
        recent = pd.DataFrame(
            [review_row(i + 20, date="2024-01-01") for i in range(4)]
        )
        summary = compare_review_period_style(historical, recent)
        self.assertEqual(len(summary), 3)
        self.assertEqual(summary.iloc[-1]["sample"], "Recent sample (2023–present)")
        self.assertEqual(summary.iloc[-1]["reviews"], 4)

    def test_style_signals_and_candidate_terms_are_descriptive(self):
        historical = pd.DataFrame(
            [review_row(i, date="2018-01-01") for i in range(10)]
        )
        recent = pd.DataFrame(
            [
                {
                    **review_row(i + 20, date="2024-01-01"),
                    "review": f"An uncanny tapestry — review number {i}.",
                }
                for i in range(4)
            ]
        )
        signals = compare_style_signals(historical, recent)
        candidate_terms = compare_candidate_style_terms(historical, recent)
        self.assertEqual(len(signals), 2)
        self.assertIn("median_em_dash_per_1000_words", signals.columns)
        self.assertEqual(
            int(candidate_terms.loc[candidate_terms["term"] == "tapestry", "recent_mentions"].iloc[0]),
            4,
        )

    def test_historical_model_evaluates_on_both_periods(self):
        historical_rows = [
            review_row(i, author=f"Writer {i % 2}", date="2018-01-01")
            for i in range(80)
        ]
        recent_rows = [
            review_row(100 + i, author=f"Writer {i % 2}", date="2024-01-01")
            for i in range(10)
        ]
        metrics, details = evaluate_historical_score_transfer(
            pd.DataFrame(historical_rows), pd.DataFrame(recent_rows)
        )
        self.assertEqual(len(metrics), 2)
        self.assertEqual(details["shared_authors"], 2)
        self.assertEqual(metrics.iloc[1]["evaluation_sample"], "Recent reviews (same authors)")


if __name__ == "__main__":
    unittest.main()
