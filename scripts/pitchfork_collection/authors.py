"""Rank the historical author cohort without guessing personal-name aliases."""

from collections import Counter, defaultdict
import csv
import json
from pathlib import Path

from .common import AUTHOR_FIELDS, clean_name, name_key, parse_date, write_csv


class AuthorResolver:
    """Apply whitespace/Unicode normalization and optional explicit aliases."""

    def __init__(self, aliases=None):
        aliases = aliases or {}
        if not isinstance(aliases, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or not clean_name(v)
            for k, v in aliases.items()
        ):
            raise ValueError("aliases must be a JSON object mapping names to canonical names")
        self.aliases = {name_key(k): clean_name(v) for k, v in aliases.items()}
        for key in self.aliases:
            self.resolve(key)

    def resolve(self, value):
        value = clean_name(value)
        visited = set()
        while name_key(value) in self.aliases:
            key = name_key(value)
            target = self.aliases[key]
            if name_key(target) == key:
                return target
            if key in visited:
                raise ValueError("author aliases contain a cycle")
            visited.add(key)
            value = target
        return value

    def key(self, value):
        return name_key(self.resolve(value))

    @classmethod
    def from_file(cls, path=None):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")) if path else {})


def rank_authors(data_path, *, resolver=None, top_n=None, min_reviews=0, authors=()):
    """Return rankings; unique_reviews counts author/artist/album/date identities.

    These are inferred review identities because the historical CSV has no URL.
    Coauthored strings stay intact unless the user supplies an explicit mapping.
    """
    resolver = resolver or AuthorResolver()
    if (top_n is not None and top_n < 1) or min_reviews < 0:
        raise ValueError("top_n must be positive and min_reviews nonnegative")
    counts = Counter()
    variants = defaultdict(Counter)
    labels = defaultdict(Counter)
    identities = defaultdict(set)
    with Path(data_path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"reviewauthor", "artist", "album", "reviewdate"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"historical CSV needs columns: {sorted(required)}")
        for row in reader:
            raw = row["reviewauthor"]
            if not clean_name(raw):
                continue
            key = resolver.key(raw)
            counts[key] += 1
            variants[key][raw] += 1
            labels[key][resolver.resolve(raw)] += 1
            try:
                review_date = parse_date(row["reviewdate"]).isoformat()
            except ValueError:
                review_date = clean_name(row["reviewdate"])
            identities[key].add((name_key(row["artist"]), name_key(row["album"]), review_date))
    ordered = sorted(counts, key=lambda k: (-len(identities[k]), -counts[k], k))
    if authors:
        chosen = {resolver.key(a) for a in authors}
        missing = chosen - counts.keys()
        if missing:
            raise ValueError(f"selected authors absent from historical CSV: {sorted(missing)}")
    else:
        chosen = {k for k in ordered if len(identities[k]) >= min_reviews}
        chosen = set([k for k in ordered if k in chosen][:top_n])
    if not chosen:
        raise ValueError("no authors satisfy the selection")
    return [
        {"rank": i, "author": labels[k].most_common(1)[0][0], "dataset_rows": counts[k],
         "unique_reviews": len(identities[k]), "selected": int(k in chosen),
         "variants": json.dumps(dict(variants[k]), ensure_ascii=False)}
        for i, k in enumerate(ordered, 1)
    ]


def write_author_counts(output_dir, rankings):
    """Save exactly the requested author cohort in the single counts CSV."""
    output_dir = Path(output_dir)
    path = output_dir / "author_counts.csv"
    write_csv(path, AUTHOR_FIELDS, [row for row in rankings if row["selected"]])
    return path

