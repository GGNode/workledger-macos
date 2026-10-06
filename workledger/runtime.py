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
def processing_lock(config: Config, name="capture"):
    path = config.home / (name + ".lock")
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


def capture_and_report(config: Config, *, day=None, open_after=None, refresh_analysis=False, collect_first=True):
    from .ingest import collect
    from .report import write_report
    from .macos import open_output
    # Separate report serialization from collection: slow inference never holds
    # capture.lock or an open write transaction.
    with processing_lock(config, "report"):
        if collect_first:
            with processing_lock(config), Store(config.db_path) as store:
                collect(config, store)
        with Store(config.db_path) as store:
            path = write_report(config, store, day, refresh_analysis=refresh_analysis)
    if open_after if open_after is not None else config.data["report_open"]:
        open_output(config, path)
    return path


def worker(home: Path, stop: threading.Event):
    from .ingest import collect
    from .macos import open_output
    reporting = None
    def scheduled_report(days):
        last = None
        try:
            for day in days:
                if stop.is_set():
                    break
                cfg = Config(home)
                if not cfg.data["schedule"]["enabled"]:
                    break
                last = capture_and_report(cfg, day=day, open_after=False, collect_first=False)
                with Store(cfg.db_path) as store:
                    # A degraded report is still a delivered report; no automatic
                    # repeated paid inference until the user explicitly regenerates.
                    store.cache_set("scheduled:" + day, now())
                    pending = store.cache_get("pending_report_dates", [])
                    store.cache_set("pending_report_dates", [d for d in pending if d != day])
            cfg = Config(home)
            if last and cfg.data["schedule"]["open"] and not stop.is_set():
                open_output(cfg, last)
        except Exception:
            logging.exception("Scheduled REPORT failed; existing reports preserved")
    while not stop.is_set():
        delay = 30
        try:
            cfg = Config(home)
            delay = cfg.data["poll_seconds"]
            with processing_lock(cfg), Store(cfg.db_path) as store:
                collect(cfg, store)
                due = sorted(set(due_dates(cfg, store, now())) | set(store.cache_get("pending_report_dates", [])))
                if not cfg.data["schedule"]["enabled"]:
                    due = []
                store.cache_set("pending_report_dates", due)
                store.cache_set("daemon_previous_tick", now())
            if due and (reporting is None or not reporting.is_alive()):
                reporting = threading.Thread(target=scheduled_report, args=(due,), daemon=True, name="workledger-report")
                reporting.start()
        except Exception:
            logging.exception("COLLECTOR iteration failed; retrying later")
        stop.wait(delay)
