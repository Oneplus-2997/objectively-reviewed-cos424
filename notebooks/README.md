# Notebook workflow

Run these notebooks in order, completing each one before starting the next:

1. [data_cleaning.ipynb](data_cleaning.ipynb)
   reads `data.csv`, cleans the metadata and audio features, and creates
   `outputs/data_clean.csv`. Cleaning reports go in `outputs/data_cleaning/`.
2. [extract_html_data.ipynb](extract_html_data.ipynb)
   reads `pitchfork-scrape/raw-html/`, extracts source-ID text files, and creates
   `outputs/review_text.csv`. Text files go in `outputs/text_reviews/`, and
   support files go in `outputs/extracting_text/`.
3. [merge_data_and_text.ipynb](merge_data_and_text.ipynb)
   links the cleaned data and text using normalized artist, album, and review
   date, with author and score checks. It creates `outputs/full_review.csv`.
   Unresolved rows go in `outputs/missed_on_merge/`, and discarded rows go in
   `outputs/discarded_on_merge/`.

All paths above are relative to the repository root.

## How to run

1. Open the repository in VS Code with notebook support, or open the notebooks
   in Jupyter.
2. Select a Python 3 kernel. The cleaning notebook requires **NumPy** and
   **pandas** in that environment. The other two use only the standard library.
3. Open `data_cleaning.ipynb`, review its configuration cell, then restart the
   kernel and choose **Run All**. Wait for the validation and save cells to finish.
4. Run all cells in `extract_html_data.ipynb`, then all cells in
   `merge_data_and_text.ipynb`. HTML extraction can take a few minutes.
5. Find the generated files in the repository's `outputs/` directory.

The notebooks locate the repository automatically when the kernel starts in
either the repository root or `notebooks/`, including deeper subdirectories.
They read the original inputs from the root and save outputs there; they do not
create an `outputs/` directory inside `notebooks/`.

Edit input and output paths in each notebook's configuration cell if needed.
Keep related paths consistent across the three notebooks when using custom
locations. The extraction notebook archives the previous text directory before
replacing it on another run.

The extraction and merge notebooks are self-contained. The Python scripts in
`scripts/` remain available as an alternative way to run those stages; see
[scripts/README.md](../scripts/README.md).

## Final notebook and NLP analysis

[main_notebook.ipynb](../main_notebook.ipynb) is the consolidated end-to-end
notebook. After cleaning, HTML extraction, and merging, its final section
analyzes review language as a predictor of Pitchfork scores and Spotify audio
features. It calls reusable modeling code in
[`scripts/preliminary_results.py`](../scripts/preliminary_results.py) to run a
held-out TF-IDF/Ridge baseline against a training-mean baseline and display
selected word coefficients. The vocabulary and target scaling are fit on
training rows only. This is an initial bag-of-words benchmark, not the proposed
word-by-word embedding analysis. The baseline requires scikit-learn, listed in
the repository's `requirements.txt`.
