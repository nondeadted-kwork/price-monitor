"""Алерт в Telegram: одно сообщение на прогон, а не по сообщению на товар."""
from __future__ import annotations

import logging
import time
from html import escape

import httpx

from .config import Config
from .money import fmt
from .storage import Change

log = logging.getLogger(__name__)
LIMIT = 4000  # у Telegram 4096, оставляем запас


def build_message(changes: list[Change], errors: list[str], cfg: Config, total: int,
                  sheet_url: str | None = None) -> str | None:
    drops = sorted((c for c in changes if c.percent <= -cfg.alert_drop_percent), key=lambda c: c.percent)
    suspicious = [c for c in drops if c.percent <= -cfg.suspicious_drop_percent]
    normal = [c for c in drops if c.percent > -cfg.suspicious_drop_percent]
    if not drops and not errors:
        return None

    def line(c: Change) -> str:
        return (f"• <b>{escape(c.source)}</b> · <a href=\"{escape(c.url, quote=True)}\">{escape(c.title[:70])}</a>\n"
                f"   {fmt(c.old_cents, c.currency)} → <b>{fmt(c.new_cents, c.currency)}</b> ({c.percent:+.1f}%)")

    parts: list[str] = []
    if normal:
        parts.append(f"📉 <b>Снижение цен у конкурентов: {len(normal)}</b>")
        parts += [line(c) for c in normal]
    if suspicious:
        parts.append(f"\n⚠️ <b>Слишком резкое падение (больше {cfg.suspicious_drop_percent:.0f}%) — проверьте вручную, "
                     f"похоже на ошибку на сайте:</b>")
        parts += [line(c) for c in suspicious]
    if errors:
        parts.append("\n🛠 <b>Не удалось собрать:</b>")
        parts += [f"• {escape(e)}" for e in errors]

    footer = f"\nВсего товаров под наблюдением: {total}."
    if sheet_url:
        footer += f' <a href="{escape(sheet_url, quote=True)}">Открыть таблицу</a>'

    text = ""
    for i, part in enumerate(parts):
        if len(text) + len(part) + len(footer) + 40 > LIMIT:
            text += f"\n…и ещё {len(parts) - i} строк — смотрите таблицу."
            break
        text += ("\n" if text else "") + part
    return text + footer


def send_telegram(token: str, chat_id: str, text: str, transport: httpx.BaseTransport | None = None) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    with httpx.Client(timeout=20, transport=transport) as client:
        for attempt in range(1, 4):
            try:
                r = client.post(url, json=payload)
                if r.status_code == 200:
                    return
                if r.status_code == 429:
                    time.sleep(int(r.json().get("parameters", {}).get("retry_after", 5)))
                    continue
                # 400/401/403 повтор не исправит: неверный токен, chat id или бот не добавлен в чат
                raise RuntimeError(f"Telegram ответил {r.status_code}: {r.text[:200]}")
            except httpx.HTTPError as e:
                log.warning("Telegram недоступен (%s), попытка %d/3", type(e).__name__, attempt)
                time.sleep(2 ** attempt)
    raise RuntimeError("Telegram недоступен после 3 попыток")
