"""Деньги храним в копейках/центах (int), форматируем только на выходе."""
from __future__ import annotations

SYMBOLS = {"RUB": "₽", "USD": "$", "GBP": "£", "EUR": "€", "KZT": "₸", "BYN": "Br"}
PREFIX = {"USD", "GBP", "EUR"}  # символ перед числом: $10.00; остальные после: 10 ₽


def fmt(cents: int, currency: str) -> str:
    symbol = SYMBOLS.get(currency, currency)
    whole, frac = divmod(abs(cents), 100)
    if currency in PREFIX:
        number = f"{whole:,}.{frac:02d}"
        return f"{'-' if cents < 0 else ''}{symbol}{number}"
    number = f"{whole:,}".replace(",", " ") + (f",{frac:02d}" if frac else "")
    return f"{'-' if cents < 0 else ''}{number} {symbol}"


def excel_format(currency: str) -> str:
    symbol = SYMBOLS.get(currency, currency)
    if currency in PREFIX:
        return f'"{symbol}"#,##0.00'
    return f'#,##0.00 "{symbol}"'
