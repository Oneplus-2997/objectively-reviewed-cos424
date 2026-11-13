"""Pure parsers for current Pitchfork page state; no browser or network calls."""

from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import json
import re

from .common import canonical_url, clean_name, html_text, parse_date, source_id


class ParseError(ValueError):
    pass


class _Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.current = None
        self.canonical = ""
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.current = [attrs, []]
        elif tag == "link" and "canonical" in attrs.get("rel", "").split():
            self.canonical = attrs.get("href", "")
        elif tag == "a":
            self.links.append(attrs)

    def handle_data(self, text):
        if self.current is not None:
            self.current[1].append(text)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            attrs, parts = self.current
            self.scripts.append((attrs, "".join(parts)))
            self.current = None


def _document(html):
    parser = _Document()
    parser.feed(html)
    parser.close()
    return parser


def _state(doc):
    for _, script in doc.scripts:
        match = re.search(r"(?:window\s*\.\s*)?__PRELOADED_STATE__\s*=\s*", script)
        if match:
            try:
                value, _ = json.JSONDecoder().raw_decode(script[match.end():])
                if isinstance(value, dict):
                    transformed = value.get("transformed", {})
                    if isinstance(transformed, dict):
                        return transformed
            except (ValueError, TypeError) as exc:
                raise ParseError("invalid JSON page state") from exc
    raise ParseError("no supported JSON page state; page may be blocked or markup changed")


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _ld(doc):
    for attrs, script in doc.scripts:
        if attrs.get("type") == "application/ld+json":
            try:
                yield from _objects(json.loads(script))
            except ValueError:
                continue


def _names(value):
    authors = (value.get("contributors") or {}).get("author", {}).get("items", [])
    return list(dict.fromkeys(clean_name(a.get("name")) for a in authors if a.get("name")))


def parse_listing(html, *, issues=None):
    """Return (cards, next_url); inspect listing containers, never related recircs."""
    doc = _document(html)
    try:
        state = _state(doc)
    except ParseError:
        state = {}
    cards = {}
    for container in (state.get("bundle") or {}).get("containers", []):
        for item in container.get("items", []):
            try:
                url = canonical_url(item.get("url"))
            except ValueError as exc:
                if issues is not None and (item.get("contentType") == "review" or item.get("ratingValue")):
                    issues.append({"url": str(item.get("url", "")), "reason": str(exc)})
                continue
            raw_date = item.get("pubDate") or item.get("date")
            try:
                pub_date = parse_date(raw_date).isoformat()
            except ValueError:
                pub_date = ""
            cards[url] = {"url": url, "authors": _names(item), "reviewdate": pub_date}
    # JSON-LD supplies URL discovery if the richer card layout changes. Missing
    # author/date values require detail-page verification rather than exclusion.
    for obj in _ld(doc):
        if obj.get("@type") != "ListItem":
            continue
        item = obj.get("item") or {}
        candidate = obj.get("url") or (item.get("url") if isinstance(item, dict) else item)
        try:
            url = canonical_url(candidate)
        except ValueError:
            continue
        cards.setdefault(url, {"url": url, "authors": [], "reviewdate": ""})
    next_url = state.get("head.nextPage")
    if not next_url:
        next_url = next((a.get("href") for a in doc.links
                         if "next" in a.get("rel", "").split() or
                         re.fullmatch(r"\?page=\d+", a.get("href", ""))), None)
    try:
        next_url = canonical_url(next_url, listing=True) if next_url else None
    except ValueError as exc:
        raise ParseError("invalid listing pagination URL") from exc
    if not cards:
        raise ParseError("listing contains no recognized album-review cards")
    return list(cards.values()), next_url


def _body_text(node):
    """Read paragraph blocks from the site's serialized HTML tree.

    Do not descend into embeds, product cards, scripts, or recommendation nodes.
    Inline text is joined without inserting spaces around punctuation.
    """
    inline_tags = {"a", "em", "strong", "i", "b", "span", "sup", "sub", "u", "s", "br", "code"}
    containers = {"div", "section", "article", "blockquote", "ul", "ol"}

    def inline(value):
        if isinstance(value, str):
            return value
        if not isinstance(value, list) or not value or value[0] not in inline_tags | {"p", "li", "h2", "h3"}:
            return ""
        if value[0] == "br":
            return " "
        return "".join(inline(v) for v in value[1:] if not isinstance(v, dict))

    blocks = []

    def walk(value):
        if not isinstance(value, list) or not value:
            return
        tag = value[0]
        if isinstance(tag, str) and tag in {"p", "li", "h2", "h3"}:
            text = clean_name(inline(value))
            if text:
                blocks.append(text)
        elif isinstance(tag, str) and tag in containers:
            for child in value[1:]:
                walk(child)
        elif not isinstance(tag, str):
            for child in value:
                walk(child)

    walk(node)
    return "\n\n".join(blocks)


def _score(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ParseError("missing or invalid critic score") from exc
    if not number.is_finite() or not 0 <= number <= 10:
        raise ParseError("critic score is outside 0–10")
    return str(number)


def _artwork(value):
    sources = (value or {}).get("sources") or {}
    for size in ("md", "lg", "sm"):
        if sources.get(size, {}).get("url"):
            return sources[size]["url"]
    return ""


def parse_review(html, url=None):
    """Return one record per reviewed album, retaining the shared page identity.

    Publication date is taken from the article JSON-LD, not dateModified.
    Current layout/state changes raise ParseError instead of using reader scores.
    """
    doc = _document(html)
    state = _state(doc)
    review = state.get("review") or {}
    header = review.get("headerProps") or {}
    if not header:
        raise ParseError("page has no review header")
    try:
        review_url = canonical_url(doc.canonical or url)
    except ValueError as exc:
        raise ParseError("missing or invalid canonical review URL") from exc
    article = next((o for o in _ld(doc) if o.get("datePublished") and
                    o.get("@type") in {"Review", "Article", "NewsArticle", "MusicReview"}), {})
    info = header.get("infoSliceFields") or {}
    try:
        pub_date = parse_date(article.get("datePublished") or info.get("reviewDate") or
                              header.get("publishDate")).isoformat()
    except ValueError as exc:
        raise ParseError(str(exc)) from exc
    authors = _names(header) or _names(review)
    if not authors:
        raw = article.get("author") or []
        raw = raw if isinstance(raw, list) else [raw]
        authors = [clean_name(a.get("name")) for a in raw if isinstance(a, dict) and a.get("name")]
    if not authors:
        raise ParseError("review has no authors")
    artists = [clean_name(a.get("name")) for a in header.get("artists", []) if a.get("name")]
    if not artists:
        raise ParseError("review has no artists")
    body = _body_text(review.get("body"))
    if not body:
        raise ParseError("review has no supported body paragraphs")
    items = (review.get("multiReviewHeaderProps") or {}).get("itemsReviewed") or []
    multi = len(items) > 1
    # Single-review header metadata retains all labels and the displayed title;
    # itemsReviewed may abbreviate labels even on single-album reviews.
    if not multi:
        items = [{"dangerousHed": header.get("dangerousHed"),
                  "releaseYear": info.get("releaseYear"), "publisher": info.get("label"),
                  "musicRating": header.get("musicRating"), "image": header.get("lede")}]
    result = []
    sid = source_id(review_url)
    for index, item in enumerate(items):
        album = html_text(item.get("dangerousHed"))
        if not album:
            raise ParseError("reviewed album has no title")
        rating = item.get("musicRating") or {}
        # Preserve ambiguous release/reissue years for inspection, without
        # confusing the release year with the publication-date filter.
        year = clean_name(item.get("releaseYear"))
        item_artists = item.get("artists") or item.get("artistDetails") or []
        artist_names = [clean_name(a.get("name")) for a in item_artists if a.get("name")]
        result.append({
            "artist": " / ".join(artist_names or artists), "album": album,
            "reviewauthor": " / ".join(authors), "score": _score(rating.get("score")),
            "releaseyear": year, "reviewdate": pub_date,
            "recordlabel": html_text(item.get("publisher")), "genre": html_text(info.get("genre")),
            "record_id": f"{sid}:{index + 1}" if multi else sid, "source_id": sid,
            "review_url": review_url, "author_names": json.dumps(authors, ensure_ascii=False),
            "selected_authors": "[]", "description": html_text(header.get("dangerousDek")),
            "review": body,
            "accolade": "Best New Music" if rating.get("isBestNewMusic") else
                        "Best New Reissue" if rating.get("isBestNewReissue") else "",
            "artwork_url": _artwork(item.get("image")), "multi_album_review": int(multi),
        })
    return result
