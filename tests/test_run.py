"""Сквозные прогоны без интернета: httpx.MockTransport отдаёт сохранённые страницы или ошибки."""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from monitor.alerts import build_message, send_telegram
from monitor.config import Config, Source
from monitor.exporters import excel, html_report
from monitor.fetch import Fetcher
from monitor.run import run_once
from monitor.storage import Change, Storage

FIXTURES = Path(__file__).parent / "fixtures"
LAPTOPS_URL = "https://shop-a.test/laptops"
BOOKS_URL = "https://shop-b.test/books"
DAY = 86400
T0 = 1_790_000_000

A = Source(name="Shop A", start_url=LAPTOPS_URL, item="div.card.thumbnail", title="a.title@title",
           price="[itemprop=price]", link="a.title@href", currency="USD")
B = Source(name="Shop B", start_url=BOOKS_URL, item="article.product_pod", title="h3 a@title",
           price="p.price_color", link="h3 a@href", currency="GBP")


def make_cfg(*sources: Source) -> Config:
    return Config(sources=list(sources), request_delay_sec=0, alert_drop_percent=5, suspicious_drop_percent=70)


class Site:
    """Фейковый интернет: url → (статус, html). Можно менять между прогонами."""

    def __init__(self) -> None:
        self.pages = {
            LAPTOPS_URL: (200, (FIXTURES / "webscraper_laptops_p1.html").read_text()),
            BOOKS_URL: (200, (FIXTURES / "books_science_p1.html").read_text()),
        }
        self.hits: dict[str, int] = {}

    def set_price(self, url: str, old: str, new: str) -> None:
        status, html = self.pages[url]
        self.pages[url] = (status, html.replace(old, new, 1))

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits[url] = self.hits.get(url, 0) + 1
        if url.endswith("/robots.txt"):
            return httpx.Response(404)
        status, html = self.pages.get(url, (404, "not found"))
        if isinstance(html, Exception):
            raise html
        return httpx.Response(status, text=html)


@pytest.fixture
def site() -> Site:
    return Site()


@pytest.fixture
def storage():
    s = Storage(":memory:")
    yield s
    s.close()


def fetcher(site: Site) -> Fetcher:
    return Fetcher("test-bot", timeout=5, delay=0, transport=httpx.MockTransport(site.handler), backoff=0)


def test_second_run_detects_price_drop(site, storage):
    cfg = make_cfg(A, B)
    first = run_once(cfg, storage, fetcher(site), now=T0)
    assert first.total == 20 and first.changes == [] and first.errors == []

    site.set_price(LAPTOPS_URL, "$416.99", "$379.99")  # Packard 255 G2 подешевел на 8.9%
    second = run_once(cfg, storage, fetcher(site), now=T0 + 3600)
    [change] = second.changes
    assert change.title == "Packard 255 G2" and change.old_cents == 41699 and change.new_cents == 37999

    rows = {r.title: r for r in storage.table(T0 + 3600)}
    assert rows["Packard 255 G2"].prev_cents == 41699
    assert rows["Packard 255 G2"].min_30d_cents == 37999

    msg = build_message(second.changes, second.errors, cfg, second.total)
    assert "Packard 255 G2" in msg and "$416.99 → <b>$379.99</b>" in msg and "(-8.9%)" in msg


def test_one_source_down_does_not_break_others(site, storage):
    site.pages[BOOKS_URL] = (503, "Service Unavailable")
    result = run_once(make_cfg(A, B), storage, fetcher(site), now=T0)
    assert result.per_source == {"Shop A": 6}
    assert any("Shop B" in e and "HTTP 503" in e for e in result.errors)
    assert site.hits[BOOKS_URL] == 3  # 503 — временная ошибка: три попытки


def test_404_is_not_retried(site, storage):
    site.pages[BOOKS_URL] = (404, "gone")
    result = run_once(make_cfg(B), storage, fetcher(site), now=T0)
    assert site.hits[BOOKS_URL] == 1 and "HTTP 404" in result.errors[0]


def test_network_error_is_reported(site, storage):
    site.pages[BOOKS_URL] = (200, httpx.ConnectError("connection refused"))
    result = run_once(make_cfg(B), storage, fetcher(site), now=T0)
    assert result.total == 0 and "сеть: ConnectError" in result.errors[0]


def test_layout_change_is_an_error_not_silence(site, storage):
    run_once(make_cfg(A), storage, fetcher(site), now=T0)
    site.pages[LAPTOPS_URL] = (200, "<html><div class='totally-new-design'></div></html>")
    result = run_once(make_cfg(A), storage, fetcher(site), now=T0 + 3600)
    assert "0 товаров" in result.errors[0] and result.changes == []
    # старые цены никуда не делись и по-прежнему в таблице
    assert len(storage.table(T0 + 3600)) == 6


def test_robots_txt_is_respected(site, storage):
    original = site.handler

    def with_robots(request):
        if str(request.url).endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /laptops")
        return original(request)

    f = Fetcher("test-bot", timeout=5, delay=0, transport=httpx.MockTransport(with_robots), backoff=0)
    result = run_once(make_cfg(A), storage, f, now=T0)
    assert "robots.txt" in result.errors[0] and LAPTOPS_URL not in site.hits


def test_min_30d_includes_price_valid_at_window_start(storage):
    from monitor.parse import Item

    item = lambda cents: [Item("X", cents, "https://x.test/1")]  # noqa: E731
    storage.save_items("S", "RUB", item(8000), T0 - 40 * DAY)   # 40 дней назад: 80 ₽
    storage.save_items("S", "RUB", item(10000), T0 - 10 * DAY)  # 10 дней назад: 100 ₽
    storage.save_items("S", "RUB", item(10000), T0)
    [row] = storage.table(T0)
    assert row.min_30d_cents == 8000  # 80 ₽ действовали ещё 20 дней внутри окна


def test_alert_separates_suspicious_drops_and_escapes_html():
    cfg = make_cfg(A)
    changes = [
        Change("Shop A", "Обычный <товар>", "https://a.test/1", "RUB", 100000, 90000),   # −10%
        Change("Shop A", "Сломанный", "https://a.test/2", "RUB", 100000, 1000),           # −99%
        Change("Shop A", "Мелочь", "https://a.test/3", "RUB", 100000, 98000),             # −2%, ниже порога
        Change("Shop A", "Подорожал", "https://a.test/4", "RUB", 100000, 120000),
    ]
    msg = build_message(changes, ["Shop B: HTTP 503"], cfg, total=100)
    assert "Обычный &lt;товар&gt;" in msg
    assert msg.index("Обычный") < msg.index("Слишком резкое") < msg.index("Сломанный")
    assert "Мелочь" not in msg and "Подорожал" not in msg
    assert "Shop B: HTTP 503" in msg
    assert build_message([], [], cfg, total=100) is None  # нечего сказать — не спамим


def test_alert_fits_telegram_limit():
    cfg = make_cfg(A)
    changes = [Change("Shop A", f"Товар {i} " + "x" * 60, f"https://a.test/{i}", "RUB", 100000, 80000)
               for i in range(200)]
    msg = build_message(changes, [], cfg, total=200)
    assert len(msg) <= 4096 and "…и ещё" in msg


def test_telegram_errors_are_explicit():
    bad_token = httpx.MockTransport(lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    with pytest.raises(RuntimeError, match="401"):
        send_telegram("bad", "1", "hi", transport=bad_token)


def test_exports_are_written(site, storage, tmp_path):
    run_once(make_cfg(A, B), storage, fetcher(site), now=T0)
    site.set_price(BOOKS_URL, "£", "£1")  # у первой цены на странице приписали «1» — цена выросла
    run_once(make_cfg(A, B), storage, fetcher(site), now=T0 + 60)
    rows, history = storage.table(T0 + 60), storage.recent_changes(T0 - DAY)
    x = excel.export(rows, history, tmp_path / "p.xlsx")
    h = html_report.export(rows, history, [], T0 + 60, 5, tmp_path / "r.html")
    assert x.stat().st_size > 5000
    text = h.read_text()
    assert "const DATA = {" in text and "</script>" not in re.sub(r"</script>\s*</body>", "", text)
