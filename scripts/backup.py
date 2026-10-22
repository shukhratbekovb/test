from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def configured_path(variable: str, fallback: str) -> Path:
    value = Path(os.environ.get(variable, fallback))
    return value if value.is_absolute() else ROOT / value


def main() -> int:
    source = configured_path("DATABASE_PATH", "data/workload.sqlite3")
    backup_dir = configured_path("BACKUP_DIR", "backups")
    if not source.is_file():
        print(f"Database not found: {source}", file=sys.stderr)
        return 1

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"workload-{timestamp}.sqlite3"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".workload-", suffix=".sqlite3", dir=backup_dir
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(source, timeout=30)) as source_db:
            with closing(sqlite3.connect(temporary)) as backup_db:
                source_db.backup(backup_db)
                result = backup_db.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise sqlite3.DatabaseError(f"Backup integrity check failed: {result}")
        os.replace(temporary, destination)
        retention_days = max(1, int(os.environ.get("BACKUP_RETENTION_DAYS", "30")))
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        for old_backup in backup_dir.glob("workload-*.sqlite3"):
            if old_backup == destination:
                continue
            modified = datetime.fromtimestamp(old_backup.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                old_backup.unlink()
        print(f"Verified backup created: {destination}")
        return 0
    except Exception as error:
        temporary.unlink(missing_ok=True)
        print(f"Backup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
