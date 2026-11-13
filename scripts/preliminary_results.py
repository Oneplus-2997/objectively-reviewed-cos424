"""Reusable TF-IDF regression helpers for Pitchfork reviews."""

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split


AUDIO_FEATURES = [
    "danceability",
    "energy",
    "loudness",
    "speechiness",
    "acousticness",
    "instrumentalness",
    "liveness",
    "valence",
    "tempo",
]
TARGET_COLUMNS = ["score", *AUDIO_FEATURES]
REQUIRED_COLUMNS = {"review_id", "review", *TARGET_COLUMNS}


@dataclass(frozen=True)
class TfidfSplit:
    """Train/test TF-IDF data shared by the separate regression models."""

    train_x: csr_matrix
    test_x: csr_matrix
    train_y_scaled: NDArray[np.float64]
    train_y: NDArray[np.float64]
    test_y: NDArray[np.float64]
    train_reviews: pd.Series
    test_reviews: pd.Series
    train_genres: pd.Series
    test_genres: pd.Series
    target_mean: NDArray[np.float64]
    target_std: NDArray[np.float64]
    feature_names: NDArray
    run_info: Dict[str, int]


def prepare_tfidf_split(
    data: pd.DataFrame,
    test_size: float = 0.2,
    random_state: int = 42,
    max_features: int = 30_000,
    min_df: int = 5,
) -> TfidfSplit:
    """Create one reproducible split and training-only TF-IDF representation."""
    missing_columns = REQUIRED_COLUMNS.difference(data.columns)
    if missing_columns:
        raise ValueError(f"Merged data is missing required columns: {sorted(missing_columns)}")
    if not 0 < test_size < 1:
        raise ValueError("test_size must be between zero and one")

    complete = data.dropna(subset=["review", *TARGET_COLUMNS]).copy()
    complete["review"] = complete["review"].astype(str)
    if len(complete) < 2:
        raise ValueError("At least two complete review/target rows are required")

    train_idx, test_idx = train_test_split(
        np.arange(len(complete)),
        test_size=test_size,
        random_state=random_state,
    )
    train = complete.iloc[train_idx]
    test = complete.iloc[test_idx]

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=min_df,
        max_features=max_features,
        sublinear_tf=True,
    )
    train_x = vectorizer.fit_transform(train["review"])
    test_x = vectorizer.transform(test["review"])

    train_y = train[TARGET_COLUMNS].to_numpy(dtype=float)
    test_y = test[TARGET_COLUMNS].to_numpy(dtype=float)
    target_mean = train_y.mean(axis=0)
    target_std = train_y.std(axis=0)
    target_std[target_std == 0] = 1.0

    return TfidfSplit(
        train_x=train_x,
        test_x=test_x,
        train_y_scaled=(train_y - target_mean) / target_std,
        train_y=train_y,
        test_y=test_y,
        train_reviews=train["review"],
        test_reviews=test["review"],
        train_genres=train.get("genre", pd.Series(pd.NA, index=train.index)),
        test_genres=test.get("genre", pd.Series(pd.NA, index=test.index)),
        target_mean=target_mean,
        target_std=target_std,
        feature_names=vectorizer.get_feature_names_out(),
        run_info={
            "complete_rows": len(complete),
            "train_rows": len(train),
            "test_rows": len(test),
            "vocabulary_size": len(vectorizer.get_feature_names_out()),
        },
    )


def _evaluate_model(
    split: TfidfSplit,
    model: Ridge | ElasticNet,
    model_name: str,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fit one regression model and report test metrics and term coefficients."""
    model.fit(split.train_x, split.train_y_scaled)
    predictions = model.predict(split.test_x) * split.target_std + split.target_mean
    mean_predictions = np.broadcast_to(split.target_mean, split.test_y.shape)
    coefficients = np.atleast_2d(model.coef_)

    metrics = []
    top_terms = []
    coefficient_counts = []
    for index, target in enumerate(TARGET_COLUMNS):
        target_predictions = predictions[:, index]
        y_true = split.test_y[:, index]
        metrics.append(
            {
                "target": target,
                "model": model_name,
                "r2": r2_score(y_true, target_predictions),
                "mae": mean_absolute_error(y_true, target_predictions),
                "rmse": float(np.sqrt(mean_squared_error(y_true, target_predictions))),
            }
        )
        metrics.append(
            {
                "target": target,
                "model": "Training-mean baseline",
                "r2": r2_score(y_true, mean_predictions[:, index]),
                "mae": mean_absolute_error(y_true, mean_predictions[:, index]),
                "rmse": float(
                    np.sqrt(mean_squared_error(y_true, mean_predictions[:, index]))
                ),
            }
        )

        weights = coefficients[index]
        nonzero_indices = np.flatnonzero(weights)
        coefficient_counts.append(
            {
                "target": target,
                "model": model_name,
                "nonzero_terms": int(len(nonzero_indices)),
            }
        )
        if not len(nonzero_indices):
            continue

        term_orders = (
            (
                "positive",
                nonzero_indices[np.argsort(weights[nonzero_indices])[-10:][::-1]],
            ),
            (
                "negative",
                nonzero_indices[np.argsort(weights[nonzero_indices])[:10]],
            ),
        )
        for direction, term_indices in term_orders:
            for term_index in term_indices:
                top_terms.append(
                    {
                        "target": target,
                        "model": model_name,
                        "direction": direction,
                        "term": split.feature_names[term_index],
                        "coefficient": float(weights[term_index]),
                    }
                )

    return (
        pd.DataFrame(metrics),
        pd.DataFrame(top_terms),
        pd.DataFrame(coefficient_counts),
    )


def fit_tfidf_baseline(
    split: TfidfSplit,
    alpha: float = 10.0,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fit the original TF-IDF + Ridge NLP baseline."""
    return _evaluate_model(split, Ridge(alpha=alpha), "TF-IDF + Ridge")


def fit_tfidf_elasticnet(
    split: TfidfSplit,
    alpha: float = 0.0003,
    l1_ratio: float = 0.5,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fit a sparse TF-IDF + ElasticNet regression as a separate model."""
    model = ElasticNet(
        alpha=alpha,
        l1_ratio=l1_ratio,
        max_iter=5000,
        tol=1e-4,
    )
    return _evaluate_model(split, model, "TF-IDF + ElasticNet")
