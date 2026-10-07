"""Windows reporting task entry point; never starts trading executors."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SERVICES = {"portfolio": "dashboard.supabase_publisher", "news": "dashboard.terminal_news"}


class ReporterAlreadyRunning(RuntimeError):
    pass


@contextmanager
def reporter_instance(service: str, directory: Path | None = None):
    """OS releases the lock on exit/crash; no stale PID-file deletion needed."""
    if service not in SERVICES:
        raise ValueError("Unknown reporting service")
    directory = directory or ROOT / "data"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f".reporting-{service}.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ReporterAlreadyRunning(f"{service} reporter already running") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class LogStream:
    def write(self, text: str) -> int:
        if text.strip():
            logging.getLogger("reporting.output").info(text.rstrip())
        return len(text)

    def flush(self) -> None:
        for handler in logging.getLogger().handlers:
            handler.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=SERVICES)
    args = parser.parse_args()
    (ROOT / "data").mkdir(exist_ok=True)
    handler = RotatingFileHandler(ROOT / "data" / f"reporting-{args.service}.log",
                                  maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(message)s", force=True)
    from core.main_executor import load_local_env
    import certifi
    load_local_env()
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    sys.argv = [sys.argv[0], "--watch"]
    with redirect_stdout(LogStream()), redirect_stderr(LogStream()):
        logging.info("Starting %s reporting task; trading executors are not managed", args.service)
        try:
            importlib.import_module(SERVICES[args.service]).main()
        except Exception:
            logging.exception("Reporting task exited unexpectedly")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
