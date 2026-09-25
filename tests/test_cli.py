"""CLI: замок от двойного запуска держится только на время прогона, а не всё время работы цикла --every."""
from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from monitor import __main__ as cli
from monitor.config import Config, Source

SHOP = Source(name="Shop", start_url="https://shop.test/", item=".card", title=".t", price=".p", link="a@href")


class LoopStopped(Exception):
    """Выход из бесконечного цикла --every в тесте."""


def stop_loop(seconds: float) -> None:
    raise LoopStopped


@pytest.fixture
def runs(monkeypatch, tmp_path) -> list[int]:
    """Настоящий main() с папкой output во временной директории. Вместо парсинга сайтов прогон
    записывает свой --demo-shuffle в список, чтобы было видно, стартовал ли он."""
    cfg = Config(sources=[SHOP], output_dir=str(tmp_path), db_path=str(tmp_path / "prices.db"))
    monkeypatch.setattr(cli, "load", lambda path: cfg)
    started: list[int] = []
    monkeypatch.setattr(cli, "cmd_run", lambda cfg, shuffle, no_alert: started.append(shuffle) or 0)
    return started


def lock_is_free(folder: Path) -> bool:
    """Проверка глазами второго процесса: своё открытие файла и захват без ожидания."""
    with open(folder / ".lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def test_loop_frees_the_lock_while_waiting_for_the_next_run(runs, monkeypatch, tmp_path):
    # Иначе ручной прогон (cron со сдвигом цен, запуск для видео) всегда слышит «прогон ещё идёт».
    free_while_sleeping = []

    def sleep(seconds: float) -> None:
        free_while_sleeping.append(lock_is_free(tmp_path))
        raise LoopStopped

    monkeypatch.setattr(cli.time, "sleep", sleep)
    with pytest.raises(LoopStopped):
        cli.main(["run", "--every", "60m"])
    assert runs == [0]
    assert free_while_sleeping == [True]


def test_loop_skips_its_slot_while_a_manual_run_holds_the_lock(runs, monkeypatch, tmp_path):
    monkeypatch.setattr(cli.time, "sleep", stop_loop)
    with open(tmp_path / ".lock", "w") as manual_run:
        fcntl.flock(manual_run, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(LoopStopped):  # цикл ждёт следующего часа, а не завершается
            cli.main(["run", "--every", "60m"])
    assert runs == []


def test_one_off_run_exits_while_another_run_holds_the_lock(runs, tmp_path):
    with open(tmp_path / ".lock", "w") as other_run:
        fcntl.flock(other_run, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert cli.main(["run", "--demo-shuffle", "3"]) == 3
    assert runs == []
