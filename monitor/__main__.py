"""CLI.

python -m monitor run                    один прогон: парсинг → таблицы → алерт
python -m monitor run --every 30m        по кругу раз в 30 минут (для Docker)
python -m monitor run --demo-shuffle 6   для видео: сдвинуть цены у 6 товаров на каждом сайте (сами они не меняются)
python -m monitor check                  проверить селекторы: первая страница каждого источника
python -m monitor report                 пересобрать xlsx/html из базы без парсинга
"""
from __future__ import annotations

import argparse
import fcntl
import logging
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import alerts
from .config import Config, ConfigError, load
from .exporters import excel, gsheets, html_report
from .fetch import Fetcher, FetchError
from .money import fmt
from .parse import parse_page
from .run import run_once
from .storage import Storage

log = logging.getLogger("monitor")


def parse_interval(text: str) -> int:
    m = re.fullmatch(r"(\d+)\s*([smh])", text.strip())
    if not m:
        raise argparse.ArgumentTypeError("интервал вида 30m, 2h, 90s")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


def export_all(cfg: Config, storage: Storage, errors: list[str], now: int) -> str | None:
    rows = storage.table(now)
    history = storage.recent_changes(now - 30 * 86400)
    out = Path(cfg.output_dir)
    xlsx = excel.export(rows, history, out / "prices.xlsx")
    html = html_report.export(rows, history, errors, now, cfg.alert_drop_percent, out / "report.html")
    log.info("Таблицы: %s, %s", xlsx, html)
    if not cfg.sheets_enabled:
        return None
    try:
        url = gsheets.export(rows, history, cfg.google_credentials_file, cfg.spreadsheet_id)
        log.info("Google Sheets обновлена: %s", url)
        return url
    except Exception as e:  # noqa: BLE001 — таблица в облаке не должна ломать локальные файлы и алерт
        errors.append(f"Google Sheets: {type(e).__name__}: {e}")
        log.error("Google Sheets не обновилась: %s", e)
        return None


def cmd_run(cfg: Config, shuffle: int, no_alert: bool) -> int:
    storage = Storage(cfg.db_path)
    fetcher = Fetcher(cfg.user_agent, cfg.timeout_sec, cfg.request_delay_sec)
    try:
        now = int(time.time())
        result = run_once(cfg, storage, fetcher, now=now, shuffle=shuffle)
        sheet_url = export_all(cfg, storage, result.errors, now)
        log.info("Итого: %d товаров, %d изменений цен, ошибок: %d", result.total, len(result.changes),
                 len(result.errors))
        for e in result.errors:
            log.warning("  %s", e)

        message = alerts.build_message(result.changes, result.errors, cfg, result.total, sheet_url)
        if message and not no_alert:
            if cfg.telegram_enabled:
                try:
                    alerts.send_telegram(cfg.telegram_token, cfg.telegram_chat_id, message)
                    log.info("Алерт отправлен в Telegram")
                except RuntimeError as e:
                    log.error("Алерт не отправлен: %s", e)
            else:
                log.info("Telegram не настроен — алерт только в консоли:\n%s", re.sub(r"<[^>]+>", "", message))
        return 0 if result.total else 2  # 2 — не собрали ничего: пусть cron/Docker это заметит
    finally:
        fetcher.close()
        storage.close()


@contextmanager
def run_lock(output_dir: str) -> Iterator[bool]:
    """Замок на время одного прогона: второй не стартует, пока идёт первый (цикл --every + cron или
    ручной запуск, медленный сайт). Между прогонами замок свободен. Отдаёт False, если он занят."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(output_dir) / ".lock", "w") as fh:  # закрытие файла снимает замок
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


def cmd_check(cfg: Config) -> int:
    """Быстрая проверка селекторов после правки config.yaml."""
    fetcher = Fetcher(cfg.user_agent, cfg.timeout_sec, cfg.request_delay_sec)
    ok = True
    try:
        for s in cfg.sources:
            try:
                html = fetcher.get(s.start_url)
            except FetchError as e:
                print(f"✗ {s.name}: {e}")
                ok = False
                continue
            items, next_url, problems = parse_page(html, s.start_url, s)
            mark = "✓" if items else "✗"
            ok &= bool(items)
            print(f"{mark} {s.name}: {len(items)} товаров на первой странице, "
                  f"следующая: {next_url or 'нет'}, проблем: {len(problems)}")
            for it in items[:3]:
                print(f"    {fmt(it.price_cents, s.currency):>12}  {it.title[:70]}")
            for p in problems[:3]:
                print(f"    ! {p}")
    finally:
        fetcher.close()
    return 0 if ok else 1


def cmd_report(cfg: Config) -> int:
    storage = Storage(cfg.db_path)
    try:
        export_all(cfg, storage, [], int(time.time()))
    finally:
        storage.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monitor", description="Мониторинг цен конкурентов")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="собрать цены, обновить таблицы, прислать алерт")
    run.add_argument("--every", type=parse_interval, help="повторять с интервалом: 30m, 2h")
    run.add_argument("--demo-shuffle", type=int, default=0, metavar="N",
                     help="ТОЛЬКО ДЛЯ ДЕМО: сдвинуть цены у N случайных товаров на каждом сайте")
    run.add_argument("--no-alert", action="store_true", help="не слать алерт")
    sub.add_parser("check", help="проверить селекторы на первой странице каждого источника")
    sub.add_parser("report", help="пересобрать таблицы из базы без парсинга")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        cfg = load(args.config)
    except ConfigError as e:
        log.error("%s", e)
        return 1

    if args.cmd == "check":
        return cmd_check(cfg)
    if args.cmd == "report":
        return cmd_report(cfg)

    if not args.every:
        with run_lock(cfg.output_dir) as locked:
            if not locked:
                log.error("Предыдущий прогон ещё идёт, выходим")
                return 3
            return cmd_run(cfg, args.demo_shuffle, args.no_alert)
    while True:
        started = time.monotonic()
        with run_lock(cfg.output_dir) as locked:
            if not locked:
                log.warning("Идёт другой прогон (cron или ручной запуск), этот пропускаем")
            else:
                try:
                    cmd_run(cfg, args.demo_shuffle, args.no_alert)
                except Exception:  # noqa: BLE001 — цикл не должен умирать из-за одного плохого прогона
                    log.exception("Прогон упал, следующий по расписанию")
        time.sleep(max(0, args.every - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
