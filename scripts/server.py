"""Production entry point shared by Windows Task Scheduler and manual startup."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_environment(path: Path) -> None:
    # Deliberately the same simple KEY=value format as backup.ps1 (no shell syntax).
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip().isidentifier():
            raise RuntimeError(f"Invalid .env setting at line {number}")
        os.environ[key.strip()] = value.strip()


def main() -> None:
    load_environment(ROOT / ".env")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "server.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[handler, logging.StreamHandler()],
    )
    sys.path.insert(0, str(ROOT))
    try:
        from app import app
        from waitress import serve

        if "--check" in sys.argv:
            with app.test_client() as client:
                if client.get("/healthz").status_code != 200:
                    raise RuntimeError("Database health check failed")
            print("Configuration and database OK")
            return
        serve(app, host=os.environ.get("HOST", "127.0.0.1"),
              port=int(os.environ.get("PORT", "8080")), threads=8)
    except Exception:
        logging.exception("Application startup or server failure")
        raise


if __name__ == "__main__":
    main()
