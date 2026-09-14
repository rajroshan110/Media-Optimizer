"""Media analysis and automatic optimization decision engine."""

import json
import math
import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from media_optimizer.config import OptimizerConfig


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    UNKNOWN = "unknown"


class DecisionAction(str, Enum):
    OPTIMIZE = "optimize"
    COPY = "copy"      # File is already optimal; preserve original without recompression
    SKIP = "skip"      # Unchanged / error


@dataclass
class ImageInfo:
    width: int
    height: int
    format: str
    mode: str
    size_bytes: int
    bits_per_pixel: float
    is_photographic: bool
    has_alpha: bool
    has_exif: bool


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    duration_sec: float
    video_codec: str
    video_bitrate_kbps: float
    audio_codec: Optional[str]
    audio_bitrate_kbps: Optional[float]
    size_bytes: int
    bits_per_pixel_per_frame: float
    is_hdr: bool


@dataclass
class OptimizationPlan:
    action: DecisionAction
    media_type: MediaType
    reason: str
    target_format: Optional[str] = None
    target_width: Optional[int] = None
    target_height: Optional[int] = None
    target_quality: Optional[int] = None
    target_video_codec: Optional[str] = None
    target_video_bitrate: Optional[str] = None
    target_fps: Optional[float] = None
    target_audio_codec: Optional[str] = None
    target_audio_bitrate: Optional[str] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)


def analyze_image(path: Path, config: OptimizerConfig) -> Tuple[Optional[ImageInfo], OptimizationPlan]:
    """Analyze an image file and determine the optimal processing decision."""
    size_bytes = path.stat().st_size
    ext = path.suffix.lower()

    if size_bytes == 0:
        return None, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.IMAGE,
            reason="Empty file",
        )

    # Use sips for HEIC/HEIF inspection if Pillow cannot open it directly
    width = 0
    height = 0
    img_format = ""
    mode = ""
    has_exif = False
    has_alpha = False
    is_photographic = True

    try:
        with Image.open(path) as img:
            width, height = img.size
            img_format = (img.format or ext.lstrip(".")).upper()
            mode = img.mode
            has_alpha = ("A" in mode) or ("transparency" in img.info)
            has_exif = bool(img.getexif()) if hasattr(img, "getexif") else False

            # Graphics vs Photographic classification
            # Photographic images typically have diverse color palettes and no simple palette
            if img_format == "PNG" or has_alpha:
                # Check for graphics/screenshot characteristics
                # Sample a thumbnail to check distinct colors quickly
                thumb = img.copy()
                thumb.thumbnail((128, 128))
                colors = thumb.getcolors(maxcolors=4096)
                if colors is not None and len(colors) < 512:
                    is_photographic = False
                else:
                    is_photographic = True
            elif img_format in ("JPEG", "JPG", "HEIC", "WEBP"):
                is_photographic = True
            else:
                is_photographic = True

    except Exception:
        # Fallback to sips for macOS native formats like HEIC
        if config.hardware.sips_path and ext in (".heic", ".heif", ".avif", ".tiff"):
            try:
                res = subprocess.run(
                    [config.hardware.sips_path, "-g", "pixelWidth", "-g", "pixelHeight", "-g", "format", str(path)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                lines = res.stdout.splitlines()
                for line in lines:
                    if "pixelWidth:" in line:
                        width = int(line.split(":")[-1].strip())
                    elif "pixelHeight:" in line:
                        height = int(line.split(":")[-1].strip())
                    elif "format:" in line:
                        img_format = line.split(":")[-1].strip().upper()
                mode = "RGB"
                is_photographic = True
            except Exception as e:
                return None, OptimizationPlan(
                    action=DecisionAction.COPY,
                    media_type=MediaType.IMAGE,
                    reason=f"Failed to inspect image: {e}",
                )
        else:
            return None, OptimizationPlan(
                action=DecisionAction.COPY,
                media_type=MediaType.IMAGE,
                reason="Unreadable image format",
            )

    if width <= 0 or height <= 0:
        return None, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.IMAGE,
            reason="Invalid dimensions",
        )

    # Calculate bits per pixel (bpp)
    num_pixels = width * height
    bpp = (size_bytes * 8) / num_pixels

    info = ImageInfo(
        width=width,
        height=height,
        format=img_format,
        mode=mode,
        size_bytes=size_bytes,
        bits_per_pixel=bpp,
        is_photographic=is_photographic,
        has_alpha=has_alpha,
        has_exif=has_exif,
    )

    # Decision Logic:
    # 1. Very small images (< image_min_size_bytes e.g. 250 KB)
    # If bpp is already low (< 1.2) and within max dimension, re-encoding yields negligible savings.
    max_dim = max(width, height)
    if size_bytes < config.image_min_size_bytes and bpp < 1.2 and max_dim <= config.image_max_dimension:
        return info, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.IMAGE,
            reason=f"Already small ({size_bytes // 1024} KB, {bpp:.2f} bpp)",
        )

    # 2. Graphics / Screenshots (PNG)
    if not is_photographic and img_format == "PNG":
        # Flat graphic / screenshot: preserve crisp PNG lines without JPEG artifacts
        # If already small (< image_min_size_bytes), preserve
        if size_bytes < config.image_min_size_bytes and bpp < 1.5:
            return info, OptimizationPlan(
                action=DecisionAction.COPY,
                media_type=MediaType.IMAGE,
                reason=f"Already efficient screenshot PNG ({size_bytes // 1024} KB)",
            )
        return info, OptimizationPlan(
            action=DecisionAction.OPTIMIZE,
            media_type=MediaType.IMAGE,
            reason="Graphic/Screenshot PNG optimization",
            target_format="PNG",
            target_width=width,
            target_height=height,
            extra_params={"lossless": True},
        )

    # 3. Photographic Images (JPEG, camera HEIC, photographic PNG)
    # Check if image exceeds maximum dimension (WhatsApp HD standard: 2048px on longest side)
    target_w = width
    target_h = height
    needs_downscale = False

    if config.image_max_dimension > 0:
        if max_dim > config.image_max_dimension:
            scale = config.image_max_dimension / max_dim
            target_w = int(round(width * scale))
            target_h = int(round(height * scale))
            needs_downscale = True

    # If it's already JPEG with low bpp and no downscaling needed, skip
    if img_format in ("JPEG", "JPG") and not needs_downscale and bpp < config.image_min_bpp_to_optimize and size_bytes < 400 * 1024:
        return info, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.IMAGE,
            reason=f"Already efficiently compressed JPEG ({bpp:.2f} bpp)",
        )

    # If already low bpp and downscale is minor (< 10% linear downscale), avoid generational loss
    if img_format in ("JPEG", "JPG") and bpp < 1.1 and max_dim <= config.image_max_dimension * 1.10 and not needs_downscale:
        return info, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.IMAGE,
            reason=f"Already efficiently compressed JPEG ({bpp:.2f} bpp, minor scale)",
        )

    reason = []
    if needs_downscale:
        reason.append(f"Downscale {width}x{height} -> {target_w}x{target_h}")
    if size_bytes > 2 * 1024 * 1024:
        reason.append(f"High file size ({size_bytes / (1024*1024):.1f} MB)")
    elif bpp >= config.image_min_bpp_to_optimize:
        reason.append(f"High entropy ({bpp:.2f} bpp)")
    else:
        reason.append("WhatsApp-like profile optimization")

    # Target format selection:
    # Photographic images (JPEG, camera HEIC, photographic PNG) target JPEG for maximum universal compatibility & size reduction
    target_fmt = "JPEG"

    return info, OptimizationPlan(
        action=DecisionAction.OPTIMIZE,
        media_type=MediaType.IMAGE,
        reason=", ".join(reason),
        target_format=target_fmt,
        target_width=target_w,
        target_height=target_h,
        target_quality=config.jpeg_quality,
        extra_params={"preserve_exif": True},
    )


def _parse_fps(fps_str: str) -> float:
    """Safely parse frame rate string (e.g. '30/1' or '29.97')."""
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/")
            return float(num) / float(den) if float(den) != 0 else 30.0
        return float(fps_str)
    except Exception:
        return 30.0


def analyze_video(path: Path, config: OptimizerConfig) -> Tuple[Optional[VideoInfo], OptimizationPlan]:
    """Analyze a video file using ffprobe and determine the optimal transcoding decision."""
    size_bytes = path.stat().st_size
    if size_bytes == 0:
        return None, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.VIDEO,
            reason="Empty file",
        )

    if not config.hardware.ffprobe_path:
        return None, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.VIDEO,
            reason="ffprobe not found",
        )

    try:
        cmd = [
            config.hardware.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        probe = json.loads(res.stdout)
    except Exception as e:
        return None, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.VIDEO,
            reason=f"ffprobe failed: {e}",
        )

    streams = probe.get("streams", [])
    format_info = probe.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not video_stream:
        return None, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.VIDEO,
            reason="No video stream found",
        )

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    codec_name = video_stream.get("codec_name", "").lower()
    fps = _parse_fps(video_stream.get("avg_frame_rate", "30/1"))
    if fps <= 0 or fps > 240:
        fps = _parse_fps(video_stream.get("r_frame_rate", "30/1"))
        if fps <= 0:
            fps = 30.0

    duration_sec = float(format_info.get("duration", video_stream.get("duration", 0.0)))

    # Estimate video bitrate
    video_bitrate_kbps = 0.0
    if "bit_rate" in video_stream and video_stream["bit_rate"]:
        try:
            video_bitrate_kbps = float(video_stream["bit_rate"]) / 1000.0
        except ValueError:
            pass

    if video_bitrate_kbps <= 0 and duration_sec > 0:
        # Fallback to total bitrate minus audio estimate
        total_bitrate_kbps = (size_bytes * 8) / (duration_sec * 1000.0)
        video_bitrate_kbps = max(100.0, total_bitrate_kbps - 128.0)

    # Audio details
    audio_codec = audio_stream.get("codec_name", "").lower() if audio_stream else None
    audio_bitrate_kbps = None
    if audio_stream and "bit_rate" in audio_stream and audio_stream["bit_rate"]:
        try:
            audio_bitrate_kbps = float(audio_stream["bit_rate"]) / 1000.0
        except ValueError:
            pass

    # Check for HDR
    color_transfer = video_stream.get("color_transfer", "")
    color_primaries = video_stream.get("color_primaries", "")
    is_hdr = any("smpte2084" in s or "arib-std-b67" in s or "bt2020" in s for s in (color_transfer, color_primaries))

    # Calculate bits per pixel per frame (bppf)
    num_pixels = width * height if width > 0 and height > 0 else 1
    bppf = (video_bitrate_kbps * 1000.0) / (num_pixels * fps) if fps > 0 else 0.0

    info = VideoInfo(
        width=width,
        height=height,
        fps=fps,
        duration_sec=duration_sec,
        video_codec=codec_name,
        video_bitrate_kbps=video_bitrate_kbps,
        audio_codec=audio_codec,
        audio_bitrate_kbps=audio_bitrate_kbps,
        size_bytes=size_bytes,
        bits_per_pixel_per_frame=bppf,
        is_hdr=is_hdr,
    )

    min_dim = min(width, height) if width > 0 and height > 0 else 1
    max_dim = max(width, height) if width > 0 and height > 0 else 1
    is_within_1080p = (min_dim <= config.video_max_height and max_dim <= 1920)

    # Decision Logic:
    # 1. If video is already ultra-low bitrate (< 200 kbps), hardware re-encoding will bloat file size
    if video_bitrate_kbps < 200.0:
        return info, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.VIDEO,
            reason=f"Already small video ({size_bytes / (1024*1024):.1f} MB, ultra-low bitrate {int(video_bitrate_kbps)} kbps)",
        )

    # 2. If small video file (< 5 MB), within 1080p, and reasonable bitrate (< 500 kbps), leave alone
    if size_bytes < config.video_min_size_bytes and video_bitrate_kbps < 500.0 and is_within_1080p:
        return info, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.VIDEO,
            reason=f"Already small efficient video ({size_bytes / (1024*1024):.1f} MB, {int(video_bitrate_kbps)} kbps)",
        )

    # 3. If already modern codec (HEVC / AV1 / VP9) with low bitrate and <= 1080p and <= 30fps, do not re-encode
    is_already_modern_codec = codec_name in ("hevc", "h265", "av1", "vp9")
    if is_already_modern_codec and is_within_1080p and (video_bitrate_kbps < 2400.0 or bppf < 0.05) and fps <= config.video_max_fps + 1:
        return info, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.VIDEO,
            reason=f"Already efficiently encoded in {codec_name.upper()} ({int(video_bitrate_kbps)} kbps)",
        )

    # 4. Optimize high-bitrate / inefficient video
    # Determine target resolution:
    target_w = width
    target_h = height
    is_portrait = height > width
    max_w = config.video_max_height if is_portrait else 1920
    max_h = 1920 if is_portrait else config.video_max_height

    if width > max_w or height > max_h:
        scale = min(max_w / width, max_h / height)
        target_w = min(width, (int(round(width * scale)) // 2) * 2)
        target_h = min(height, (int(round(height * scale)) // 2) * 2)

    # Ensure dimensions are divisible by 2 and never upscaled
    target_w = min(width, (target_w // 2) * 2)
    target_h = min(height, (target_h // 2) * 2)

    # Target framerate (capped to WhatsApp 30fps standard if source exceeds it)
    target_fps = float(config.video_max_fps) if fps > (float(config.video_max_fps) + 0.5) else None

    # Target bitrate calculation based on target resolution (WhatsApp profile sweet spot)
    target_max_dim = max(target_w, target_h)
    if target_max_dim >= 1800:
        base_target_bitrate_kbps = 2200.0  # WhatsApp 1080p HEVC sweet spot
    elif target_max_dim >= 1200:
        base_target_bitrate_kbps = 1200.0  # WhatsApp 720p HEVC sweet spot
    else:
        base_target_bitrate_kbps = 700.0   # SD / 480p

    # Target ~30% reduction from source, bounded between 250k and base_target_bitrate_kbps
    if video_bitrate_kbps > 0:
        target_bitrate_kbps = min(base_target_bitrate_kbps, max(250.0, video_bitrate_kbps * 0.70))
    else:
        target_bitrate_kbps = base_target_bitrate_kbps

    target_bitrate_str = f"{int(target_bitrate_kbps)}k"

    # Select video encoder:
    # Use hevc_videotoolbox if available on Apple Silicon, fallback to libx265 or libx264
    if config.prefer_hardware_encoder and config.hardware.has_hevc_videotoolbox:
        target_vcodec = "hevc_videotoolbox"
    elif config.hardware.has_libx265:
        target_vcodec = "libx265"
    else:
        target_vcodec = "libx264"

    # Audio optimization:
    # If no audio stream in source, don't attempt audio encoding
    if not audio_stream:
        target_acodec = None
        target_abitrate = None
    elif audio_codec in ("aac", "opus", "mp3") and audio_bitrate_kbps and audio_bitrate_kbps <= 160:
        target_acodec = "copy"
        target_abitrate = None
    else:
        target_acodec = "aac"
        target_abitrate = config.audio_bitrate

    reason_parts = []
    if height != target_h or width != target_w:
        reason_parts.append(f"Downscale {width}x{height} -> {target_w}x{target_h}")
    if target_fps:
        reason_parts.append(f"Cap framerate {int(fps)}fps -> {int(target_fps)}fps")
    if codec_name != "hevc":
        reason_parts.append(f"Modernize codec ({codec_name} -> HEVC)")
    if video_bitrate_kbps > target_bitrate_kbps * 1.3:
        reason_parts.append(f"Reduce bitrate ({int(video_bitrate_kbps)}k -> {target_bitrate_str})")

    return info, OptimizationPlan(
        action=DecisionAction.OPTIMIZE,
        media_type=MediaType.VIDEO,
        reason=", ".join(reason_parts) if reason_parts else "Compress video stream",
        target_format="mp4",
        target_width=target_w,
        target_height=target_h,
        target_fps=target_fps,
        target_video_codec=target_vcodec,
        target_video_bitrate=target_bitrate_str,
        target_audio_codec=target_acodec,
        target_audio_bitrate=target_abitrate,
        extra_params={
            "faststart": True,
            "hdr": is_hdr,
            "orig_width": width,
            "orig_height": height,
            "orig_fps": fps,
        },
    )


def analyze_file(path: Path, config: OptimizerConfig) -> Tuple[MediaType, Any, OptimizationPlan]:
    """Inspect any file and return its type, info, and optimization plan."""
    ext = path.suffix.lower()

    if ext in config.image_extensions:
        info, plan = analyze_image(path, config)
        return MediaType.IMAGE, info, plan
    elif ext in config.video_extensions:
        info, plan = analyze_video(path, config)
        return MediaType.VIDEO, info, plan
    else:
        return MediaType.UNKNOWN, None, OptimizationPlan(
            action=DecisionAction.COPY,
            media_type=MediaType.UNKNOWN,
            reason="Non-media file",
        )
