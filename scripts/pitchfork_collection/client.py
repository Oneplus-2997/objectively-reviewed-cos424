"""Paced HTTP downloads with bounded retries and reusable HTML caches."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .common import canonical_url, source_id


class FetchError(OSError):
    pass


class HtmlClient:
    def __init__(self, output_dir, *, delay=2.0, timeout=30.0, retries=2,
                 offline=False, refresh=False):
        self.output_dir = Path(output_dir)
        self.delay, self.timeout, self.retries = delay, timeout, retries
        self.offline, self.refresh = offline, refresh
        self.last_request = None

    def cache_path(self, url, *, listing=False):
        if listing:
            digest = hashlib.sha256(url.encode()).hexdigest()[:32]
            return self.output_dir / "cache" / "listings" / f"{digest}.html"
        return self.output_dir / "raw_html" / f"{source_id(url)}.html"

    def fetch(self, url, *, listing=False):
        url = canonical_url(url, listing=listing)
        path = self.cache_path(url, listing=listing)
        # Listing pages change when new reviews are published; refresh them on
        # every online run. Detail pages are reused unless refresh is requested.
        if path.exists() and (self.offline or (not listing and not self.refresh)):
            return path.read_text(encoding="utf-8-sig")
        if self.offline:
            raise FetchError(f"offline cache miss: {url}")
        for attempt in range(self.retries + 1):
            if self.last_request is not None:
                time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            request = Request(url, headers={
                "User-Agent": "PitchforkReviewResearch/1.0",
                "Accept": "text/html,application/xhtml+xml",
            })
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    canonical_url(response.geturl(), listing=listing)
                    content_type = response.headers.get_content_type()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise FetchError(f"expected HTML, received {content_type}")
                    html = response.read().decode(response.headers.get_content_charset() or "utf-8")
                if not html.strip():
                    raise FetchError("empty HTML response")
                path.parent.mkdir(parents=True, exist_ok=True)
                staged = path.with_suffix(".html.tmp")
                staged.write_text(html, encoding="utf-8")
                staged.replace(path)
                return html
            except HTTPError as exc:
                wait = 2 ** attempt
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after:
                    try:
                        wait = float(retry_after)
                    except ValueError:
                        try:
                            wait = max(0, (parsedate_to_datetime(retry_after) -
                                           datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            pass
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise FetchError(f"HTTP {exc.code}: {url}") from exc
                if wait > 60:
                    raise FetchError(f"server requests waiting {wait:.0f}s; resume later: {url}") from exc
                time.sleep(max(0, wait))
            except (URLError, TimeoutError, ConnectionError) as exc:
                if attempt == self.retries:
                    raise FetchError(f"download failed: {url}: {exc}") from exc
                time.sleep(2 ** attempt)
        raise FetchError(f"download failed: {url}")
