"""HTML-отчёт: один самодостаточный файл без внешних зависимостей — открыть в браузере или выложить."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..money import fmt
from ..storage import Change, Row
from .excel import sort_key

TEMPLATE = Path(__file__).with_name("report_template.html")


def render(rows: list[Row], history: list[tuple[int, Change]], errors: list[str], last_run: int,
           alert_drop_percent: float) -> str:
    data = {
        "generated": datetime.fromtimestamp(last_run).strftime("%d.%m.%Y %H:%M"),
        "threshold": alert_drop_percent,
        "errors": errors,
        "sources": sorted({r.source for r in rows}),
        "rows": [
            {
                "source": r.source, "title": r.title, "url": r.url,
                "price": r.price_cents / 100, "priceText": fmt(r.price_cents, r.currency),
                "prev": r.prev_cents / 100 if r.prev_cents else None,
                "prevText": fmt(r.prev_cents, r.currency) if r.prev_cents else "",
                "pct": round(r.percent, 2) if r.percent is not None else None,
                "min": r.min_30d_cents / 100,
                "minText": fmt(r.min_30d_cents, r.currency),
                "isMin": r.price_cents <= r.min_30d_cents,
                "changed": datetime.fromtimestamp(r.changed_at).strftime("%d.%m %H:%M"),
                "changedTs": r.changed_at,
            }
            for r in sorted(rows, key=sort_key)
        ],
        "history": [
            {
                "when": datetime.fromtimestamp(ts).strftime("%d.%m %H:%M"), "source": c.source, "title": c.title,
                "url": c.url, "old": fmt(c.old_cents, c.currency), "new": fmt(c.new_cents, c.currency),
                "pct": round(c.percent, 2),
            }
            for ts, c in history[:60]
        ],
    }
    # Названия товаров пришли с чужих сайтов: «</script>» внутри JSON закрыл бы тег, поэтому экранируем «<».
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)


def export(rows, history, errors, last_run, alert_drop_percent, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.html")
    tmp.write_text(render(rows, history, errors, last_run, alert_drop_percent), encoding="utf-8")
    tmp.replace(path)
    return path
