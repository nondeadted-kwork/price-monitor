"""SQLite: товары и история цен. Новая строка в prices пишется только когда цена изменилась."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id         INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,
    url        TEXT NOT NULL,
    title      TEXT NOT NULL,
    currency   TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    UNIQUE (source, url)
);
CREATE TABLE IF NOT EXISTS prices (
    product_id  INTEGER NOT NULL REFERENCES products(id),
    price_cents INTEGER NOT NULL,
    seen_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices ON prices(product_id, seen_at);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  INTEGER NOT NULL,
    finished_at INTEGER,
    items       INTEGER DEFAULT 0,
    changes     INTEGER DEFAULT 0,
    errors      TEXT DEFAULT ''
);
"""


@dataclass(frozen=True)
class Change:
    source: str
    title: str
    url: str
    currency: str
    old_cents: int
    new_cents: int

    @property
    def percent(self) -> float:
        return (self.new_cents - self.old_cents) / self.old_cents * 100


@dataclass(frozen=True)
class Row:
    """Строка итоговой таблицы: текущая цена товара и контекст."""
    source: str
    title: str
    url: str
    currency: str
    price_cents: int
    prev_cents: int | None
    min_30d_cents: int
    changed_at: int
    last_seen: int

    @property
    def percent(self) -> float | None:
        if not self.prev_cents:
            return None
        return (self.price_cents - self.prev_cents) / self.prev_cents * 100


class Storage:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def start_run(self, now: int) -> int:
        cur = self.db.execute("INSERT INTO runs (started_at) VALUES (?)", (now,))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, now: int, items: int, changes: int, errors: list[str]) -> None:
        self.db.execute("UPDATE runs SET finished_at=?, items=?, changes=?, errors=? WHERE id=?",
                        (now, items, changes, "\n".join(errors), run_id))
        self.db.commit()

    def save_items(self, source: str, currency: str, items, now: int) -> list[Change]:
        """Сохраняет товары одного источника одной транзакцией. Возвращает изменения цен."""
        changes: list[Change] = []
        with self.db:  # или всё, или ничего
            for item in items:
                self.db.execute(
                    """INSERT INTO products (source, url, title, currency, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source, url) DO UPDATE SET title=excluded.title, last_seen=excluded.last_seen""",
                    (source, item.url, item.title, currency, now, now),
                )
                pid = self.db.execute("SELECT id FROM products WHERE source=? AND url=?",
                                      (source, item.url)).fetchone()["id"]
                last = self.db.execute(
                    "SELECT price_cents FROM prices WHERE product_id=? ORDER BY seen_at DESC, rowid DESC LIMIT 1",
                    (pid,),
                ).fetchone()
                if last is None or last["price_cents"] != item.price_cents:
                    self.db.execute("INSERT INTO prices (product_id, price_cents, seen_at) VALUES (?, ?, ?)",
                                    (pid, item.price_cents, now))
                if last is not None and last["price_cents"] != item.price_cents:
                    old = last["price_cents"]
                    changes.append(Change(source, item.title, item.url, currency, old, item.price_cents))
        return changes

    def table(self, now: int, stale_after: int = 3 * 86400) -> list[Row]:
        """Текущие цены всех товаров, которые видели за последние stale_after секунд."""
        rows = self.db.execute(
            """
            WITH ranked AS (
                SELECT product_id, price_cents, seen_at,
                       ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY seen_at DESC, rowid DESC) AS rn
                FROM prices
            )
            SELECT p.source, p.title, p.url, p.currency, p.last_seen,
                   cur.price_cents, cur.seen_at AS changed_at, prev.price_cents AS prev_cents,
                   (SELECT MIN(pc) FROM (
                        SELECT price_cents AS pc FROM prices x WHERE x.product_id = p.id AND x.seen_at >= :since
                        UNION ALL  -- цена, которая действовала на начало окна
                        SELECT * FROM (SELECT price_cents FROM prices y WHERE y.product_id = p.id
                                       AND y.seen_at < :since ORDER BY y.seen_at DESC LIMIT 1)
                   )) AS min_30d
            FROM products p
            JOIN ranked cur  ON cur.product_id = p.id AND cur.rn = 1
            LEFT JOIN ranked prev ON prev.product_id = p.id AND prev.rn = 2
            WHERE p.last_seen >= :fresh
            ORDER BY p.source, p.title
            """,
            {"since": now - 30 * 86400, "fresh": now - stale_after},
        ).fetchall()
        return [
            Row(r["source"], r["title"], r["url"], r["currency"], r["price_cents"], r["prev_cents"],
                min(r["min_30d"] or r["price_cents"], r["price_cents"]), r["changed_at"], r["last_seen"])
            for r in rows
        ]

    def recent_changes(self, since: int) -> list[tuple[int, Change]]:
        """Все изменения цен начиная с since: (когда, изменение), новые сверху."""
        rows = self.db.execute(
            """
            SELECT p.source, p.title, p.url, p.currency, cur.seen_at, cur.price_cents AS new_cents,
                   (SELECT price_cents FROM prices prev WHERE prev.product_id = cur.product_id
                     AND (prev.seen_at < cur.seen_at OR (prev.seen_at = cur.seen_at AND prev.rowid < cur.rowid))
                     ORDER BY prev.seen_at DESC, prev.rowid DESC LIMIT 1) AS old_cents
            FROM prices cur JOIN products p ON p.id = cur.product_id
            WHERE cur.seen_at >= ?
            ORDER BY cur.seen_at DESC, cur.rowid DESC
            """,
            (since,),
        ).fetchall()
        return [
            (r["seen_at"], Change(r["source"], r["title"], r["url"], r["currency"], r["old_cents"], r["new_cents"]))
            for r in rows if r["old_cents"] is not None
        ]

    def last_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
