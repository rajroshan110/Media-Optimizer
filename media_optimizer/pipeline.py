"""Batch processing pipeline for scanning, queuing, and executing media optimization."""

import concurrent.futures
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple, Union

from media_optimizer.config import OptimizerConfig, get_default_config
from media_optimizer.core.analyzer import (
    DecisionAction,
    MediaType,
    analyze_file,
)
from media_optimizer.core.image_opt import optimize_image
from media_optimizer.core.journal import BatchSummary, Journal
from media_optimizer.core.video_opt import optimize_video


from media_optimizer.utils import format_bytes


@dataclass
class TaskItem:
    src_path: Path
    rel_path: str
    dst_path: Path
    media_type: MediaType
    size_bytes: int
    mtime: float


class OptimizationPipeline:
    """Pipeline managing file discovery, decision dispatch, and worker execution."""

    def __init__(self, config: Optional[OptimizerConfig] = None):
        self.config = config or get_default_config()
        self._stop_requested = threading.Event()
        self._video_semaphore = threading.BoundedSemaphore(self.config.video_workers)

    def stop(self) -> None:
        """Signal the pipeline to gracefully stop."""
        self._stop_requested.set()

    def is_stopped(self) -> bool:
        return self._stop_requested.is_set()

    def scan_directory(self, input_dir: Path, output_dir: Path) -> List[TaskItem]:
        """Recursively scan input directory for supported media files."""
        tasks: List[TaskItem] = []
        input_dir = input_dir.resolve()
        output_dir = output_dir.resolve()

        for root, dirs, files in os.walk(input_dir):
            # Ignore hidden directories like .git, .Trash
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for f in files:
                if f.startswith(".") or f.startswith("._"):
                    continue

                src_file = Path(root) / f
                ext = src_file.suffix.lower()

                if ext in self.config.image_extensions:
                    media_type = MediaType.IMAGE
                elif ext in self.config.video_extensions:
                    media_type = MediaType.VIDEO
                else:
                    continue

                rel_path = str(src_file.relative_to(input_dir))
                dst_file = output_dir / rel_path

                # Absolute safety: if destination file equals source file, append _optimized
                if src_file.resolve() == dst_file.resolve():
                    dst_file = dst_file.parent / f"{src_file.stem}_optimized{src_file.suffix}"
                    rel_path = str(dst_file.relative_to(output_dir))

                try:
                    stat = src_file.stat()
                    tasks.append(TaskItem(
                        src_path=src_file,
                        rel_path=rel_path,
                        dst_path=dst_file,
                        media_type=media_type,
                        size_bytes=stat.st_size,
                        mtime=stat.st_mtime,
                    ))
                except Exception:
                    continue

        return tasks

    def create_tasks(
        self,
        input_target: Union[Path, str, Sequence[Union[Path, str]]],
        output_dir: Path
    ) -> Tuple[List[TaskItem], str]:
        """Create task items from a directory, single file, or list of files."""
        tasks: List[TaskItem] = []
        output_dir = output_dir.resolve()

        if isinstance(input_target, (list, tuple)):
            for item in input_target:
                src_file = Path(item).resolve()
                if src_file.is_file():
                    ext = src_file.suffix.lower()
                    if ext in self.config.image_extensions:
                        m_type = MediaType.IMAGE
                    elif ext in self.config.video_extensions:
                        m_type = MediaType.VIDEO
                    else:
                        continue
                    try:
                        stat = src_file.stat()
                        # If output_dir is the same folder where the file lives or matches name,
                        # append _optimized to ensure the original file is NEVER replaced
                        if src_file.parent.resolve() == output_dir.resolve() or (output_dir / src_file.name).resolve() == src_file.resolve():
                            dst_name = f"{src_file.stem}_optimized{src_file.suffix}"
                        else:
                            dst_name = src_file.name

                        dst_file = output_dir / dst_name
                        tasks.append(TaskItem(
                            src_path=src_file,
                            rel_path=dst_name,
                            dst_path=dst_file,
                            media_type=m_type,
                            size_bytes=stat.st_size,
                            mtime=stat.st_mtime,
                        ))
                    except Exception:
                        continue
            return tasks, f"{len(tasks)} selected file(s)"

        target_path = Path(input_target).resolve()
        if target_path.is_file():
            ext = target_path.suffix.lower()
            if ext in self.config.image_extensions:
                m_type = MediaType.IMAGE
            elif ext in self.config.video_extensions:
                m_type = MediaType.VIDEO
            else:
                return tasks, f"file: {target_path.name}"
            try:
                stat = target_path.stat()
                # If output_dir is the same folder where the file lives, append _optimized
                if target_path.parent.resolve() == output_dir.resolve() or (output_dir / target_path.name).resolve() == target_path.resolve():
                    dst_name = f"{target_path.stem}_optimized{target_path.suffix}"
                else:
                    dst_name = target_path.name

                dst_file = output_dir / dst_name
                tasks.append(TaskItem(
                    src_path=target_path,
                    rel_path=dst_name,
                    dst_path=dst_file,
                    media_type=m_type,
                    size_bytes=stat.st_size,
                    mtime=stat.st_mtime,
                ))
            except Exception:
                pass
            return tasks, f"file: {target_path.name}"

        # Otherwise treat as directory
        tasks = self.scan_directory(target_path, output_dir)
        return tasks, f"directory: {target_path.name}"

    def process_batch(
        self,
        input_target: Union[Path, str, Sequence[Union[Path, str]]],
        output_dir: Path,
        on_progress: Optional[Callable[[int, int, str, int, int, int, str], None]] = None,
        on_item_finish: Optional[Callable[[str, int, int, str], None]] = None,
        on_item_start: Optional[Callable[[str, int, str], None]] = None,
        on_activity: Optional[Callable[[str, str], None]] = None,
    ) -> BatchSummary:
        """Run batch optimization on a directory or file list with real-time activity and progress reporting."""
        self._stop_requested.clear()
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Clean up any temporary files left from previous crashes
        try:
            for tmp_file in output_dir.glob("**/*.tmp.*"):
                if tmp_file.is_file():
                    tmp_file.unlink()
        except Exception:
            pass

        def emit_activity(msg: str, level: str = "info") -> None:
            if on_activity:
                try:
                    on_activity(msg, level)
                except Exception:
                    pass

        journal_path = output_dir / ".media_optimizer_journal.sqlite3"
        journal = Journal(journal_path)

        tasks, desc = self.create_tasks(input_target, output_dir)
        total_items = len(tasks)
        num_images = sum(1 for t in tasks if t.media_type == MediaType.IMAGE)
        num_videos = sum(1 for t in tasks if t.media_type == MediaType.VIDEO)

        emit_activity(f"Scanning {desc}...", "info")
        emit_activity(f"Discovered {total_items} media items ({num_images} photos, {num_videos} videos).", "info")

        if total_items == 0:
            is_dir = not isinstance(input_target, (list, tuple)) and Path(input_target).is_dir()
            emit_activity("No supported media files found in directory." if is_dir else "No supported media files found to optimize.", "info")
            emit_activity("Batch completed: 0 files to optimize.", "complete")
            summary = journal.get_summary()
            if on_progress:
                on_progress(0, 0, "No media files", 0, 0, 0, "No media files found")
            return summary

        # Filter out already completed files
        pending_tasks: List[TaskItem] = []
        skipped_already_done = 0

        for t in tasks:
            if journal.is_already_done(t.rel_path, t.mtime, t.size_bytes, t.dst_path):
                skipped_already_done += 1
            else:
                pending_tasks.append(t)

        if skipped_already_done > 0:
            emit_activity(f"Resuming batch: {skipped_already_done} files already completed, {len(pending_tasks)} remaining.", "info")

        if not pending_tasks:
            summary = journal.get_summary()
            emit_activity("All files are already up-to-date and optimized!", "complete")
            if on_progress:
                on_progress(
                    total_items,
                    total_items,
                    "Completed",
                    summary.original_bytes,
                    summary.optimized_bytes,
                    summary.saved_bytes,
                    "All files already completed",
                )
            return summary

        processed_counter = skipped_already_done
        counter_lock = threading.Lock()
        
        # In-memory counters for performance
        initial_summary = journal.get_summary()
        current_completed = initial_summary.completed
        current_skipped = initial_summary.skipped
        current_failed = initial_summary.failed
        current_orig_bytes = initial_summary.original_bytes
        current_opt_bytes = initial_summary.optimized_bytes
        
        active_files: set = set()
        active_lock = threading.Lock()

        def _process_task(task: TaskItem) -> None:
            nonlocal processed_counter, current_orig_bytes, current_opt_bytes
            nonlocal current_completed, current_skipped, current_failed
            
            if self._stop_requested.is_set():
                return

            rel_p = task.rel_path
            orig_sz = task.size_bytes
            mtime = task.mtime

            # Update active files set
            with active_lock:
                active_files.add(task.src_path.name)
                active_display = ", ".join(list(active_files)[:2])
                if len(active_files) > 2:
                    active_display += f" (+{len(active_files)-2} more)"

            # Analyze file
            try:
                media_type, info, plan = analyze_file(task.src_path, self.config)
                plan_reason = plan.reason
                
                # Update destination extension if format changes
                if plan and plan.action == DecisionAction.OPTIMIZE and plan.target_format:
                    new_ext = f".{plan.target_format.lower()}"
                    if new_ext == ".jpeg":
                        new_ext = ".jpg"
                    if task.dst_path.suffix.lower() != new_ext:
                        old_dst = task.dst_path
                        task.dst_path = task.dst_path.with_suffix(new_ext)
                        # If resolving collision made them identical again (unlikely but safe)
                        if task.src_path.resolve() == task.dst_path.resolve():
                            task.dst_path = task.dst_path.parent / f"{task.dst_path.stem}_optimized{new_ext}"
                        
                        # Update rel_p
                        if rel_p.endswith(old_dst.name):
                            rel_p = rel_p[:-len(old_dst.name)] + task.dst_path.name
                        else:
                            rel_p = str(task.dst_path.relative_to(output_dir))
                            
            except Exception as e:
                plan_reason = f"Analysis error: {e}"
                media_type = task.media_type
                plan = None

            if on_item_start:
                try:
                    on_item_start(rel_p, orig_sz, plan_reason)
                except Exception:
                    pass

            emit_activity(f"Optimizing {rel_p} ({format_bytes(orig_sz)}) - {plan_reason}", "start")

            if on_progress:
                with counter_lock:
                    c_orig = current_orig_bytes
                    c_opt = current_opt_bytes
                
                on_progress(
                    processed_counter,
                    total_items,
                    active_display,
                    c_orig,
                    c_opt,
                    max(0, c_orig - c_opt),
                    f"Processing {task.src_path.name}",
                )

            try:
                # 2. Optimize or Copy based on plan
                if media_type == MediaType.VIDEO:
                    if self._stop_requested.is_set():
                        return
                    success, final_sz, msg = optimize_video(
                        task.src_path, task.dst_path, plan, self.config
                    )
                else:  # Image
                    if self._stop_requested.is_set():
                        return
                    success, final_sz, msg = optimize_image(
                        task.src_path, task.dst_path, plan, self.config
                    )

                # 3. Record in journal
                if success:
                    if plan and plan.action == DecisionAction.OPTIMIZE and final_sz < orig_sz:
                        journal.record_completed(rel_p, orig_sz, final_sz, mtime, msg)
                        act_level = "saved"
                    else:
                        journal.record_skipped(rel_p, orig_sz, final_sz, mtime, msg)
                        act_level = "copied"
                else:
                    journal.record_failed(rel_p, orig_sz, mtime, msg)
                    act_level = "error"

            except Exception as e:
                msg = f"Failed: {e}"
                final_sz = orig_sz
                journal.record_failed(rel_p, orig_sz, mtime, msg)
                act_level = "error"

            with active_lock:
                active_files.discard(task.src_path.name)
                active_display = ", ".join(list(active_files)[:2]) if active_files else task.src_path.name

            with counter_lock:
                processed_counter += 1
                curr_count = processed_counter
                
                current_orig_bytes += orig_sz
                current_opt_bytes += final_sz
                
                if act_level == "saved":
                    current_completed += 1
                elif act_level == "copied":
                    current_skipped += 1
                elif act_level == "error":
                    current_failed += 1
                    
                c_orig = current_orig_bytes
                c_opt = current_opt_bytes

            if on_progress:
                on_progress(
                    curr_count,
                    total_items,
                    active_display,
                    c_orig,
                    c_opt,
                    max(0, c_orig - c_opt),
                    msg,
                )

            if on_item_finish:
                try:
                    on_item_finish(rel_p, orig_sz, final_sz, msg)
                except Exception:
                    pass

            emit_activity(f"{rel_p}: {msg}", act_level)

        # Notify initial progress state
        if on_progress and skipped_already_done > 0:
            summary = journal.get_summary()
            on_progress(
                skipped_already_done,
                total_items,
                "Resumed batch",
                summary.original_bytes,
                summary.optimized_bytes,
                summary.saved_bytes,
                f"Skipped {skipped_already_done} previously completed files",
            )

        # Dedicated thread pools for images and videos eliminate worker starvation:
        # Long-running video transcoding will not block rapid image processing.
        image_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.config.image_workers))
        video_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.config.video_workers))
        futures = []

        try:
            for t in pending_tasks:
                if t.media_type == MediaType.VIDEO:
                    futures.append(video_executor.submit(_process_task, t))
                else:
                    futures.append(image_executor.submit(_process_task, t))

            for future in concurrent.futures.as_completed(futures):
                if self._stop_requested.is_set():
                    image_executor.shutdown(wait=False, cancel_futures=True)
                    video_executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    future.result()
                except Exception:
                    pass
        finally:
            image_executor.shutdown(wait=True)
            video_executor.shutdown(wait=True)

        final_summary = journal.get_summary()
        emit_activity(
            f"Batch completed! Processed: {final_summary.completed} optimized, {final_summary.skipped} preserved, {final_summary.failed} failed. Space saved: {format_bytes(final_summary.saved_bytes)} ({final_summary.reduction_percent:.1f}% reduction).",
            "complete",
        )
        return final_summary
