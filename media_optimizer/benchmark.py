"""Benchmarking module for comparing Original vs WhatsApp vs Media Optimizer outputs."""

import json
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False
from PIL import Image

from media_optimizer.config import detect_hardware


@dataclass
class MediaMetrics:
    label: str
    file_path: Path
    file_size_bytes: int
    size_mb: float
    reduction_pct: float
    dimensions: str
    codec: str
    bitrate_kbps: Optional[float]
    fps: Optional[float]
    ssim: Optional[float]
    psnr: Optional[float]
    vmaf: Optional[float]
    encode_time_sec: Optional[float] = None


def compute_image_ssim_psnr(ref_path: Path, test_path: Path) -> Tuple[float, float]:
    """Calculate SSIM and PSNR between original reference image and compressed image."""
    try:
        with Image.open(ref_path) as ref_img, Image.open(test_path) as test_img:
            # Convert both to RGB
            ref_rgb = ref_img.convert("RGB")
            test_rgb = test_img.convert("RGB")

            # If test is downscaled, resize test back to ref dimensions for pixel metric
            if test_rgb.size != ref_rgb.size:
                test_rgb = test_rgb.resize(ref_rgb.size, Image.Resampling.LANCZOS)

            if HAS_NUMPY:
                ref_arr = np.array(ref_rgb, dtype=np.float32)
                test_arr = np.array(test_rgb, dtype=np.float32)

                # PSNR
                mse = np.mean((ref_arr - test_arr) ** 2)
                if mse == 0:
                    psnr = 100.0
                else:
                    psnr = 20 * math.log10(255.0 / math.sqrt(mse))

                # Simplified SSIM
                c1 = (0.01 * 255) ** 2
                c2 = (0.03 * 255) ** 2
                mu1 = np.mean(ref_arr)
                mu2 = np.mean(test_arr)
                sigma1_sq = np.var(ref_arr)
                sigma2_sq = np.var(test_arr)
                sigma12 = np.mean((ref_arr - mu1) * (test_arr - mu2))

                ssim = ((2 * mu1 * mu2 + c1) * (2 * sigma12 + c2)) / (
                    (mu1 ** 2 + mu2 ** 2 + c1) * (sigma1_sq + sigma2_sq + c2)
                )

                return float(ssim), float(psnr)
            else:
                from PIL import ImageChops, ImageStat
                diff = ImageChops.difference(ref_rgb, test_rgb)
                stat = ImageStat.Stat(diff)
                mse = sum(r ** 2 for r in stat.rms) / max(1, len(stat.rms))
                if mse == 0:
                    psnr = 100.0
                    ssim = 1.0
                else:
                    psnr = 20 * math.log10(255.0 / math.sqrt(mse))
                    ssim = max(0.0, 1.0 - (mse / (255.0 * 255.0)))
                return float(ssim), float(psnr)
    except Exception:
        return 0.0, 0.0


def compute_video_ssim_psnr(
    ref_path: Path, test_path: Path, ffmpeg_path: str
) -> Tuple[Optional[float], Optional[float]]:
    """Compute video SSIM and PSNR using FFmpeg filters."""
    try:
        # Scale test video to match ref video dimensions and frame count if needed
        filter_str = (
            "[1:v][0:v]scale2ref=flags=lanczos[test][ref];"
            "[test][ref]ssim=stats_file=-[v_ssim];"
            "[test][ref]psnr=stats_file=-"
        )
        cmd = [
            ffmpeg_path,
            "-i", str(ref_path),
            "-i", str(test_path),
            "-filter_complex", filter_str,
            "-f", "null",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        stderr = res.stderr

        ssim_val = None
        psnr_val = None

        # Parse SSIM
        ssim_match = re.search(r"SSIM\s+Y:([0-9.]+)\s+.*?All:([0-9.]+)", stderr)
        if ssim_match:
            ssim_val = float(ssim_match.group(2))

        # Parse PSNR
        psnr_match = re.search(r"PSNR\s+y:([0-9.]+)\s+.*?average:([0-9.]+)", stderr)
        if psnr_match:
            psnr_val = float(psnr_match.group(2))

        return ssim_val, psnr_val
    except Exception:
        return None, None


def probe_file_metrics(file_path: Path, label: str, orig_size: int, ffprobe_path: Optional[str]) -> MediaMetrics:
    """Extract technical and stream information from a file."""
    sz = file_path.stat().st_size
    sz_mb = sz / (1024 * 1024)
    reduction = ((orig_size - sz) / orig_size * 100.0) if orig_size > 0 else 0.0

    ext = file_path.suffix.lower()
    is_video = ext in (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")

    dims = "Unknown"
    codec = ext.lstrip(".").upper()
    bitrate = None
    fps = None

    if is_video and ffprobe_path and os.path.exists(ffprobe_path):
        try:
            cmd = [
                ffprobe_path,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(file_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            probe = json.loads(res.stdout)
            video_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
            if video_stream:
                w = video_stream.get("width", 0)
                h = video_stream.get("height", 0)
                dims = f"{w}x{h}"
                codec = video_stream.get("codec_name", "").upper()
                if "bit_rate" in video_stream:
                    bitrate = float(video_stream["bit_rate"]) / 1000.0
                elif "duration" in video_stream:
                    dur = float(video_stream["duration"])
                    bitrate = (sz * 8) / (dur * 1000.0) if dur > 0 else None

                avg_fps = video_stream.get("avg_frame_rate", "0/1")
                if "/" in avg_fps:
                    n, d = avg_fps.split("/")
                    fps = float(n) / float(d) if float(d) != 0 else 0.0
        except Exception:
            pass
    elif not is_video:
        try:
            with Image.open(file_path) as img:
                dims = f"{img.width}x{img.height}"
                codec = (img.format or ext.lstrip(".")).upper()
        except Exception:
            pass

    return MediaMetrics(
        label=label,
        file_path=file_path,
        file_size_bytes=sz,
        size_mb=sz_mb,
        reduction_pct=reduction,
        dimensions=dims,
        codec=codec,
        bitrate_kbps=bitrate,
        fps=fps,
        ssim=None,
        psnr=None,
        vmaf=None,
    )


class MediaBenchmark:
    """Run comparative quality and efficiency benchmarking."""

    def __init__(self):
        self.hw = detect_hardware()

    def compare(
        self,
        orig_file: Path,
        our_file: Path,
        whatsapp_file: Optional[Path] = None,
        encode_time_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Perform side-by-side comparison."""
        orig_size = orig_file.stat().st_size
        ext = orig_file.suffix.lower()
        is_video = ext in (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")

        # 1. Probe basic metrics
        orig_m = probe_file_metrics(orig_file, "Original", orig_size, self.hw.ffprobe_path)
        our_m = probe_file_metrics(our_file, "Media Optimizer", orig_size, self.hw.ffprobe_path)
        our_m.encode_time_sec = encode_time_sec

        wa_m = None
        if whatsapp_file and whatsapp_file.exists():
            wa_m = probe_file_metrics(whatsapp_file, "WhatsApp", orig_size, self.hw.ffprobe_path)

        # 2. Perceptual Quality Metrics
        if is_video and self.hw.ffmpeg_path:
            our_ssim, our_psnr = compute_video_ssim_psnr(orig_file, our_file, self.hw.ffmpeg_path)
            our_m.ssim = our_ssim
            our_m.psnr = our_psnr

            if wa_m:
                wa_ssim, wa_psnr = compute_video_ssim_psnr(orig_file, whatsapp_file, self.hw.ffmpeg_path)
                wa_m.ssim = wa_ssim
                wa_m.psnr = wa_psnr
        else:
            # Images
            our_ssim, our_psnr = compute_image_ssim_psnr(orig_file, our_file)
            our_m.ssim = our_ssim
            our_m.psnr = our_psnr

            if wa_m:
                wa_ssim, wa_psnr = compute_image_ssim_psnr(orig_file, whatsapp_file)
                wa_m.ssim = wa_ssim
                wa_m.psnr = wa_psnr

        return {
            "original": orig_m,
            "our_output": our_m,
            "whatsapp": wa_m,
            "is_video": is_video,
        }

    def format_markdown_report(self, results: Dict[str, Any]) -> str:
        """Format benchmark results as a Markdown table."""
        orig: MediaMetrics = results["original"]
        our: MediaMetrics = results["our_output"]
        wa: Optional[MediaMetrics] = results.get("whatsapp")
        is_video = results["is_video"]

        headers = ["Metric", "Original", "Our Optimizer"]
        if wa:
            headers.append("WhatsApp")

        lines = [
            f"# Benchmark Comparison: {orig.file_path.name}\n",
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]

        # File Size
        row = ["File Size", f"{orig.size_mb:.2f} MB", f"{our.size_mb:.2f} MB"]
        if wa:
            row.append(f"{wa.size_mb:.2f} MB")
        lines.append("| " + " | ".join(row) + " |")

        # Reduction %
        row = ["Reduction", "0.0%", f"**{our.reduction_pct:.1f}%**"]
        if wa:
            row.append(f"{wa.reduction_pct:.1f}%")
        lines.append("| " + " | ".join(row) + " |")

        # Dimensions
        row = ["Dimensions", orig.dimensions, our.dimensions]
        if wa:
            row.append(wa.dimensions)
        lines.append("| " + " | ".join(row) + " |")

        # Codec
        row = ["Codec", orig.codec, our.codec]
        if wa:
            row.append(wa.codec)
        lines.append("| " + " | ".join(row) + " |")

        if is_video:
            # Bitrate
            row = [
                "Bitrate",
                f"{orig.bitrate_kbps:.0f} kbps" if orig.bitrate_kbps else "N/A",
                f"{our.bitrate_kbps:.0f} kbps" if our.bitrate_kbps else "N/A",
            ]
            if wa:
                row.append(f"{wa.bitrate_kbps:.0f} kbps" if wa.bitrate_kbps else "N/A")
            lines.append("| " + " | ".join(row) + " |")

            # FPS
            row = [
                "FPS",
                f"{orig.fps:.1f}" if orig.fps else "N/A",
                f"{our.fps:.1f}" if our.fps else "N/A",
            ]
            if wa:
                row.append(f"{wa.fps:.1f}" if wa.fps else "N/A")
            lines.append("| " + " | ".join(row) + " |")

        # Perceptual SSIM
        row = ["SSIM (Quality)", "1.000", f"{our.ssim:.4f}" if our.ssim is not None else "N/A"]
        if wa:
            row.append(f"{wa.ssim:.4f}" if wa.ssim is not None else "N/A")
        lines.append("| " + " | ".join(row) + " |")

        # PSNR
        row = ["PSNR (dB)", "Inf", f"{our.psnr:.2f} dB" if our.psnr is not None else "N/A"]
        if wa:
            row.append(f"{wa.psnr:.2f} dB" if wa.psnr is not None else "N/A")
        lines.append("| " + " | ".join(row) + " |")

        if our.encode_time_sec is not None:
            lines.append(f"\n*Encoding time: {our.encode_time_sec:.2f}s*")

        return "\n".join(lines)
