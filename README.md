# objectively-reviewed

An extension of "Objectively Reviewed", the ICWSM 2020 dataset paper. This
project combines Pitchfork album reviews with album metadata and Spotify audio
features to explore relationships between review language, critical scores,
and musical characteristics.

[main_notebook.ipynb](main_notebook.ipynb) is the main entry point. It prepares
the dataset, displays descriptive and exploratory analyses, and fits an initial
model to predict review scores and audio features from review text.

## Notebook workflow

Run the notebook from top to bottom:

| Section | What it does |
| --- | --- |
| 0. Setup and configuration | Defines repository paths, output locations, and cleaning settings. |
| 1. Clean metadata and audio features | Reads `data.csv`, normalizes text, handles missing values and duplicates, checks audio features, and exports cleaned metadata. |
| 2. Extract review text | Calls the scripts in `scripts/` to parse saved HTML, identify reviews, exclude duplicate groups, and build the review-text CSV. |
| 3. Merge metadata and review text | Calls the merge script to match artist, album, and date, require author and score agreement, and report unmatched or discarded rows. |
| 4. Validate outputs | Checks row accounting and source-ID uniqueness, and summarizes the generated datasets. |
| 5. Descriptive and exploratory statistics | Examines missingness, audio-feature distributions and correlations, text lengths, vocabulary, genre differences, and review scores. |
| 6. NLP baseline | Uses TF-IDF unigram/bigram features and Ridge regression to predict scores and nine audio features, comparing results with a training-mean baseline on an 80/20 held-out split. |

Sections 2 and 3 import reusable functions from
[html_to_txt.py](scripts/html_to_txt.py),
[txt_to_csv.py](scripts/txt_to_csv.py),
[review_common.py](scripts/review_common.py), and
[merge_reviews.py](scripts/merge_reviews.py). Section 6 uses
[preliminary_results.py](scripts/preliminary_results.py). These scripts must
remain in the repository's `scripts/` directory; the notebook adds that
directory to Python's import path automatically. You do not need to run the
scripts separately when using the notebook.

## Setup

Keep the repository structure intact, including:

- `data.csv`: source album metadata, scores, and audio features.
- `pitchfork-scrape/raw-html/`: saved Pitchfork review pages.
- `scripts/`: extraction, merging, and NLP helpers.

The workflow uses these local inputs; it does not require Spotify credentials
or a new web scrape.

From the repository root, create and activate a Python 3 environment, then
install the dependencies pinned in [requirements.txt](requirements.txt):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead.
The requirements include NumPy and pandas for data preparation, Matplotlib and
Seaborn for plots, scikit-learn for text features and regression, and Jupyter
and IPython for notebook execution.

## Execute the notebook

Open `main_notebook.ipynb` in VS Code and select the environment where you
installed the requirements, or launch JupyterLab from the activated environment:

```bash
jupyter lab main_notebook.ipynb
```

1. Review **0. Setup and configuration**, including `HTML_INPUTS`,
   `OUTPUTS_DIR`, and the cleaning settings. The notebook locates the repository
   from the current directory or its parents. If the kernel starts outside the
   repository, set `REPO_ROOT_OVERRIDE` to the repository's absolute path.
2. For local execution, comment out or skip the Colab-specific cell
   `%cd /content/objectively-reviewed/outputs` in **5.1 Dataset Overview**.
   The remaining cells use the configured paths, so this directory change is
   unnecessary. Comment it out before choosing **Run All**.
3. Restart the kernel and run all cells in order. Wait for extraction and
   merging to finish before continuing to the analysis; parsing the complete
   HTML collection can take several minutes.
4. Review the validation summaries and merge exclusions, then inspect the
   plots and held-out model results displayed in the notebook. The NLP section
   reports R², MAE, RMSE, and selected positive and negative word coefficients.

After changing paths or cleaning settings, restart the kernel and rerun the
workflow so later sections use the updated results.

## Generated files

By default, generated files are saved under `outputs/`. Changing `OUTPUTS_DIR`
in the setup cell redirects the workflow's outputs together.

| Path | Contents |
| --- | --- |
| `outputs/data_clean.csv` | Cleaned metadata, audio features, helper fields, and quality flags. |
| `outputs/data_cleaning/log_df.csv` | Cleaning actions and affected counts. |
| `outputs/text_reviews/` | Extracted description and review text, named by source ID. |
| `outputs/review_text.csv` | Review identity fields, descriptions, and review bodies. |
| `outputs/extracting_text/` | HTML/text manifest, duplicate report, missing-description list, and skipped-page log. |
| `outputs/full_review.csv` | Validated merged dataset used by the exploratory and NLP sections. |
| `outputs/missed_on_merge/` | Unresolved metadata and text rows, with `merge_reason`. |
| `outputs/discarded_on_merge/` | Excluded metadata and text rows, with `discard_reason`. |

The merged dataset contains the matched subset of cleaned metadata plus
`source_id`, `description`, and `review`. Plots, model metrics, and selected
coefficients are displayed in the notebook.

Rerunning the workflow replaces generated CSVs and reports. Before replacing
the extracted text directory, extraction archives the previous files under
`outputs/previous_text_reviews_*/text_reviews/`. Source inputs are preserved.

The [separate preparation notebooks](notebooks/README.md) remain available.
See [scripts/README.md](scripts/README.md) for the extraction and merge command
line workflow and its matching rules.
