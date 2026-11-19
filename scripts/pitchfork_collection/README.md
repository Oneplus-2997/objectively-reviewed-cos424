# Pitchfork review collection

Collect reviews by prolific authors in root `data.csv`, split by **review
publication date**:

- **2023:** January 1–December 31, 2023.
- **2024 onward:** January 1, 2024 through the collection date.

Supports **Python 3.10.12** using only the standard library. No additional
packages are needed. Run the commands below from the repository root.

## 1. Choose authors

This optional command exports author rankings for inspection. Collection selects
authors independently from `data.csv`, so you can skip this step.

Save **all authors**, ranked:

```bash
python3 -m scripts.pitchfork_collection authors
```

Or save **only the top 10**:

```bash
python3 -m scripts.pitchfork_collection authors --top-n 10
```

Both write the same file: `outputs/pitchfork_collection/author_counts.csv`.
Each invocation replaces its contents with the requested author list.

Authors are ranked by unique historical reviews, inferred from author, artist,
album, and review date. Duplicate rows do not increase that count. Whitespace
and capitalization are normalized; different names require an explicit alias mapping.

## 2. Try a small collection

```bash
python3 -m scripts.pitchfork_collection collect --top-n 10 --max-pages 2 --max-reviews 5
```

Scans at most two listing pages and attempts at most five candidate reviews.
A preview may find no matching reviews; it does not cover the whole period.

## 3. Collect across the archive

```bash
python3 -m scripts.pitchfork_collection collect --top-n 10
```

The collector ranks authors directly from `data.csv` on each run. It defaults
to the top 10 and does not read or depend on the exported `author_counts.csv`.
You can collect the top 20 even if you previously exported only 10:

```bash
python3 -m scripts.pitchfork_collection collect --top-n 20
```

Discovery follows the archive until it reaches two fully dated pages before
2023, the end of pagination, or the page limit (default: 200). Check
`summary.json` and `failures.csv` before treating a run as complete.

## Outputs

Each collection saves directly in `outputs/pitchfork_collection/`, replacing
the previous collection's files. No timestamped `runs/` folders are kept.

| File | Contents |
| --- | --- |
| `author_counts.csv` | Optional author-ranking export from the `authors` command |
| `author_counts_collected.csv` | Selected authors' historical counts, plus `reviews_2023` and `reviews_2024_to_present` |
| `reviews_2023.csv`, `reviews_2024_present.csv` | Metadata, full review text, descriptions, URLs, and IDs |
| `metadata_2023.csv`, `metadata_2024_present.csv` | The first eight metadata columns from root `data.csv` |
| `discovered_reviews.csv` | Discovered URLs and filtering decisions |
| `manifest.csv`, `failures.csv` | Processing results and errors |
| `summary.json` | Settings, date range, counts, and stopping reason |

`raw_html/` and `cache/listings/` are replaced entirely, so old pages do not
accumulate. Online collections download pages afresh. `--offline` reads the
previous cache and keeps only pages used by the new collection. Saved HTML can
also be read with `--html-dir` before its output directory is replaced.

`author_counts_collected.csv` uses the authors selected for this collection,
ranked from `data.csv`. It includes authors with zero collected reviews. The
new counts measure unique review pages; a multi-album review counts once, and
a coauthored review counts for each selected author. The optional standalone
`author_counts.csv` is not changed by collection.

Dates use the New York publication calendar. Older albums reviewed during the
requested periods are included. Audio features are a separate enrichment step.
Multi-album reviews produce one row per album with shared review text and
`source_id`; `record_id` distinguishes the album rows.

## Useful options

Author selection uses the historical dataset supplied with `--data`.

| Option | Purpose |
| --- | --- |
| `--top-n 20 --min-reviews 200` | Select up to 20 authors with at least 200 historical reviews |
| `--author "Ian Cohen" --author "Stephen M. Deusner"` | Select specific authors instead of the top N |
| `--aliases names.json` | Apply a verified JSON mapping of alias names to canonical names |
| `--end-date 2026-11-12` | Set an inclusive cutoff; defaults to today in New York |
| `--max-pages 300`, `--max-reviews 10` | Set collection limits |
| `--offline` | Collect from the previous cache without network requests |
| `--urls-file urls.txt` | Collect from a URL list instead of discovering URLs |
| `--html-dir PATH` | Parse saved current-layout HTML without networking |
| `--output-dir PATH` | Change the output location |
| `--data PATH` | Use a different historical dataset for author rankings |

Network defaults: two seconds between requests, a 30-second timeout, and two
retries. Adjust with `--delay`, `--timeout`, and `--retries`. Failed pages are
logged while successful results are saved. Exit code 2 indicates failures;
130 indicates interruption. A limited preview can exit 0 while still incomplete.

## Use from a notebook

With the repository root on Python's import path:

```python
from scripts.pitchfork_collection import CollectionConfig, collect_reviews

result = collect_reviews(
    CollectionConfig(top_n=10, max_pages=2, max_reviews=5),
    progress=print,
)
result.summary       # Counts and stopping reason
result.output_dir    # Saved files (overwritten by the next collection)
# reviews = pd.DataFrame(result.records)  # Optional, if pandas is imported
```

Default input/output paths resolve from the package location. The standalone
helpers `rank_authors`, `parse_listing`, and `parse_review` are also importable.
`rank_authors` returns rankings with selection flags. To export a report from a
notebook, use `write_author_counts(output_dir, rank_authors("data.csv"))`.
Omit `top_n` to save all authors, or pass it to `rank_authors` to limit the report.

## Run tests

```bash
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```
