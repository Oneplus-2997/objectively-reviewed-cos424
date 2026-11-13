"""Run with python3 -m scripts.pitchfork_collection from the repository root."""

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from .authors import AuthorResolver, rank_authors, write_author_counts
from .collector import CollectionConfig, collect_reviews
from .common import DEFAULT_OUTPUT, REPO_ROOT, today


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect Pitchfork reviews by historical author cohort and publication date.")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("authors", "collect"):
        p = sub.add_parser(command)
        p.add_argument("--data", type=Path, default=REPO_ROOT / "data.csv")
        p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
        p.add_argument("--top-n", type=int, default=None if command == "authors" else 10,
                       help="Authors: omit to save all. Collect: defaults to the top 10 historical authors.")
        p.add_argument("--min-reviews", type=int, default=0)
        p.add_argument("--author", action="append", default=[], help="Repeat to select explicit historical authors.")
        p.add_argument("--aliases", type=Path, help="JSON mapping of verified author aliases.")
        if command == "collect":
            p.add_argument("--end-date", type=date.fromisoformat, default=today())
            p.add_argument("--max-pages", type=int, default=200)
            p.add_argument("--max-reviews", type=int)
            p.add_argument("--delay", type=float, default=2.0)
            p.add_argument("--timeout", type=float, default=30.0)
            p.add_argument("--retries", type=int, default=2)
            p.add_argument("--offline", action="store_true")
            p.add_argument("--refresh", action="store_true")
            source = p.add_mutually_exclusive_group()
            source.add_argument("--urls-file", type=Path, help="One review URL per line; skips archive discovery.")
            source.add_argument("--html-dir", type=Path, help="Parse saved HTML files without networking.")
    args = parser.parse_args(argv)
    try:
        if args.command == "authors":
            rankings = rank_authors(args.data, resolver=AuthorResolver.from_file(args.aliases),
                                    top_n=args.top_n, min_reviews=args.min_reviews, authors=args.author)
            path = write_author_counts(args.output_dir, rankings)
            for row in rankings:
                if row["selected"]:
                    print(f"{row['author']}: {row['unique_reviews']} unique reviews ({row['dataset_rows']} rows)")
            print(f"Saved selected authors to {path}")
            return 0
        urls = ()
        if args.urls_file:
            urls = tuple(line.strip() for line in args.urls_file.read_text(encoding="utf-8-sig").splitlines()
                         if line.strip() and not line.lstrip().startswith("#"))
            if not urls:
                raise ValueError("URL file is empty")
        config = CollectionConfig(
            data_path=args.data, output_dir=args.output_dir, top_n=args.top_n,
            min_reviews=args.min_reviews, authors=tuple(args.author), aliases_path=args.aliases,
            end_date=args.end_date, max_pages=args.max_pages, max_reviews=args.max_reviews,
            delay=args.delay, timeout=args.timeout, retries=args.retries,
            offline=args.offline, refresh=args.refresh,
            review_urls=urls, html_inputs=(args.html_dir,) if args.html_dir else (),
        )
        result = collect_reviews(config, progress=lambda s: print(s, flush=True))
        print(json.dumps(result.summary["period_counts"], indent=2))
        print(f"Stop reason: {result.summary['stop_reason']}; failures: {result.summary['failure_count']}")
        if result.summary["interrupted"]:
            return 130
        return 2 if result.summary["failure_count"] else 0
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
