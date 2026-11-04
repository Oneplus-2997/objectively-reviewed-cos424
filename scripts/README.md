Run the review pipeline from any working directory using these scripts in order:

```bash
python3 scripts/html_to_txt.py
python3 scripts/txt_to_csv.py
python3 scripts/merge_reviews.py
```

The example commands assume the repository is the current directory. Default
input and output paths are resolved relative to the scripts. Only Python's
standard library is required; each script accepts `--help`.

The `notebooks/` directory contains notebooks for the same workflow:
run `data_cleaning.ipynb`, then `extract_html_data.ipynb` (HTML extraction and
review CSV creation), then `merge_data_and_text.ipynb` (validated matching and
exclusion reports). Run each notebook's cells in order. Their path settings are
in the first configuration cell, and the Python scripts remain available. See
[the notebook instructions](../notebooks/README.md) for setup and output paths.

`html_to_txt.py` extracts the first artist link and album heading, publication
date, author, score, description, and review. Names retain their punctuation.
The filename is the first 32 hexadecimal characters of the SHA-256 hash of the
canonical review URL, followed by `.txt`. URL scheme, `www`, tracking parameters,
fragments, and trailing slashes are canonicalized before hashing.

Text files contain the description, a blank line, and the review paragraphs.
`outputs/extracting_text/html_text_match.csv` links each source ID and text filename to its
original HTML filename and matching metadata. It supplies the metadata to
`txt_to_csv.py`; filenames are never parsed for artist or album names.

`outputs/review_text.csv` has only these columns:

```text
source_id,artist,album,reviewdate,reviewauthor,score,description,review
```

The merger uses normalized artist, album, and publication date to find a unique
candidate, then requires the author and numeric score to agree. Normalization
handles Unicode, curly quotes, whitespace, and case. It does not remove
punctuation, guess names, or repair spreadsheet-converted titles. Original names
remain unchanged in the source metadata. `outputs/full_review.csv` retains the
columns from `data_clean.csv` and adds `source_id`, `description`, and `review`.
Each source ID appears at most once in the merged output.

The exclusion rules and reports are:

- Every HTML in a repeated source-ID or normalized artist/album/date group is
  discarded, rather than selecting one copy. These appear in
  `outputs/extracting_text/discarded_html.csv`; corresponding metadata rows are excluded too.
- Every metadata row in a repeated normalized artist/album/date group is
  discarded, along with the corresponding text rows during merging. No record
  label is selected arbitrarily.
- Prince's December 2, 2019 review of `1999` is explicitly excluded. The April
  30, 2016 review remains eligible.
- Merge exclusions are saved in `outputs/discarded_on_merge/`, with a
  `discard_reason` column. Other unmatched, ambiguous, or inconsistent rows go
  to `outputs/missed_on_merge/`, with a `merge_reason` column.
- Missing descriptions are listed by source-ID text filename in
  `outputs/extracting_text/missing_descriptions.txt`. These text files are retained but excluded
  from `review_text.csv`. Invalid source pages are listed in
  `outputs/extracting_text/skipped_htmls.txt`.

Raw HTML and `data_clean.csv` are preserved. Each extraction run archives the
previous text directory in an `outputs/previous_text_reviews_*` folder before
replacing it. Generated CSVs and reports describe the latest run.

Run the regression checks with:

```bash
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```
