"""Разбор HTML: карточки товаров и цены. Никакой сети — только строки на входе."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .config import Source


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class Item:
    title: str
    price_cents: int
    url: str


def select(node: Tag, selector: str) -> str | None:
    """'h3 a@title' → атрибут title первого совпадения; 'p.price' → его текст."""
    css, _, attr = selector.partition("@")
    el = node.select_one(css.strip()) if css.strip() else node
    if el is None:
        return None
    value = el.get(attr) if attr else el.get_text(" ", strip=True)
    if isinstance(value, list):  # например, class
        value = " ".join(value)
    return value.strip() if value else None


_PRICE_CHARS = re.compile(r"[^\d.,]")


def parse_price(text: str) -> int:
    """Цена в копейках/центах: '£51.77' → 5177, '1 299 ₽' → 129900, '12 990,50 руб.' → 1299050,
    '$1,178.99' → 117899, '1.299,00 €' → 129900."""
    raw = _PRICE_CHARS.sub("", text).strip(".,")  # «руб.» оставил бы точку в конце
    if not raw or not any(c.isdigit() for c in raw):
        raise ParseError(f"нет цифр в цене: {text!r}")

    last_dot, last_comma = raw.rfind("."), raw.rfind(",")
    sep = max(last_dot, last_comma)
    # Разделитель дробной части — последний из «.» и «,», если после него 1–2 цифры.
    if sep != -1 and 1 <= len(raw) - sep - 1 <= 2:
        integer, fraction = raw[:sep], raw[sep + 1:]
    else:
        integer, fraction = raw, ""
    integer = integer.replace(".", "").replace(",", "")
    try:
        value = Decimal(integer or "0") + (Decimal(fraction) / (10 ** len(fraction)) if fraction else 0)
    except InvalidOperation:
        raise ParseError(f"не разобрать цену: {text!r}") from None
    if value <= 0:
        raise ParseError(f"цена не больше нуля: {text!r}")
    return int((value * 100).to_integral_value())


def parse_page(html: str, page_url: str, source: Source) -> tuple[list[Item], str | None, list[str]]:
    """Возвращает (товары, ссылка на следующую страницу, ошибки по отдельным карточкам)."""
    soup = BeautifulSoup(html, "lxml")
    items, problems = [], []
    for card in soup.select(source.item):
        title = select(card, source.title)
        price_text = select(card, source.price)
        href = select(card, source.link)
        if not title or not price_text or not href:
            problems.append(f"карточка без {'названия' if not title else 'цены' if not price_text else 'ссылки'}")
            continue
        try:
            price = parse_price(price_text)
        except ParseError as e:
            problems.append(f"{title[:40]}: {e}")
            continue
        items.append(Item(title=title, price_cents=price, url=urljoin(page_url, href)))

    next_url = None
    if source.next_page:
        href = select(soup, source.next_page)
        if href:
            next_url = urljoin(page_url, href)
    return items, next_url, problems
