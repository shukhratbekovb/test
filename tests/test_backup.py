import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BackupTests(unittest.TestCase):
    def test_backup_is_integrity_checked_and_old_copies_are_pruned(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "data" / "workload.sqlite3"
            backup_directory = root / "backups"
            database.parent.mkdir()
            backup_directory.mkdir()
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE sample(value TEXT)")
                connection.execute("INSERT INTO sample(value) VALUES ('stored value')")
                connection.commit()

            old_backup = backup_directory / "workload-old.sqlite3"
            old_backup.write_bytes(b"expired")
            old_timestamp = (datetime.now() - timedelta(days=45)).timestamp()
            os.utime(old_backup, (old_timestamp, old_timestamp))
            environment = os.environ.copy()
            environment["DATABASE_PATH"] = str(database)
            environment["BACKUP_DIR"] = str(backup_directory)
            environment["BACKUP_RETENTION_DAYS"] = "30"

            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "backup.py")],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            backups = list(backup_directory.glob("workload-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            self.assertNotEqual(backups[0], old_backup)
            with closing(sqlite3.connect(backups[0])) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    connection.execute("SELECT value FROM sample").fetchone()[0],
                    "stored value",
                )


if __name__ == "__main__":
    unittest.main()
