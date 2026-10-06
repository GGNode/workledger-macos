from __future__ import annotations

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from .config import Config
from .store import Store
from .util import day_bounds, today, now, digest
from .adapters.bridge import ingest_bridge

MAX_BODY = 2 * 1024**2


def valid_host(host: str, port: int) -> bool:
    return host.lower() in {f"127.0.0.1:{port}", f"localhost:{port}"}


def valid_origin(origin: str | None, port: int) -> bool:
    if not origin:
        return True
    p = urlsplit(origin)
    return origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"} or (p.scheme in {"chrome-extension", "moz-extension"} and bool(p.netloc) and not p.path)


def make_server(config: Config, *, port: int | None = None):
    home = config.home
    class Handler(BaseHTTPRequestHandler):
        server_version = "WorkLedger/0.1"
        def log_message(self, fmt, *args):
            # No payloads, Authorization headers, query strings or tokens in access logs.
            logging.debug("HTTP %s", self.command)

        def send(self, status, value, content_type="application/json; charset=utf-8"):
            raw = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith("application/json") else (value.encode() if isinstance(value, str) else value)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
            origin = self.headers.get("Origin")
            if origin and valid_origin(origin, self.server.server_port):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(raw)

        def gate(self, auth=True):
            p = self.server.server_port
            if not valid_host(self.headers.get("Host", ""), p) or not valid_origin(self.headers.get("Origin"), p):
                self.send(403, {"error": "Host/origin rejected"})
                return False
            if auth:
                expected = "Bearer " + Config(home).token
                actual = self.headers.get("Authorization", "")
                if not hmac.compare_digest(actual, expected):
                    self.send(401, {"error": "Local pairing token required"})
                    return False
            return True

        def do_OPTIONS(self):
            if not self.gate(False):
                return
            self.send_response(204)
            origin = self.headers.get("Origin")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization,Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in {"/", "/app.js"}:
                if not self.gate(False):
                    return
                name = "index.html" if path == "/" else "app.js"
                self.send(200, (Path(__file__).parent / "static" / name).read_text(), "text/html; charset=utf-8" if path == "/" else "text/javascript; charset=utf-8")
                return
            if not self.gate():
                return
            cfg = Config(home)
            try:
                with Store(cfg.db_path) as store:
                    if path == "/api/status":
                        from .doctor import doctor
                        self.send(200, {**store.summary(), "last_capture": store.cache_get("last_capture"), "config": cfg.data, "home": str(home), "doctor": doctor(cfg)})
                    elif path == "/api/events":
                        day = parse_qs(urlsplit(self.path).query).get("date", [today(cfg.data["timezone"])])[0]
                        start, end = day_bounds(day, cfg.data["timezone"])
                        rows = store.events(start, end)
                        self.send(200, {"events": [e for e in rows if e["kind"] in {"document_change", "user_message", "review", "note"}][-250:]})
                    elif path == "/api/reports":
                        dates = sorted((p.name for p in cfg.reports.iterdir() if p.is_dir() and (p / "report.html").exists()), reverse=True)
                        self.send(200, {"dates": dates})
                    else:
                        self.send(404, {"error": "Not found"})
            except (ValueError, OSError) as e:
                self.send(400, {"error": str(e)})

        def do_POST(self):
            if not self.gate():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY:
                    self.send(413, {"error": "Request size out of bounds"})
                    return
                if "application/json" not in self.headers.get("Content-Type", ""):
                    self.send(415, {"error": "JSON required"})
                    return
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("JSON object required")
                cfg = Config(home)
                path = urlsplit(self.path).path
                if path == "/api/config":
                    allowed = {"timezone", "report_open", "poll_seconds", "projects", "sources", "llm", "schedule", "capture_paused", "exclude", "max_file_mb", "max_source_mb", "max_project_files"}
                    if not set(data) <= allowed:
                        raise ValueError("Unsupported setting; edit the local config file for port changes, then restart")
                    cfg.save(data)
                    self.send(200, {"saved": True})
                elif path == "/api/report":
                    from .runtime import capture_and_report
                    result = capture_and_report(cfg, day=data.get("date"), open_after=data.get("open", True))
                    self.send(200, {"path": str(result), "date": result.parent.name})
                elif path == "/api/open":
                    from .macos import open_output
                    date = data["date"]
                    day_bounds(date, cfg.data["timezone"])
                    self.send(200, open_output(cfg, cfg.reports / date / "report.html"))
                elif path == "/api/collect":
                    from .runtime import processing_lock
                    from .ingest import collect
                    with processing_lock(cfg), Store(cfg.db_path) as store:
                        result = collect(cfg, store, force=data.get("force", False))
                    self.send(200, result)
                elif path in {"/api/events", "/api/annotate", "/api/note"}:
                    with Store(cfg.db_path) as store, store.transaction():
                        if path == "/api/events":
                            rows = data.get("events", [])
                            if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
                                raise ValueError("events must contain 1..200 objects")
                            ids = ingest_bridge(rows, store)
                            result = {"accepted": len(ids), "ids": ids}
                        elif path == "/api/annotate":
                            ids = data.get("ids", [])
                            if not isinstance(ids, list) or not 1 <= len(ids) <= 250:
                                raise ValueError("Select 1..250 events")
                            for eid in ids:
                                store.annotate(eid, data.get("actor", "human"), data.get("reason", ""))
                            result = {"updated": len(ids)}
                        else:
                            text = str(data.get("text", "")).strip()
                            if not text or len(text) > 8000:
                                raise ValueError("Note must contain 1..8000 characters")
                            at = now()
                            eid = store.event("manual", digest([at, text]), "note", actor="human", occurred_at=at, text=text, evidence="explicit_user_note", metadata={"project": data.get("project", "其他工作")})
                            result = {"id": eid}
                    self.send(200, result)
                else:
                    self.send(404, {"error": "Not found"})
            except (ValueError, KeyError, TypeError, OSError) as e:
                self.send(400, {"error": str(e)})
            except Exception as e:
                logging.exception("API operation failed")
                self.send(500, {"error": "Operation failed: " + type(e).__name__})

    server = ThreadingHTTPServer(("127.0.0.1", config.data["port"] if port is None else port), Handler)
    server.daemon_threads = True
    return server
