"""Configuration and hardware detection for media-optimizer."""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set


@dataclass
class HardwareProfile:
    is_apple_silicon: bool
    cpu_cores: int
    total_ram_gb: float
    has_hevc_videotoolbox: bool
    has_h264_videotoolbox: bool
    has_libx265: bool
    has_libx264: bool
    ffmpeg_path: Optional[str]
    ffprobe_path: Optional[str]
    exiftool_path: Optional[str]
    sips_path: Optional[str]


@dataclass
class OptimizerConfig:
    # Hardware & concurrency
    hardware: HardwareProfile
    image_workers: int = 4
    video_workers: int = 2

    # Optimization profile ("whatsapp" or "custom")
    profile: str = "whatsapp"

    # Image optimization thresholds & parameters (WhatsApp sweet spot: max 2048px HD, quality ~80)
    convert_heic_to_jpeg: bool = True       # By default convert HEIC to JPEG for compatibility
    image_min_size_bytes: int = 250 * 1024  # Don't re-encode images under 250 KB unless oversized
    image_min_bpp_to_optimize: float = 1.3   # Bits per pixel threshold
    image_max_dimension: int = 2048          # WhatsApp HD max dimension (longest side)
    jpeg_quality: int = 80                  # WhatsApp perceptual sweet spot (quality 78-80)
    webp_quality: int = 78
    heic_quality: int = 78
    png_compression_level: int = 9
    image_resample_filter: str = "bilinear"       # Fast downsampling ("bilinear" or "bicubic") avoiding CPU bottleneck

    # Video optimization thresholds & parameters (WhatsApp sweet spot: 1080p/720p, 30fps, 1200k-2200k)
    video_min_size_bytes: int = 3 * 1024 * 1024  # Don't re-encode videos under 3 MB if already efficient
    video_max_height: int = 1080                 # Downscale 4K/UHD to 1080p for massive size reduction
    video_max_fps: int = 30                      # WhatsApp standard 30fps cap (halves encode time & bitrate)
    video_target_bitrate_1080p: str = "2200k"    # Target bitrate for 1080p HEVC
    video_target_bitrate_720p: str = "1200k"     # Target bitrate for 720p HEVC
    video_target_bitrate_4k: str = "6000k"       # Target bitrate for 4K HEVC if kept
    audio_bitrate: str = "128k"                  # AAC audio bitrate
    prefer_hardware_encoder: bool = True
    video_scale_flags: str = "bicubic"           # Fast video scaling (bicubic) avoiding CPU bottlenecking
    video_prio_speed: bool = True                # Hint VideoToolbox to prioritize throughput speed

    # General options
    overwrite_existing: bool = False             # If true, overwrite existing files instead of appending _1, _2
    preserve_metadata: bool = True
    preserve_timestamps: bool = True
    min_saving_ratio: float = 0.05               # Must save at least 5% or keep original

    # Supported file extensions
    image_extensions: Set[str] = field(default_factory=lambda: {
        ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tiff", ".tif", ".bmp"
    })
    video_extensions: Set[str] = field(default_factory=lambda: {
        ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".3gp", ".flv"
    })


def detect_hardware() -> HardwareProfile:
    """Detect Apple Silicon, CPU count, RAM, and available media encoders."""
    # Check architecture
    arch = os.uname().machine
    is_apple_silicon = (arch == "arm64" and sys.platform == "darwin")

    # CPU cores
    cpu_cores = os.cpu_count() or 4

    # Total RAM
    total_ram_gb = 8.0
    if sys.platform == "darwin":
        try:
            res = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True)
            total_ram_gb = round(int(res.stdout.strip()) / (1024 ** 3), 1)
        except Exception:
            pass

    # Tool paths
    ffmpeg_path = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(ffmpeg_path):
        ffmpeg_path = None

    ffprobe_path = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    if not os.path.exists(ffprobe_path):
        ffprobe_path = None

    exiftool_path = shutil.which("exiftool") or "/opt/homebrew/bin/exiftool"
    if not os.path.exists(exiftool_path):
        exiftool_path = None

    sips_path = shutil.which("sips") or "/usr/bin/sips"
    if not os.path.exists(sips_path):
        sips_path = None

    # Check VideoToolbox encoders
    has_hevc_vt = False
    has_h264_vt = False
    has_libx265 = False
    has_libx264 = False
    if ffmpeg_path:
        try:
            res = subprocess.run([ffmpeg_path, "-encoders"], capture_output=True, text=True)
            output = res.stdout + res.stderr
            has_hevc_vt = "hevc_videotoolbox" in output
            has_h264_vt = "h264_videotoolbox" in output
            has_libx265 = "libx265" in output
            has_libx264 = "libx264" in output
        except Exception:
            pass

    return HardwareProfile(
        is_apple_silicon=is_apple_silicon,
        cpu_cores=cpu_cores,
        total_ram_gb=total_ram_gb,
        has_hevc_videotoolbox=has_hevc_vt,
        has_h264_videotoolbox=has_h264_vt,
        has_libx265=has_libx265,
        has_libx264=has_libx264,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        exiftool_path=exiftool_path,
        sips_path=sips_path,
    )


import json

USER_CONFIG_PATH = Path.home() / ".media_optimizer.json"

def load_user_config(config: OptimizerConfig) -> None:
    """Load user settings from file and apply them to config."""
    if USER_CONFIG_PATH.exists():
        try:
            with open(USER_CONFIG_PATH, "r") as f:
                user_data = json.load(f)
            for k, v in user_data.items():
                if hasattr(config, k):
                    setattr(config, k, v)
        except Exception as e:
            print(f"Failed to load user config: {e}")

def save_user_config(config: OptimizerConfig) -> None:
    """Save user settings to file."""
    # List of keys we allow users to customize
    user_keys = [
        "convert_heic_to_jpeg",
        "overwrite_existing",
        "preserve_metadata",
        "jpeg_quality",
        "image_max_dimension",
        "video_max_height",
        "video_max_fps"
    ]
    try:
        user_data = {k: getattr(config, k) for k in user_keys if hasattr(config, k)}
        with open(USER_CONFIG_PATH, "w") as f:
            json.dump(user_data, f, indent=2)
    except Exception as e:
        print(f"Failed to save user config: {e}")
import hashlib

def get_config_hash(config: OptimizerConfig) -> str:
    """Generate a hash representing the user-configurable optimization parameters."""
    keys = [
        "convert_heic_to_jpeg",
        "preserve_metadata",
        "jpeg_quality",
        "image_max_dimension",
        "video_max_height",
        "video_max_fps"
    ]
    try:
        user_data = {k: getattr(config, k) for k in keys if hasattr(config, k)}
        s = json.dumps(user_data, sort_keys=True)
        return hashlib.md5(s.encode()).hexdigest()
    except Exception:
        return ""
def get_default_config() -> OptimizerConfig:
    """Generate default configuration tuned for the current Mac, and apply user settings."""
    hw = detect_hardware()

    # Determine concurrency
    # Video transcoding uses hardware encoder or 100% CPU thread, keep workers conservative
    if hw.is_apple_silicon:
        video_workers = 3 if hw.cpu_cores >= 8 else 2 if hw.cpu_cores >= 6 else 1
        image_workers = min(8, max(2, hw.cpu_cores // 2))
    else:
        video_workers = 1
        image_workers = max(2, hw.cpu_cores // 2)

    config = OptimizerConfig(
        hardware=hw,
        image_workers=image_workers,
        video_workers=video_workers,
    )
    load_user_config(config)
    return config
