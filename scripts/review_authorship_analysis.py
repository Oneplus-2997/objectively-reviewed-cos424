"""Exploratory analysis for recent Pitchfork reviews and authorship evidence."""

from pathlib import Path
import re
from typing import Dict, Tuple
import zipfile

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split


RECENT_REVIEW_FILES = {
    "2023": "pitchfork_collection/reviews_2023.csv",
    "2024-present": "pitchfork_collection/reviews_2024_present.csv",
}
REQUIRED_COLUMNS = {"artist", "album", "reviewauthor", "score", "reviewdate", "review_url", "review"}
AUDIO_FEATURES = {
    "danceability",
    "energy",
    "loudness",
    "speechiness",
    "acousticness",
    "instrumentalness",
    "liveness",
    "valence",
    "tempo",
}
AI_PROVENANCE_PATTERN = re.compile(
    r"(?:genai|llm|ai[_ -]?(?:assisted|generated|written|authorship)|provenance)",
    re.IGNORECASE,
)
WORD_PATTERN = re.compile(r"\b[\w]+(?:['’][\w]+)?\b", re.UNICODE)
STYLE_CANDIDATE_TERMS = [
    "delve",
    "tapestry",
    "multifaceted",
    "moreover",
    "furthermore",
    "nuanced",
    "resonates",
    "underscores",
    "testament",
    "ultimately",
]


def load_recent_review_archive(
    archive_path: str | Path,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load single-album review texts and report archive coverage and schema."""
    archive_path = Path(archive_path)
    review_tables = []
    audit_rows = []
    all_columns = set()

    with zipfile.ZipFile(archive_path) as archive:
        archive_names = set(archive.namelist())
        missing_files = set(RECENT_REVIEW_FILES.values()).difference(archive_names)
        if missing_files:
            raise ValueError(
                f"Archive is missing expected review files: {sorted(missing_files)}"
            )

        for period, member in RECENT_REVIEW_FILES.items():
            with archive.open(member) as csv_file:
                data = pd.read_csv(csv_file)
            missing_columns = REQUIRED_COLUMNS.difference(data.columns)
            if missing_columns:
                raise ValueError(
                    f"{member} is missing required columns: {sorted(missing_columns)}"
                )

            all_columns.update(data.columns)
            multi_album = (
                data["multi_album_review"]
                .astype("string")
                .str.strip()
                .str.lower()
                .isin({"1", "true", "yes"})
                if "multi_album_review" in data
                else pd.Series(False, index=data.index)
            )
            data["reviewdate"] = pd.to_datetime(data["reviewdate"], errors="coerce")
            data["score"] = pd.to_numeric(data["score"], errors="coerce")
            data["review"] = data["review"].fillna("").astype(str).str.strip()
            data["reviewauthor"] = data["reviewauthor"].fillna("").astype(str).str.strip()

            valid = (
                data["reviewdate"].notna()
                & data["score"].notna()
                & data["review"].ne("")
                & data["reviewauthor"].ne("")
            )
            usable = ~multi_album & valid
            usable_data = data.loc[usable].copy()
            usable_data["collection_period"] = period
            review_tables.append(usable_data)

            audit_rows.append(
                {
                    "period": period,
                    "source_file": Path(member).name,
                    "archive_rows": len(data),
                    "multi_album_rows_excluded": int(multi_album.sum()),
                    "other_invalid_rows_excluded": int((~multi_album & ~valid).sum()),
                    "usable_single_album_reviews": len(usable_data),
                    "unique_authors": usable_data["reviewauthor"].nunique(),
                    "first_review_date": usable_data["reviewdate"].min(),
                    "last_review_date": usable_data["reviewdate"].max(),
                }
            )

        metadata_members = [
            "pitchfork_collection/metadata_2023.csv",
            "pitchfork_collection/metadata_2024_present.csv",
        ]
        for member in metadata_members:
            if member in archive_names:
                with archive.open(member) as csv_file:
                    all_columns.update(pd.read_csv(csv_file, nrows=0).columns)

    recent = pd.concat(review_tables, ignore_index=True)
    duplicate_urls = recent["review_url"].duplicated(keep=False)
    if duplicate_urls.any():
        examples = recent.loc[duplicate_urls, "review_url"].drop_duplicates().head(5).tolist()
        raise ValueError(
            "Multiple single-album rows share a review URL; resolve before modeling. "
            f"Examples: {examples}"
        )

    provenance_columns = sorted(
        column for column in all_columns if AI_PROVENANCE_PATTERN.search(column)
    )
    feature_columns = sorted(AUDIO_FEATURES.intersection(all_columns))
    schema_audit = pd.DataFrame(
        [
            {
                "ai_authorship_or_provenance_fields": (
                    ", ".join(provenance_columns) if provenance_columns else "None found"
                ),
                "Spotify_audio_feature_columns": (
                    ", ".join(feature_columns) if feature_columns else "None found"
                ),
                "archive_path": str(archive_path),
            }
        ]
    )
    return recent, pd.DataFrame(audit_rows), schema_audit


def _text_features(text: str) -> Tuple[int, int, float]:
    tokens = WORD_PATTERN.findall(text.lower())
    sentences = [
        sentence
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip())
        if sentence.strip()
    ]
    if text.strip() and not sentences:
        sentences = [text.strip()]
    lexical_diversity = len(set(tokens)) / len(tokens) if tokens else 0.0
    return len(tokens), len(sentences), lexical_diversity


def compare_review_period_style(
    historical: pd.DataFrame,
    recent: pd.DataFrame,
) -> pd.DataFrame:
    """Compare descriptive text properties, including a common-author cohort."""
    required = {"review", "reviewauthor", "score"}
    for label, data in (("historical", historical), ("recent", recent)):
        missing = required.difference(data.columns)
        if missing:
            raise ValueError(f"{label} data is missing columns: {sorted(missing)}")

    recent_authors = set(recent["reviewauthor"].dropna().astype(str).str.strip())
    historical_common = historical[
        historical["reviewauthor"].astype(str).str.strip().isin(recent_authors)
    ]

    def summarize(label: str, data: pd.DataFrame) -> Dict[str, object]:
        features = data["review"].fillna("").astype(str).map(_text_features)
        feature_table = pd.DataFrame(
            features.tolist(),
            columns=["word_count", "sentence_count", "lexical_diversity"],
            index=data.index,
        )
        return {
            "sample": label,
            "reviews": len(data),
            "authors": data["reviewauthor"].nunique(),
            "mean_score": pd.to_numeric(data["score"], errors="coerce").mean(),
            "median_words": feature_table["word_count"].median(),
            "median_sentences": feature_table["sentence_count"].median(),
            "median_lexical_diversity": feature_table["lexical_diversity"].median(),
        }

    return pd.DataFrame(
        [
            summarize("Historical corpus (all authors)", historical),
            summarize("Historical corpus (authors in recent sample)", historical_common),
            summarize("Recent sample (2023–present)", recent),
        ]
    )


def compare_style_signals(
    historical: pd.DataFrame,
    recent: pd.DataFrame,
) -> pd.DataFrame:
    """Compare review style measures across periods, macro-averaged by author."""
    recent_authors = set(recent["reviewauthor"].dropna().astype(str).str.strip())
    old = historical[
        historical["reviewauthor"].astype(str).str.strip().isin(recent_authors)
    ].copy()
    new = recent.copy()
    shared_authors = sorted(
        set(old["reviewauthor"].astype(str).str.strip())
        .intersection(new["reviewauthor"].astype(str).str.strip())
    )
    if not shared_authors:
        raise ValueError("No reviewers are shared by the historical and recent data.")

    def per_review(data: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for author, text in zip(data["reviewauthor"], data["review"].fillna("")):
            text = str(text)
            tokens = WORD_PATTERN.findall(text.lower())
            sentences = [
                sentence
                for sentence in re.split(r"(?<=[.!?])\s+", text.strip())
                if sentence.strip()
            ]
            sentence_lengths = [
                len(WORD_PATTERN.findall(sentence)) for sentence in sentences
            ]
            word_count = len(tokens)
            rows.append(
                {
                    "author": str(author).strip(),
                    "word_count": word_count,
                    "sentence_count": len(sentences),
                    "mean_words_per_sentence": (
                        float(np.mean(sentence_lengths)) if sentence_lengths else 0.0
                    ),
                    "lexical_diversity": (
                        len(set(tokens)) / word_count if word_count else 0.0
                    ),
                    "em_dash_count": text.count("—"),
                    "reviews_with_em_dash": int("—" in text),
                    "word_count_for_punctuation_rate": word_count,
                }
            )
        return pd.DataFrame(rows)

    old_features = per_review(old)
    new_features = per_review(new)
    measures = [
        "word_count",
        "sentence_count",
        "mean_words_per_sentence",
        "lexical_diversity",
    ]
    author_em_dash_rates = {}
    results = []
    for label, features in (
        ("Historical, shared authors", old_features),
        ("Recent, same authors", new_features),
    ):
        grouped = features.groupby("author")
        author_medians = grouped[measures].median()
        author_em_dash_rates[label] = (
            grouped["em_dash_count"].sum()
            / grouped["word_count_for_punctuation_rate"].sum().replace(0, np.nan)
            * 1000
        ).fillna(0)
        author_em_dash_review_shares = grouped["reviews_with_em_dash"].mean()
        results.append(
            {
                "period": label,
                "authors": len(author_medians),
                **author_medians.median().to_dict(),
                "median_em_dash_per_1000_words": author_em_dash_rates[label].median(),
                "median_em_dash_review_share": author_em_dash_review_shares.median(),
            }
        )
    results[1]["authors_with_higher_em_dash_rate"] = int(
        (
            author_em_dash_rates["Recent, same authors"]
            > author_em_dash_rates["Historical, shared authors"]
        ).sum()
    )
    return pd.DataFrame(results)


def compare_candidate_style_terms(
    historical: pd.DataFrame,
    recent: pd.DataFrame,
) -> pd.DataFrame:
    """Count exploratory style terms; these are not validated AI markers."""
    recent_authors = set(recent["reviewauthor"].dropna().astype(str).str.strip())
    old = historical[
        historical["reviewauthor"].astype(str).str.strip().isin(recent_authors)
    ]
    new = recent

    def term_stats(data: pd.DataFrame, term: str) -> Dict[str, float]:
        pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
        texts = data["review"].fillna("").astype(str)
        total_words = sum(len(WORD_PATTERN.findall(text.lower())) for text in texts)
        total_mentions = sum(len(pattern.findall(text)) for text in texts)
        mentioning_reviews = sum(bool(pattern.search(text)) for text in texts)
        return {
            "mentions": total_mentions,
            "reviews_mentioning": mentioning_reviews,
            "mentions_per_10000_words": (
                total_mentions / total_words * 10_000 if total_words else 0.0
            ),
        }

    rows = []
    for term in STYLE_CANDIDATE_TERMS:
        historical_stats = term_stats(old, term)
        recent_stats = term_stats(new, term)
        rows.append(
            {
                "term": term,
                "historical_mentions": historical_stats["mentions"],
                "historical_mentions_per_10000_words": historical_stats[
                    "mentions_per_10000_words"
                ],
                "recent_mentions": recent_stats["mentions"],
                "recent_mentions_per_10000_words": recent_stats[
                    "mentions_per_10000_words"
                ],
            }
        )
    return pd.DataFrame(rows)


def rank_recent_vocabulary_shifts(
    historical: pd.DataFrame,
    recent: pd.DataFrame,
    top_n: int = 12,
) -> pd.DataFrame:
    """Rank terms more prevalent in recent text, averaging equally by author."""
    recent_authors = set(recent["reviewauthor"].dropna().astype(str).str.strip())
    old = historical[
        historical["reviewauthor"].astype(str).str.strip().isin(recent_authors)
    ].copy()
    new = recent.copy()
    common_authors = sorted(
        set(old["reviewauthor"].astype(str).str.strip())
        .intersection(new["reviewauthor"].astype(str).str.strip())
    )
    if not common_authors:
        raise ValueError("No reviewers are shared by the historical and recent data.")

    old_text = old["review"].fillna("").astype(str).tolist()
    new_text = new["review"].fillna("").astype(str).tolist()
    vectorizer = CountVectorizer(
        binary=True,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=20,
        max_features=30_000,
    )
    features = vectorizer.fit_transform(old_text + new_text)
    old_features = features[: len(old_text)]
    new_features = features[len(old_text) :]
    old_author = old["reviewauthor"].astype(str).str.strip().to_numpy()
    new_author = new["reviewauthor"].astype(str).str.strip().to_numpy()

    old_document_counts = np.asarray(old_features.sum(axis=0)).ravel()
    new_document_counts = np.asarray(new_features.sum(axis=0)).ravel()
    macro_old = np.zeros(features.shape[1], dtype=float)
    macro_new = np.zeros(features.shape[1], dtype=float)
    for author in common_authors:
        old_indices = np.flatnonzero(old_author == author)
        new_indices = np.flatnonzero(new_author == author)
        macro_old += np.asarray(old_features[old_indices].mean(axis=0)).ravel()
        macro_new += np.asarray(new_features[new_indices].mean(axis=0)).ravel()
    macro_old /= len(common_authors)
    macro_new /= len(common_authors)
    eligible = (old_document_counts >= 50) & (new_document_counts >= 20)
    if not eligible.any():
        return pd.DataFrame(
            columns=[
                "term",
                "historical_author_average_prevalence",
                "recent_author_average_prevalence",
                "prevalence_difference",
                "historical_reviews_containing_term",
                "recent_reviews_containing_term",
            ]
        )

    differences = macro_new - macro_old
    candidates = np.flatnonzero(eligible)
    selected = candidates[np.argsort(differences[candidates])[-top_n:][::-1]]
    terms = vectorizer.get_feature_names_out()
    return pd.DataFrame(
        {
            "term": terms[selected],
            "historical_author_average_prevalence": macro_old[selected],
            "recent_author_average_prevalence": macro_new[selected],
            "prevalence_difference": differences[selected],
            "historical_reviews_containing_term": old_document_counts[selected],
            "recent_reviews_containing_term": new_document_counts[selected],
        }
    )


def evaluate_historical_score_transfer(
    historical: pd.DataFrame,
    recent: pd.DataFrame,
    random_state: int = 42,
    alpha: float = 10.0,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Train the original TF-IDF/Ridge score model historically and test later."""
    required = {"review", "reviewauthor", "score"}
    for label, data in (("historical", historical), ("recent", recent)):
        missing = required.difference(data.columns)
        if missing:
            raise ValueError(f"{label} data is missing columns: {sorted(missing)}")

    old = historical.dropna(subset=["review", "reviewauthor", "score"]).copy()
    new = recent.dropna(subset=["review", "reviewauthor", "score"]).copy()
    old["reviewauthor"] = old["reviewauthor"].astype(str).str.strip()
    new["reviewauthor"] = new["reviewauthor"].astype(str).str.strip()
    old["score"] = pd.to_numeric(old["score"], errors="coerce")
    new["score"] = pd.to_numeric(new["score"], errors="coerce")
    old = old.dropna(subset=["score"])
    new = new.dropna(subset=["score"])

    recent_authors = set(new["reviewauthor"])
    old = old[old["reviewauthor"].isin(recent_authors)].copy()
    common_authors = sorted(set(old["reviewauthor"]).intersection(new["reviewauthor"]))
    new = new[new["reviewauthor"].isin(common_authors)].copy()
    if len(old) < 10 or len(new) < 2:
        raise ValueError("Not enough historical and recent reviews from shared authors.")

    train_indices, test_indices = train_test_split(
        np.arange(len(old)),
        test_size=0.2,
        random_state=random_state,
    )
    train = old.iloc[train_indices]
    historical_test = old.iloc[test_indices]
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=5,
        max_features=30_000,
        sublinear_tf=True,
    )
    train_x = vectorizer.fit_transform(train["review"].astype(str))
    historical_test_x = vectorizer.transform(historical_test["review"].astype(str))
    recent_x = vectorizer.transform(new["review"].astype(str))

    train_y = train["score"].to_numpy(dtype=float)
    target_mean = float(train_y.mean())
    target_std = float(train_y.std()) or 1.0
    model = Ridge(alpha=alpha)
    model.fit(train_x, (train_y - target_mean) / target_std)

    vocabulary = set(vectorizer.vocabulary_)

    def unknown_token_rate(texts: pd.Series) -> float:
        tokens = [
            token
            for text in texts.fillna("").astype(str)
            for token in WORD_PATTERN.findall(text.lower())
        ]
        if not tokens:
            return 0.0
        return sum(token not in vocabulary for token in tokens) / len(tokens)

    result_rows = []
    for label, data, features in (
        ("Historical holdout (shared authors)", historical_test, historical_test_x),
        ("Recent reviews (same authors)", new, recent_x),
    ):
        actual = data["score"].to_numpy(dtype=float)
        predicted = model.predict(features) * target_std + target_mean
        result_rows.append(
            {
                "evaluation_sample": label,
                "reviews": len(data),
                "mean_actual_score": float(actual.mean()),
                "score_std": float(actual.std()),
                "R²": r2_score(actual, predicted),
                "model_MAE": mean_absolute_error(actual, predicted),
                "training_mean_MAE": mean_absolute_error(
                    actual, np.full(len(actual), target_mean)
                ),
                "unseen_token_rate": unknown_token_rate(data["review"]),
            }
        )

    details: Dict[str, object] = {
        "historical_training_reviews": len(train),
        "historical_shared_author_reviews": len(old),
        "recent_shared_author_reviews": len(new),
        "shared_authors": len(common_authors),
        "ridge_alpha": alpha,
        "vocabulary_size": len(vocabulary),
    }
    return pd.DataFrame(result_rows), details
