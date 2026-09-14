"""Command-line interface for the local media optimizer."""

import argparse
import sys
import time
from pathlib import Path

from media_optimizer.config import get_default_config
from media_optimizer.core.analyzer import analyze_file
from media_optimizer.pipeline import OptimizationPipeline


def format_bytes(num_bytes: int) -> str:
    """Format bytes into readable units."""
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / (1024 ** 3):.2f} GB"
    elif num_bytes >= 1024 ** 2:
        return f"{num_bytes / (1024 ** 2):.1f} MB"
    elif num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} B"


def run_dry_run(input_target: Path, config) -> None:
    """Scan and print the decision for each media file without writing changes."""
    print(f"\n[DRY RUN] Scanning: {input_target}")
    pipeline = OptimizationPipeline(config)
    tasks, _ = pipeline.create_tasks(input_target, input_target if input_target.is_dir() else input_target.parent)

    print(f"Found {len(tasks)} supported media file(s).\n")
    print(f"{'Filename':<35} | {'Type':<6} | {'Size':<10} | {'Action':<10} | {'Decision / Reason'}")
    print("-" * 100)

    for t in tasks:
        media_type, info, plan = analyze_file(t.src_path, config)
        sz_str = format_bytes(t.size_bytes)
        print(f"{t.src_path.name[:35]:<35} | {media_type.value:<6} | {sz_str:<10} | {plan.action.value.upper():<10} | {plan.reason}")

    print("-" * 100)
    print("Dry run complete. No files were modified.\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Media Optimizer - Automatic macOS local media compressor for photos & videos."
    )
    parser.add_argument("input_path", nargs="?", help="Input file or directory containing photos/videos.")
    parser.add_argument("-o", "--output", help="Output directory (defaults to <input>_optimized).")
    parser.add_argument("--dry-run", action="store_true", help="Analyze media and display optimization plans without processing.")
    parser.add_argument("--gui", action="store_true", help="Launch graphical user interface.")
    parser.add_argument("--web", action="store_true", help="Launch local browser-based GUI interface.")
    parser.add_argument("--image-workers", type=int, help="Override image worker threads.")
    parser.add_argument("--video-workers", type=int, help="Override concurrent video transcodings.")

    args = parser.parse_args()

    if args.web or args.gui or not args.input_path:
        from media_optimizer.gui import launch_gui
        launch_gui(args.input_path, force_web=args.web)
        return

    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.exists():
        print(f"Error: Input path does not exist: {input_path}", file=sys.stderr)
        print("Please provide an existing media file or folder to optimize.", file=sys.stderr)
        sys.exit(1)

    if input_path.is_file():
        default_out = input_path.parent
    else:
        default_out = input_path.parent / f"{input_path.name}_optimized"

    output_dir = Path(args.output).expanduser().resolve() if args.output else default_out

    config = get_default_config()
    if args.image_workers:
        config.image_workers = max(1, args.image_workers)
    if args.video_workers:
        config.video_workers = max(1, args.video_workers)

    if args.dry_run:
        run_dry_run(input_path, config)
        return

    print("==================================================")
    print("   LOCAL MEDIA OPTIMIZER (macOS Apple Silicon)    ")
    print("==================================================")
    print(f"Input Target:      {input_path}")
    print(f"Output Directory:  {output_dir}")
    print(f"Hardware Profile:  {config.hardware.cpu_cores} Cores, {config.hardware.total_ram_gb} GB RAM")
    print(f"Video Encoder:     {'hevc_videotoolbox (Hardware)' if config.hardware.has_hevc_videotoolbox else 'Software'}")
    print(f"Workers:           {config.image_workers} image threads, {config.video_workers} video encoders")
    print("==================================================\n")

    start_time = time.time()
    pipeline = OptimizationPipeline(config)

    def on_progress(current, total, current_file, orig_b, opt_b, saved_b, msg):
        pct = (saved_b / orig_b * 100.0) if orig_b > 0 else 0.0
        line = f"[{current}/{total}] {format_bytes(orig_b)} -> {format_bytes(opt_b)} (Saved {format_bytes(saved_b)}, -{pct:.1f}%) | {current_file[:25]}"
        # Overwrite terminal line
        sys.stdout.write(f"\r\033[K{line}")
        sys.stdout.flush()

    summary = pipeline.process_batch(input_path, output_dir, on_progress=on_progress)
    print("\n")
    elapsed = time.time() - start_time

    print(summary.format_report())
    print(f"Total time elapsed:   {elapsed:.1f}s")
    print(f"Output stored in:     {output_dir}\n")


if __name__ == "__main__":
    main()
