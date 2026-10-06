from __future__ import annotations

import fcntl
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo
from .config import Config
from .store import Store
from .util import now, stamp


@contextmanager
def processing_lock(config: Config):
    path = config.home / "capture.lock"
    with path.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def due_dates(config: Config, store: Store, current: str) -> list[str]:
    opts = config.data["schedule"]
    if not opts["enabled"]:
        return []
    tz = ZoneInfo(config.data["timezone"])
    dt = datetime.fromisoformat(stamp(current)).astimezone(tz)
    previous = store.cache_get("daemon_previous_tick")
    first = datetime.fromisoformat(previous).astimezone(tz).date() if previous else dt.date()
    first = max(first, dt.date() - timedelta(days=3))
    hour, minute = map(int, opts["time"].split(":"))
    days = []
    d = first
    while d <= dt.date():
        when = datetime.combine(d, time(hour, minute), tz)
        if when <= dt and (not opts["weekdays_only"] or d.weekday() < 5) and not store.cache_get("scheduled:" + d.isoformat()):
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def capture_and_report(config: Config, *, day=None, open_after=None):
    from .ingest import collect
    from .report import write_report
    from .macos import open_output
    with processing_lock(config), Store(config.db_path) as store:
        collect(config, store)
        path = write_report(config, store, day)
        store.conn.commit()
    if open_after if open_after is not None else config.data["report_open"]:
        open_output(config, path)
    return path


def worker(home: Path, stop: threading.Event):
    from .ingest import collect
    from .report import write_report
    from .macos import open_output
    while not stop.is_set():
        delay = 30
        try:
            cfg = Config(home)
            delay = cfg.data["poll_seconds"]
            with processing_lock(cfg), Store(cfg.db_path) as store:
                collect(cfg, store)
                due = due_dates(cfg, store, now())
                last = None
                for day in due:
                    last = write_report(cfg, store, day)
                    store.cache_set("scheduled:" + day, now())
                store.cache_set("daemon_previous_tick", now())
                store.conn.commit()
            if last and cfg.data["schedule"]["open"]:
                open_output(cfg, last)
        except Exception:
            logging.exception("Collector iteration failed; retrying later")
        stop.wait(delay)
