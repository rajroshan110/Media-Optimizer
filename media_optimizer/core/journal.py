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
    res_saved_bytes: int = 0
    codec_saved_bytes: int = 0

    def format_report(self) -> str:
        """Format an informative summary report."""
        from media_optimizer.utils import format_bytes

        breakdown = ""
        if self.saved_bytes > 0 and (self.res_saved_bytes > 0 or self.codec_saved_bytes > 0):
            breakdown = (
                f"  ├─ Spatial Resizing: {format_bytes(self.res_saved_bytes)}\n"
                f"  └─ Codec Efficiency: {format_bytes(self.codec_saved_bytes)}\n"
            )

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
            f"{breakdown}"
            f"Reduction:            {self.reduction_percent:.1f}%\n"
            f"----------------------------------------"
        )




import hashlib


def compute_file_fingerprint(path: Path) -> str:
    """Compute a fast content fingerprint using first 64KB and last 64KB (or full file if small)."""
    try:
        if not path.exists():
            return ""
        size = path.stat().st_size
        if size == 0:
            return "empty"
        chunk = 64 * 1024
        h = hashlib.sha256()
        with open(path, "rb") as f:
            if size <= chunk * 2:
                h.update(f.read())
            else:
                h.update(f.read(chunk))
                f.seek(size - chunk)
                h.update(f.read(chunk))
        return h.hexdigest()[:32]
    except Exception:
        return ""


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
                for col in ("config_hash TEXT DEFAULT ''", "src_path TEXT DEFAULT ''", "fingerprint TEXT DEFAULT ''"):
                    try:
                        conn.execute(f"ALTER TABLE tasks ADD COLUMN {col}")
                    except sqlite3.OperationalError:
                        pass
                conn.commit()

    def is_already_done(self, task: Any, config_hash: str, output_dir: Optional[Path] = None, deep_mode: bool = False) -> Optional[Tuple[str, int, int]]:
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
                    "SELECT status, rel_path, orig_size, orig_mtime, fingerprint FROM tasks WHERE src_path = ? AND config_hash = ? ORDER BY updated_at DESC",
                    (src_path_str, config_hash)
                )
                rows = cur.fetchall()
                for status, saved_rel_path, orig_size, orig_mtime, saved_fp in rows:
                    if status in ("COMPLETED", "SKIPPED"):
                        # Invalidate cache if source file was modified or replaced
                        task_sz = getattr(task, "size_bytes", None)
                        task_mt = getattr(task, "mtime", None)
                        if task_sz is not None and task_sz != orig_size:
                            continue
                        if task_mt is not None and orig_mtime is not None:
                            if abs(task_mt - orig_mtime) > 1.0:
                                continue

                        # Invalidate cache if content fingerprint doesn't match (deep mode only)
                        if deep_mode and saved_fp:
                            current_fp = compute_file_fingerprint(task.src_path)
                            if current_fp and current_fp != saved_fp:
                                continue

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

    def record_completed(self, rel_path: str, orig_size: int, opt_size: int, mtime: float, reason: str, config_hash: str, src_path: str, deep_mode: bool = False) -> None:
        fp = compute_file_fingerprint(Path(src_path)) if deep_mode else ""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks 
                    (rel_path, orig_size, opt_size, status, orig_mtime, reason, error_msg, updated_at, config_hash, src_path, fingerprint)
                    VALUES (?, ?, ?, 'COMPLETED', ?, ?, NULL, ?, ?, ?, ?)
                """, (rel_path, orig_size, opt_size, mtime, reason, time.time(), config_hash, src_path, fp))
                conn.commit()

    def record_skipped(self, rel_path: str, orig_size: int, mtime: float, reason: str, config_hash: str, src_path: str, deep_mode: bool = False) -> None:
        fp = compute_file_fingerprint(Path(src_path)) if deep_mode else ""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks 
                    (rel_path, orig_size, opt_size, status, orig_mtime, reason, error_msg, updated_at, config_hash, src_path, fingerprint)
                    VALUES (?, ?, ?, 'SKIPPED', ?, ?, NULL, ?, ?, ?, ?)
                """, (rel_path, orig_size, orig_size, mtime, reason, time.time(), config_hash, src_path, fp))
                conn.commit()

    def record_failed(self, rel_path: str, orig_size: int, mtime: float, error_msg: str, config_hash: str, src_path: str, deep_mode: bool = False) -> None:
        fp = compute_file_fingerprint(Path(src_path)) if deep_mode else ""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks 
                    (rel_path, orig_size, opt_size, status, orig_mtime, reason, error_msg, updated_at, config_hash, src_path, fingerprint)
                    VALUES (?, ?, 0, 'FAILED', ?, NULL, ?, ?, ?, ?, ?)
                """, (rel_path, orig_size, mtime, error_msg, time.time(), config_hash, src_path, fp))
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
