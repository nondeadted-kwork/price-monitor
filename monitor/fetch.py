"""HTTP: таймауты, повторы с нарастающей паузой, robots.txt, вежливая скорость."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(self, user_agent: str, timeout: float, delay: float,
                 transport: httpx.BaseTransport | None = None, retries: int = 3, backoff: float = 2.0) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "ru,en;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self.user_agent = user_agent
        self.delay = delay
        self.retries = retries
        self.backoff = backoff
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_request: dict[str, float] = {}

    def close(self) -> None:
        self.client.close()

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            rp = None
            try:
                r = self.client.get(f"{host}/robots.txt")
                if r.status_code == 200:
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                pass  # robots.txt недоступен — считаем, что ограничений нет
            self._robots[host] = rp
        rp = self._robots[host]
        return rp is None or rp.can_fetch(self.user_agent, url)

    def _polite_wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        wait = self.delay - (time.monotonic() - self._last_request.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def get(self, url: str) -> str:
        if not self.allowed(url):
            raise FetchError(f"robots.txt запрещает {url}")
        last_error = ""
        for attempt in range(1, self.retries + 1):
            self._polite_wait(url)
            try:
                r = self.client.get(url)
            except httpx.TimeoutException:
                last_error = "таймаут"
            except httpx.HTTPError as e:
                last_error = f"сеть: {type(e).__name__}"
            else:
                if r.status_code == 200:
                    return r.text
                last_error = f"HTTP {r.status_code}"
                if r.status_code not in RETRY_STATUSES:
                    break  # 403/404 повтор не исправит
                retry_after = r.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    time.sleep(min(int(retry_after), 60))
            if attempt < self.retries:
                pause = self.backoff ** attempt
                log.warning("%s — %s, повтор %d/%d через %.0f с", url, last_error, attempt, self.retries - 1, pause)
                time.sleep(pause)
        raise FetchError(f"{last_error} ({url})")
