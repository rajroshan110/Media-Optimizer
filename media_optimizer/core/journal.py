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
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN config_hash TEXT DEFAULT ''")
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN src_path TEXT DEFAULT ''")
                except sqlite3.OperationalError:
                    pass
                conn.commit()

    def is_already_done(self, task: Any, config_hash: str, output_dir: Optional[Path] = None) -> Optional[Tuple[str, int, int]]:
        """Check if file has already been successfully processed with this config.
        Returns (status, orig_size, opt_size) if done, else None.
        """
        if output_dir is None:
            output_dir = self.db_path.parent

        src_path_str = str(task.src_path.resolve())
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT status, rel_path, orig_size FROM tasks WHERE src_path = ? AND config_hash = ? ORDER BY updated_at DESC",
                    (src_path_str, config_hash)
                )
                rows = cur.fetchall()
                for status, saved_rel_path, orig_size in rows:
                    if status in ("COMPLETED", "SKIPPED"):
                        dest_file = output_dir / saved_rel_path
                        if not dest_file.exists():
                            alt_mp4 = dest_file.with_suffix(".mp4")
                            if alt_mp4.exists():
                                dest_file = alt_mp4
                            else:
                                continue

                        try:
                            sz = dest_file.stat().st_size
                            if sz > 0:
                                return (status, orig_size, sz)
                        except OSError:
                            continue
        return None
        
    def has_record(self, rel_path: str) -> bool:
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM tasks WHERE rel_path = ?", (rel_path,))
                return cur.fetchone() is not None

    def record_completed(self, rel_path: str, orig_size: int, opt_size: int, mtime: float, reason: str, config_hash: str, src_path: str) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks 
                    (rel_path, orig_size, opt_size, status, orig_mtime, reason, error_msg, updated_at, config_hash, src_path)
                    VALUES (?, ?, ?, 'COMPLETED', ?, ?, NULL, ?, ?, ?)
                """, (rel_path, orig_size, opt_size, mtime, reason, time.time(), config_hash, src_path))
                conn.commit()

    def record_skipped(self, rel_path: str, orig_size: int, mtime: float, reason: str, config_hash: str, src_path: str) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks 
                    (rel_path, orig_size, opt_size, status, orig_mtime, reason, error_msg, updated_at, config_hash, src_path)
                    VALUES (?, ?, ?, 'SKIPPED', ?, ?, NULL, ?, ?, ?)
                """, (rel_path, orig_size, orig_size, mtime, reason, time.time(), config_hash, src_path))
                conn.commit()

    def record_failed(self, rel_path: str, orig_size: int, mtime: float, error_msg: str, config_hash: str, src_path: str) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks 
                    (rel_path, orig_size, opt_size, status, orig_mtime, reason, error_msg, updated_at, config_hash, src_path)
                    VALUES (?, ?, 0, 'FAILED', ?, NULL, ?, ?, ?, ?)
                """, (rel_path, orig_size, mtime, error_msg, time.time(), config_hash, src_path))
                conn.commit()

    def get_summary(self) -> BatchSummary:
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT status, orig_size, opt_size, rel_path FROM tasks")
                rows = cur.fetchall()

                completed = 0
                skipped = 0
                failed = 0
                orig_total = 0
                opt_total = 0
                
                out_dir = self.db_path.parent

                for status, orig_s, opt_s, rel_path in rows:
                    if status in ("COMPLETED", "SKIPPED"):
                        # Only count files that still exist on disk
                        p = out_dir / rel_path
                        if not p.exists():
                            alt_mp4 = p.with_suffix(".mp4")
                            if not alt_mp4.exists():
                                continue
                            else:
                                p = alt_mp4
                        
                        # Use actual file size for absolute accuracy
                        try:
                            actual_sz = p.stat().st_size
                            opt_total += actual_sz
                            orig_total += orig_s
                            if status == "COMPLETED":
                                completed += 1
                            else:
                                skipped += 1
                        except OSError:
                            continue
                    elif status == "FAILED":
                        failed += 1
                        orig_total += orig_s

                saved = max(0, orig_total - opt_total)
                ratio = (saved / orig_total * 100.0) if orig_total > 0 else 0.0

                return BatchSummary(
                    total_files=completed + skipped + failed,
                    completed=completed,
                    skipped=skipped,
                    failed=failed,
                    original_bytes=orig_total,
                    optimized_bytes=opt_total,
                    saved_bytes=saved,
                    reduction_percent=ratio,
                )
