"""Один прогон: собрать все источники → сохранить → найти изменения."""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field

from .config import Config, Source
from .fetch import Fetcher, FetchError
from .parse import Item, parse_page
from .storage import Change, Storage

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    total: int = 0
    per_source: dict[str, int] = field(default_factory=dict)
    changes: list[Change] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def scrape_source(fetcher: Fetcher, source: Source) -> tuple[list[Item], list[str]]:
    """Обходит страницы источника. Возвращает товары и некритичные проблемы.
    FetchError на первой странице пробрасывается — источник целиком недоступен."""
    items: dict[str, Item] = {}
    problems: list[str] = []
    url: str | None = source.start_url
    visited: set[str] = set()
    for page in range(1, source.max_pages + 1):
        if not url or url in visited:  # защита от зацикленной пагинации
            break
        visited.add(url)
        try:
            html = fetcher.get(url)
        except FetchError as e:
            if page == 1:
                raise
            problems.append(f"страница {page}: {e}")  # то, что успели собрать, сохраняем
            break
        page_items, url, page_problems = parse_page(html, url, source)
        problems += page_problems
        for item in page_items:
            items.setdefault(item.url, item)  # один товар на двух страницах — считаем один раз
    return list(items.values()), problems


def demo_shuffle(items: list[Item], count: int, rng: random.Random) -> list[Item]:
    """ТОЛЬКО ДЛЯ ДЕМО: на тренировочных сайтах цены не меняются никогда.
    Чтобы показать алерт на видео, сдвигаем цены у нескольких случайных товаров."""
    if not items or count <= 0:
        return items
    picked = set(rng.sample(range(len(items)), min(count, len(items))))
    out = []
    for i, it in enumerate(items):
        if i in picked:
            factor = rng.choice([0.82, 0.88, 0.9, 0.93, 1.07, 1.12])
            it = Item(it.title, max(1, round(it.price_cents * factor)), it.url)
        out.append(it)
    return out


def run_once(cfg: Config, storage: Storage, fetcher: Fetcher, now: int | None = None,
             shuffle: int = 0, rng: random.Random | None = None) -> RunResult:
    now = now or int(time.time())
    rng = rng or random.Random()
    result = RunResult()
    run_id = storage.start_run(now)

    for source in cfg.sources:
        started = time.monotonic()
        try:
            items, problems = scrape_source(fetcher, source)
        except FetchError as e:
            result.errors.append(f"{source.name}: {e}")
            log.error("%s: источник недоступен — %s", source.name, e)
            continue
        except Exception as e:  # noqa: BLE001 — один сломанный источник не должен ронять остальные
            result.errors.append(f"{source.name}: {type(e).__name__}: {e}")
            log.exception("%s: неожиданная ошибка", source.name)
            continue

        if not items:
            # Пустой результат опаснее ошибки: без этой проверки все товары молча «пропали бы».
            result.errors.append(f"{source.name}: 0 товаров — похоже, поменялась вёрстка, проверьте селекторы")
            continue
        broken = [p for p in problems if not p.startswith("страница")]
        if len(broken) > max(3, len(items) // 5):
            result.errors.append(f"{source.name}: не разобрано {len(broken)} карточек — проверьте селектор цены")
        result.errors += [f"{source.name}: {p}" for p in problems if p.startswith("страница")]

        if shuffle:
            items = demo_shuffle(items, shuffle, rng)
        changes = storage.save_items(source.name, source.currency, items, now)
        result.changes += changes
        result.per_source[source.name] = len(items)
        result.total += len(items)
        log.info("%s: %d товаров, %d изменений цен, %.1f с", source.name, len(items), len(changes),
                 time.monotonic() - started)

    storage.finish_run(run_id, int(time.time()), result.total, len(result.changes), result.errors)
    return result
