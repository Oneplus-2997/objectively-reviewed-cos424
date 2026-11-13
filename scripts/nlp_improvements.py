"""Additional NLP model experiments, separate from the original baseline."""

from typing import Dict, Tuple

import numpy as np
import pandas as pd
from scipy.sparse import hstack
from sklearn.compose import TransformedTargetRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GridSearchCV, KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from preliminary_results import TARGET_COLUMNS, TfidfSplit


def tune_tfidf_ridge(
    split: TfidfSplit,
    cv_folds: int = 3,
    random_state: int = 42,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Tune TF-IDF and Ridge settings using CV within the training partition.

    All text vectorization and target scaling are fit inside each CV fold. The
    outer held-out test partition is used once for final metrics.
    """
    pipeline = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    sublinear_tf=True,
                    min_df=5,
                    max_features=30_000,
                ),
            ),
            ("ridge", Ridge()),
        ]
    )
    estimator = TransformedTargetRegressor(
        regressor=pipeline,
        transformer=StandardScaler(),
    )
    parameter_grid = {
        "regressor__tfidf__ngram_range": [(1, 1), (1, 2)],
        "regressor__ridge__alpha": [1.0, 10.0, 50.0, 100.0],
    }
    search = GridSearchCV(
        estimator=estimator,
        param_grid=parameter_grid,
        scoring="r2",
        cv=KFold(n_splits=cv_folds, shuffle=True, random_state=random_state),
        n_jobs=1,
        refit=True,
    )
    search.fit(split.train_reviews, split.train_y)
    predictions = search.predict(split.test_reviews)

    results = []
    for index, target in enumerate(TARGET_COLUMNS):
        y_true = split.test_y[:, index]
        y_predicted = predictions[:, index]
        results.append(
            {
                "target": target,
                "model": "Tuned TF-IDF + Ridge",
                "r2": r2_score(y_true, y_predicted),
                "mae": mean_absolute_error(y_true, y_predicted),
                "rmse": float(np.sqrt(mean_squared_error(y_true, y_predicted))),
            }
        )
    search_info: Dict[str, object] = {
        "cv_folds": cv_folds,
        "cv_r2": float(search.best_score_),
        "best_params": search.best_params_,
    }
    return pd.DataFrame(results), search_info


def compare_genre_models(
    split: TfidfSplit,
    alpha: float = 10.0,
) -> pd.DataFrame:
    """Compare genre-only and text-plus-genre Ridge models on the same split."""
    encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=True)
    train_genre = split.train_genres.fillna("Unknown").astype(str).to_frame()
    test_genre = split.test_genres.fillna("Unknown").astype(str).to_frame()
    train_genre_x = encoder.fit_transform(train_genre)
    test_genre_x = encoder.transform(test_genre)

    model_features = {
        "Genre only (Ridge)": (train_genre_x, test_genre_x),
        "TF-IDF + genre (Ridge)": (
            hstack([split.train_x, train_genre_x], format="csr"),
            hstack([split.test_x, test_genre_x], format="csr"),
        ),
    }
    results = []
    for model_name, (train_x, test_x) in model_features.items():
        model = Ridge(alpha=alpha)
        model.fit(train_x, split.train_y_scaled)
        predictions = model.predict(test_x) * split.target_std + split.target_mean
        for index, target in enumerate(TARGET_COLUMNS):
            y_true = split.test_y[:, index]
            y_predicted = predictions[:, index]
            results.append(
                {
                    "target": target,
                    "model": model_name,
                    "r2": r2_score(y_true, y_predicted),
                    "mae": mean_absolute_error(y_true, y_predicted),
                    "rmse": float(np.sqrt(mean_squared_error(y_true, y_predicted))),
                }
            )
    return pd.DataFrame(results)
