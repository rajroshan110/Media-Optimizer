"""SQLite-based resume journal and batch status tracking."""

import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class BatchSummary:
    total_files: int
    completed: int
    skipped: int
    failed: int
    original_bytes: int
    optimized_bytes: int
    saved_bytes: int
    reduction_percent: float

    def format_report(self) -> str:
        """Format an informative summary report."""
        from media_optimizer.utils import format_bytes

        return (
            f"----------------------------------------\n"
            f"BATCH OPTIMIZATION REPORT\n"
            f"----------------------------------------\n"
            f"Files processed:      {self.completed + self.skipped + self.failed:,}\n"
            f"  - Completed:        {self.completed:,}\n"
            f"  - Skipped/Copied:   {self.skipped:,}\n"
            f"  - Failed:           {self.failed:,}\n"
            f"Original size:        {format_bytes(self.original_bytes)}\n"
            f"Optimized size:       {format_bytes(self.optimized_bytes)}\n"
            f"Space saved:          {format_bytes(self.saved_bytes)}\n"
            f"Reduction:            {self.reduction_percent:.1f}%\n"
            f"----------------------------------------"
        )




class Journal:
    """Thread-safe SQLite journal for resuming interrupted batches."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        rel_path TEXT PRIMARY KEY,
                        orig_size INTEGER NOT NULL,
                        opt_size INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        orig_mtime REAL NOT NULL,
                        reason TEXT,
                        error_msg TEXT,
                        updated_at REAL NOT NULL
                    );
                """)
                conn.commit()

    def is_already_done(self, rel_path: str, current_mtime: float, current_size: int, dest_file: Path) -> bool:
        """Check if file has already been successfully processed and unchanged."""
        target_file = dest_file
        if not target_file.exists():
            # Check if a non-mp4 video was converted to .mp4
            alt_mp4 = dest_file.with_suffix(".mp4")
            if alt_mp4.exists():
                target_file = alt_mp4
            else:
                return False

        if target_file.stat().st_size == 0:
            return False

        with self._lock:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT status, orig_mtime, orig_size FROM tasks WHERE rel_path = ?",
                    (rel_path,)
                )
                row = cur.fetchone()
                if not row:
                    return False
                status, prev_mtime, prev_size = row
                if status in ("COMPLETED", "SKIPPED"):
                    # Check if file has been modified since previous run
                    if abs(prev_mtime - current_mtime) < 0.001 and prev_size == current_size:
                        return True
        return False

    def record_completed(self, rel_path: str, orig_size: int, opt_size: int, mtime: float, reason: str) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks 
                    (rel_path, orig_size, opt_size, status, orig_mtime, reason, error_msg, updated_at)
                    VALUES (?, ?, ?, 'COMPLETED', ?, ?, NULL, ?)
                """, (rel_path, orig_size, opt_size, mtime, reason, time.time()))
                conn.commit()

    def record_skipped(self, rel_path: str, orig_size: int, opt_size: int, mtime: float, reason: str) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks 
                    (rel_path, orig_size, opt_size, status, orig_mtime, reason, error_msg, updated_at)
                    VALUES (?, ?, ?, 'SKIPPED', ?, ?, NULL, ?)
                """, (rel_path, orig_size, opt_size, mtime, reason, time.time()))
                conn.commit()

    def record_failed(self, rel_path: str, orig_size: int, mtime: float, error_msg: str) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks 
                    (rel_path, orig_size, opt_size, status, orig_mtime, reason, error_msg, updated_at)
                    VALUES (?, ?, ?, 'FAILED', ?, NULL, ?, ?)
                """, (rel_path, orig_size, orig_size, mtime, error_msg, time.time()))
                conn.commit()

    def get_summary(self) -> BatchSummary:
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT status, orig_size, opt_size FROM tasks")
                rows = cur.fetchall()

                completed = 0
                skipped = 0
                failed = 0
                orig_total = 0
                opt_total = 0

                for status, orig_s, opt_s in rows:
                    orig_total += orig_s
                    opt_total += opt_s
                    if status == "COMPLETED":
                        completed += 1
                    elif status == "SKIPPED":
                        skipped += 1
                    elif status == "FAILED":
                        failed += 1

                saved = max(0, orig_total - opt_total)
                ratio = (saved / orig_total * 100.0) if orig_total > 0 else 0.0

                return BatchSummary(
                    total_files=len(rows),
                    completed=completed,
                    skipped=skipped,
                    failed=failed,
                    original_bytes=orig_total,
                    optimized_bytes=opt_total,
                    saved_bytes=saved,
                    reduction_percent=ratio,
                )
