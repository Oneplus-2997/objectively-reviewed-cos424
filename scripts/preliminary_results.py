"""NLP baseline for predicting Pitchfork scores and Spotify audio features."""

from typing import Dict, Tuple

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
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


def fit_tfidf_baseline(
    data: pd.DataFrame,
    test_size: float = 0.2,
    random_state: int = 42,
    max_features: int = 30_000,
    min_df: int = 5,
    alpha: float = 10.0,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, int]]:
    """Fit TF-IDF + Ridge models and compare them with a training-mean baseline.

    The vectorizer and target scaling are fit using training rows only. Metrics
    are evaluated in each target's original units.
    """
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
    train_y_scaled = (train_y - target_mean) / target_std

    model = Ridge(alpha=alpha)
    model.fit(train_x, train_y_scaled)
    prediction = model.predict(test_x) * target_std + target_mean
    baseline = np.broadcast_to(target_mean, test_y.shape)

    metrics = []
    for index, target in enumerate(TARGET_COLUMNS):
        for model_name, predicted in (
            ("TF-IDF + Ridge", prediction[:, index]),
            ("Training-mean baseline", baseline[:, index]),
        ):
            metrics.append(
                {
                    "target": target,
                    "model": model_name,
                    "r2": r2_score(test_y[:, index], predicted),
                    "mae": mean_absolute_error(test_y[:, index], predicted),
                    "rmse": float(
                        np.sqrt(mean_squared_error(test_y[:, index], predicted))
                    ),
                }
            )

    feature_names = vectorizer.get_feature_names_out()
    coefficients = np.asarray(model.coef_)
    top_terms = []
    for target_index, target in enumerate(TARGET_COLUMNS):
        weights = coefficients[target_index]
        for direction, term_indices in (
            ("positive", np.argsort(weights)[-10:][::-1]),
            ("negative", np.argsort(weights)[:10]),
        ):
            for term_index in term_indices:
                top_terms.append(
                    {
                        "target": target,
                        "direction": direction,
                        "term": feature_names[term_index],
                        "coefficient": float(weights[term_index]),
                    }
                )

    run_info = {
        "complete_rows": len(complete),
        "train_rows": len(train),
        "test_rows": len(test),
        "vocabulary_size": len(feature_names),
    }
    return pd.DataFrame(metrics), pd.DataFrame(top_terms), run_info
