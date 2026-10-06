from __future__ import annotations

import argparse
import fcntl
import json
import logging
import logging.handlers
import os
import signal
import sys
import subprocess
import time
import threading
import webbrowser
from pathlib import Path
from .config import Config
from .store import Store
from .util import now, digest


def main(argv=None):
    parser = argparse.ArgumentParser(prog="workledger", description="本机工作日报：人、Agent、文档和网页会话")
    parser.add_argument("--home", type=Path, help="独立数据目录；默认 ~/Library/Application Support/WorkLedger")
    subs = parser.add_subparsers(dest="cmd", required=True)
    subs.add_parser("init", help="初始化设置")
    subs.add_parser("doctor", help="检查本机路径及能力")
    c = subs.add_parser("collect", help="手动采集一次")
    c.add_argument("--force", action="store_true", help="重新解析未变化日志（稳定ID去重）")
    r = subs.add_parser("report", help="采集并生成报告")
    r.add_argument("--date", help="YYYY-MM-DD，按已配置的本机时区")
    r.add_argument("--no-open", action="store_true")
    r.add_argument("--open", action="store_true")
    subs.add_parser("ui", help="打开本机控制面板；未运行时以前台方式启动服务")
    subs.add_parser("daemon", help="持续采集、定时报告及本机控制面板")
    subs.add_parser("pair", help="显示浏览器扩展配对信息；不要分享配对码")
    imp = subs.add_parser("import", help="导入规范事件或 ChatGPT 官方导出")
    imp.add_argument("path", type=Path)
    imp.add_argument("--format", choices=["bridge", "chatgpt", "opencode"], default="bridge")
    anno = subs.add_parser("confirm", help="确认某条记录的归属，保留审计历史")
    anno.add_argument("event_id", nargs="+")
    anno.add_argument("--actor", choices=["human", "agent", "unknown"], default="human")
    anno.add_argument("--reason", required=True)
    link = subs.add_parser("link", help="明确指定已知主/子会话关系，保留原因")
    link.add_argument("--source", required=True)
    link.add_argument("--child", required=True)
    link.add_argument("--parent", required=True)
    link.add_argument("--relation", choices=["delegation","fork","lineage"], required=True)
    link.add_argument("--reason", required=True)
    note = subs.add_parser("note", help="补充本人完成的工作或结论")
    note.add_argument("text")
    note.add_argument("--project", default="其他工作")
    service = subs.add_parser("service", help="安装/卸载 macOS 登录后台采集")
    service.add_argument("action", choices=["install", "uninstall"])
    service.add_argument("--executable", default=str(Path.home() / ".local/bin/workledger"))
    demo = subs.add_parser("demo", help="在独立目录生成演示报告，不污染真实数据")
    demo.add_argument("--output", type=Path, default=Path.cwd() / "workledger-demo")
    demo.add_argument("--no-open", action="store_true")
    hook = subs.add_parser("hook", help="读取 Claude hook stdin 并排入本地队列")
    hook.add_argument("--source", choices=["claude"], default="claude")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.cmd == "demo":
            from .demo import create_demo
            path = create_demo(args.output)
            print(path)
            if sys.platform == "darwin" and not args.no_open:
                subprocess.run(["/usr/bin/open", str(path.parent), str(path)], check=True)
            return 0
        cfg = Config(args.home)
        if args.cmd == "init":
            print(f"设置：{cfg.path}\n报告：{cfg.reports}\n运行 workledger ui 打开控制面板。")
        elif args.cmd == "doctor":
            from .doctor import doctor
            print(json.dumps(doctor(cfg), ensure_ascii=False, indent=2))
        elif args.cmd == "pair":
            print(json.dumps({"url": f"http://127.0.0.1:{cfg.data['port']}", "token": cfg.token}, indent=2))
        elif args.cmd == "collect":
            from .runtime import processing_lock
            from .ingest import collect
            with processing_lock(cfg), Store(cfg.db_path) as store:
                print(json.dumps(collect(cfg, store, force=args.force), ensure_ascii=False, indent=2))
        elif args.cmd == "report":
            from .runtime import capture_and_report
            opening = False if args.no_open else True if args.open else None
            print(capture_and_report(cfg, day=args.date, open_after=opening))
        elif args.cmd == "import":
            from .adapters.bridge import ingest_bridge, import_chatgpt_export
            from .adapters.opencode import parse_opencode_export
            from .ingest import read_rows
            if args.path.stat().st_size > cfg.data["max_source_mb"] * 1024**2:
                raise ValueError("Import exceeds size limit")
            with Store(cfg.db_path) as store, store.transaction():
                if args.format == "bridge":
                    rows, warnings = read_rows(args.path, cfg.data["max_source_mb"] * 1024**2)
                    ingest_bridge(rows, store)
                    for warning in warnings:
                        store.issue("manual-import:" + str(args.path), "bridge", warning)
                elif args.format == "chatgpt":
                    import_chatgpt_export(json.loads(args.path.read_text()), store)
                else:
                    parse_opencode_export(json.loads(args.path.read_text()), store)
                print(json.dumps(store.summary(), ensure_ascii=False))
        elif args.cmd == "confirm":
            with Store(cfg.db_path) as store, store.transaction():
                for eid in args.event_id:
                    store.annotate(eid, args.actor, args.reason)
            print(f"已记录 {len(args.event_id)} 条归属确认。重新生成日报即可看到变化。")
        elif args.cmd == "link":
            with Store(cfg.db_path) as store, store.transaction():
                sid = store.session(args.source, args.child, parent=args.parent, relation=args.relation, evidence="explicit_session_link", metadata={"link_reason":args.reason,"linked_at":now()})
                print(sid)
        elif args.cmd == "note":
            with Store(cfg.db_path) as store, store.transaction():
                stamp = now()
                print(store.event("manual", digest([stamp, args.text]), "note", actor="human", occurred_at=stamp, text=args.text, evidence="explicit_user_note", metadata={"project": args.project}))
        elif args.cmd == "service":
            from .macos import install_launchd, uninstall_launchd
            print(install_launchd(cfg, args.executable) if args.action == "install" else uninstall_launchd())
        elif args.cmd == "hook":
            from .hooks import record_hook
            record_hook(cfg, json.loads(sys.stdin.buffer.read(2 * 1024**2)))
        elif args.cmd in {"ui", "daemon"}:
            from .server import make_server
            url = f"http://127.0.0.1:{cfg.data['port']}/#token={cfg.token}"
            if args.cmd == "ui":
                import urllib.request
                try:
                    req = urllib.request.Request(url.split("#")[0] + "api/status", headers={"Authorization": "Bearer " + cfg.token})
                    with urllib.request.urlopen(req, timeout=2) as response:
                        if response.status == 200:
                            webbrowser.open(url)
                            return 0
                except OSError:
                    if (cfg.home / "daemon.lock").exists():
                        for _ in range(12):
                            time.sleep(0.25)
                            try:
                                with urllib.request.urlopen(req, timeout=1) as response:
                                    if response.status == 200:
                                        webbrowser.open(url)
                                        return 0
                            except OSError:
                                continue
            lock = (cfg.home / "daemon.lock").open("a")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Daemon already running. Use workledger ui; check port/config if pairing fails.")
            handler = logging.handlers.RotatingFileHandler(cfg.home / "logs/runtime.log", maxBytes=2_000_000, backupCount=3)
            logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
            server = make_server(cfg)
            stop = threading.Event()
            from .runtime import worker
            thread = threading.Thread(target=worker, args=(cfg.home, stop), daemon=True)
            thread.start()
            def shutdown(*_):
                stop.set()
                threading.Thread(target=server.shutdown, daemon=True).start()
            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)
            if args.cmd == "ui":
                webbrowser.open(url)
            print(f"WorkLedger running at http://127.0.0.1:{server.server_port}; Ctrl+C to stop", flush=True)
            try:
                server.serve_forever()
            finally:
                stop.set()
                server.server_close()
                thread.join(timeout=10)
                lock.close()
        return 0
    except (ValueError, OSError, KeyError, RuntimeError, subprocess.SubprocessError) as e:
        print(f"WorkLedger: {e}", file=sys.stderr)
        return 2
