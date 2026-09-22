from pathlib import Path

import pytest

from monitor.config import Source
from monitor.parse import ParseError, parse_page, parse_price

FIXTURES = Path(__file__).parent / "fixtures"

BOOKS = Source(name="Books", start_url="https://books.toscrape.com/catalogue/page-1.html",
               item="article.product_pod", title="h3 a@title", price="p.price_color", link="h3 a@href",
               next_page="li.next a@href", currency="GBP")
LAPTOPS = Source(name="Laptops", start_url="https://webscraper.io/test-sites/e-commerce/static/computers/laptops",
                 item="div.card.thumbnail", title="a.title@title", price="[itemprop=price]", link="a.title@href",
                 next_page="a.page-link[rel=next]@href", currency="USD")


@pytest.mark.parametrize("text, cents", [
    ("£51.77", 5177),
    ("$1,178.99", 117899),
    ("1 299 ₽", 129900),
    ("1 299 ₽", 129900),        # неразрывные пробелы, как на большинстве русских сайтов
    ("12 990,50 руб.", 1299050),
    ("1.299,00 €", 129900),
    ("от 990 ₽", 99000),
    ("1,299", 129900),                   # запятая как разделитель тысяч
    ("12.5", 1250),
])
def test_parse_price(text, cents):
    assert parse_price(text) == cents


@pytest.mark.parametrize("text", ["Нет в наличии", "", "0 ₽", "бесплатно"])
def test_parse_price_rejects_garbage(text):
    with pytest.raises(ParseError):
        parse_price(text)


def test_books_page():
    html = (FIXTURES / "books_science_p1.html").read_text()
    items, next_url, problems = parse_page(html, "https://books.toscrape.com/catalogue/category/books/science_22/index.html",
                                           BOOKS)
    assert len(items) == 14 and not problems and next_url is None
    first = items[0]
    assert first.title == "The Most Perfect Thing: Inside (and Outside) a Bird's Egg"
    assert first.url.startswith("https://books.toscrape.com/catalogue/the-most-perfect-thing")
    assert first.price_cents > 0


def test_laptops_page_and_pagination():
    html = (FIXTURES / "webscraper_laptops_p1.html").read_text()
    items, next_url, problems = parse_page(html, LAPTOPS.start_url, LAPTOPS)
    assert len(items) == 6 and not problems
    assert items[0].title == "Packard 255 G2" and items[0].price_cents == 41699
    assert next_url == LAPTOPS.start_url + "?page=2"


def test_changed_layout_gives_zero_items_not_crash():
    items, next_url, problems = parse_page("<html><body><div class='new-card'>…</div></body></html>",
                                           LAPTOPS.start_url, LAPTOPS)
    assert items == [] and next_url is None


def test_card_without_price_is_reported():
    html = """<div class="card thumbnail"><a class="title" title="X" href="/p/1">X</a>
              <span itemprop="price">Нет в наличии</span></div>"""
    items, _, problems = parse_page(html, LAPTOPS.start_url, LAPTOPS)
    assert items == [] and "нет цифр" in problems[0]
