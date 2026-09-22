"""Выгрузка в Google Sheets через сервисный аккаунт (включается, если заданы ключ и ID таблицы)."""
from __future__ import annotations

from datetime import datetime

from ..storage import Change, Row
from .excel import sort_key


def _sheet(book, title: str, rows: int, cols: int):
    import gspread

    try:
        ws = book.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title, rows=rows, cols=cols)
    return ws


def export(rows: list[Row], history: list[tuple[int, Change]], credentials_file: str, spreadsheet_id: str) -> str:
    import gspread

    gc = gspread.service_account(filename=credentials_file)
    book = gc.open_by_key(spreadsheet_id)

    values = [["Конкурент", "Товар", "Цена", "Валюта", "Было", "Изменение", "Мин. за 30 дней",
               "Цена изменилась", "Проверено", "Ссылка"]]
    for r in sorted(rows, key=sort_key):
        values.append([
            r.source, r.title, r.price_cents / 100, r.currency, r.prev_cents / 100 if r.prev_cents else "",
            r.percent / 100 if r.percent is not None else "", r.min_30d_cents / 100,
            f"{datetime.fromtimestamp(r.changed_at):%d.%m.%Y %H:%M}",
            f"{datetime.fromtimestamp(r.last_seen):%d.%m.%Y %H:%M}", r.url,
        ])
    ws = _sheet(book, "Цены", len(values) + 20, len(values[0]))
    ws.clear()
    ws.update(values, "A1", value_input_option="RAW")
    ws.format("A1:J1", {"backgroundColor": {"red": .12, "green": .16, "blue": .22},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}})
    ws.format(f"F2:F{len(values)}", {"numberFormat": {"type": "PERCENT", "pattern": "+0.0%;-0.0%;—"}})
    ws.freeze(rows=1)

    hist = [["Когда", "Конкурент", "Товар", "Было", "Стало", "Валюта", "Изменение", "Ссылка"]]
    for ts, c in history:
        hist.append([f"{datetime.fromtimestamp(ts):%d.%m.%Y %H:%M}", c.source, c.title, c.old_cents / 100,
                     c.new_cents / 100, c.currency, c.percent / 100, c.url])
    hs = _sheet(book, "Изменения", len(hist) + 20, len(hist[0]))
    hs.clear()
    hs.update(hist, "A1", value_input_option="RAW")
    hs.format("A1:H1", {"textFormat": {"bold": True}})
    if len(hist) > 1:
        hs.format(f"G2:G{len(hist)}", {"numberFormat": {"type": "PERCENT", "pattern": "+0.0%;-0.0%;—"}})
    hs.freeze(rows=1)
    return book.url
