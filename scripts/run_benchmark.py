"""Script to generate realistic test media, optimize it, and print comparative benchmarks."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from PIL import Image

from media_optimizer.benchmark import MediaBenchmark
from media_optimizer.config import get_default_config
from media_optimizer.pipeline import OptimizationPipeline


def main():
    root = Path(__file__).resolve().parent.parent
    demo_in = root / "demo_media"
    demo_out = root / "demo_media_optimized"

    if demo_in.exists():
        shutil.rmtree(demo_in)
    if demo_out.exists():
        shutil.rmtree(demo_out)

    demo_in.mkdir(parents=True, exist_ok=True)
    sub = demo_in / "Vacation2026"
    sub.mkdir(parents=True, exist_ok=True)

    print("==================================================")
    print("Generating representative test media set...")
    print("==================================================")

    # 1. Massive 4K camera photo (JPEG, high quality 98)
    photo1 = demo_in / "camera_landscape_4k.jpg"
    Image.frombytes("RGB", (3840, 2160), os.urandom(3840 * 2160 * 3)).save(photo1, "JPEG", quality=98)
    print(f"• Created {photo1.name} ({photo1.stat().st_size / (1024*1024):.2f} MB)")

    # 2. Large uncompressed screenshot PNG
    scr = demo_in / "system_screenshot.png"
    img_scr = Image.new("RGBA", (1920, 1080), color=(245, 245, 247, 255))
    for i in range(100, 500, 10):
        for j in range(100, 800, 10):
            img_scr.putpixel((j, i), (30, 30, 30, 255))
    img_scr.save(scr, "PNG", compress_level=0)
    print(f"• Created {scr.name} ({scr.stat().st_size / (1024*1024):.2f} MB)")

    # 3. Already small efficient photo
    small_photo = sub / "icon_small.jpg"
    Image.new("RGB", (300, 200), color=(100, 150, 200)).save(small_photo, "JPEG", quality=60)
    print(f"• Created {small_photo.name} ({small_photo.stat().st_size / 1024:.1f} KB)")

    # 4. High-bitrate 1080p video (simulating raw phone/action cam footage)
    vid = sub / "drone_footage.mp4"
    config = get_default_config()
    cmd = [
        config.hardware.ffmpeg_path,
        "-v", "error",
        "-y",
        "-f", "lavfi",
        "-i", "mandelbrot=size=1920x1080:rate=30",
        "-t", "3",
        "-c:v", "libx264",
        "-crf", "10",
        "-pix_fmt", "yuv420p",
        str(vid),
    ]
    subprocess.run(cmd, check=True)
    print(f"• Created {vid.name} ({vid.stat().st_size / (1024*1024):.2f} MB)")

    print("\nRunning Media Optimizer batch pipeline...")
    start_t = time.time()
    pipeline = OptimizationPipeline(config)
    summary = pipeline.process_batch(demo_in, demo_out)
    elapsed = time.time() - start_t

    print("\n" + summary.format_report())
    print(f"Elapsed Time: {elapsed:.2f}s\n")

    print("==================================================")
    print("Running Perceptual Quality Benchmarks (SSIM / PSNR)")
    print("==================================================")
    bench = MediaBenchmark()

    # Benchmark photo
    opt_photo = demo_out / "camera_landscape_4k.jpg"
    res_photo = bench.compare(photo1, opt_photo)
    print(bench.format_markdown_report(res_photo))
    print("\n")

    # Benchmark video
    opt_vid = demo_out / "Vacation2026" / "drone_footage.mp4"
    res_vid = bench.compare(vid, opt_vid)
    print(bench.format_markdown_report(res_vid))


if __name__ == "__main__":
    main()
