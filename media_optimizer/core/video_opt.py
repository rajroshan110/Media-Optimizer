"""Video optimization engine using FFmpeg and Apple Silicon VideoToolbox."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from media_optimizer.config import OptimizerConfig
from media_optimizer.core.analyzer import DecisionAction, OptimizationPlan
from media_optimizer.core.metadata import copy_metadata, preserve_timestamps


def optimize_video(
    src: Path,
    dst: Path,
    plan: OptimizationPlan,
    config: OptimizerConfig,
) -> Tuple[bool, int, str]:
    """Optimize a video using FFmpeg with hardware acceleration.
    
    Returns:
        (success: bool, final_size_bytes: int, summary_msg: str)
    """
    orig_size = src.stat().st_size
    dst.parent.mkdir(parents=True, exist_ok=True)

    # If plan is COPY or not OPTIMIZE, just copy original
    if plan.action != DecisionAction.OPTIMIZE:
        shutil.copy2(src, dst)
        preserve_timestamps(src, dst)
        return True, orig_size, f"Preserved original ({plan.reason})"

    if not config.hardware.ffmpeg_path:
        shutil.copy2(src, dst)
        preserve_timestamps(src, dst)
        return False, orig_size, "FFmpeg not available, copied original"

    # Always target .mp4 container for maximum compatibility
    final_dst = dst.with_suffix(".mp4") if dst.suffix.lower() != ".mp4" else dst
    temp_dst = final_dst.with_name(f"{final_dst.name}.tmp.mp4")

    hw_decode = config.prefer_hardware_encoder and config.hardware.has_hevc_videotoolbox

    try:
        # Build FFmpeg command
        cmd = [
            config.hardware.ffmpeg_path,
            "-v", "error",
            "-y",  # overwrite output
        ]

        # Use Apple Silicon hardware accelerated decoding if available
        if hw_decode:
            cmd.extend(["-hwaccel", "videotoolbox"])

        cmd.extend(["-i", str(src)])

        # Video filter for resolution scaling
        filters = []
        if plan.target_width and plan.target_height:
            w = (plan.target_width // 2) * 2
            h = (plan.target_height // 2) * 2
            orig_w = plan.extra_params.get("orig_width")
            orig_h = plan.extra_params.get("orig_height")
            # Only add scaling filter if dimensions actually changed (avoids CPU scaling bottleneck)
            if orig_w is None or orig_h is None or (w != orig_w or h != orig_h):
                scale_flags = getattr(config, "video_scale_flags", "bicubic")
                filters.append(f"scale={w}:{h}:flags={scale_flags}")

        if filters:
            cmd.extend(["-vf", ",".join(filters)])

        # Framerate cap (WhatsApp 30fps standard)
        if plan.target_fps:
            cmd.extend(["-r", str(plan.target_fps)])

        # Video codec & bitrate
        vcodec = plan.target_video_codec or "hevc_videotoolbox"
        cmd.extend(["-c:v", vcodec])

        if vcodec == "hevc_videotoolbox":
            bitrate = plan.target_video_bitrate or "2200k"
            cmd.extend([
                "-b:v", bitrate,
                "-prio_speed", "1",      # Prioritize encoding speed in VideoToolbox
                "-spatial_aq", "1",      # Apple Silicon hardware spatial adaptive quantization
                "-tag:v", "hvc1",        # Required for Apple QuickTime / iOS / macOS preview compatibility
                "-pix_fmt", "yuv420p",
            ])
        elif vcodec == "h264_videotoolbox":
            bitrate = plan.target_video_bitrate or "2800k"
            cmd.extend([
                "-b:v", bitrate,
                "-prio_speed", "1",
                "-spatial_aq", "1",
                "-profile:v", "high",
                "-pix_fmt", "yuv420p",
            ])
        elif vcodec == "libx265":
            cmd.extend([
                "-crf", "25",
                "-preset", "fast",
                "-tag:v", "hvc1",
                "-pix_fmt", "yuv420p",
            ])
        else:  # libx264
            cmd.extend([
                "-crf", "23",
                "-preset", "veryfast",
                "-pix_fmt", "yuv420p",
            ])

        # Audio codec & bitrate
        if plan.target_audio_codec == "copy":
            cmd.extend(["-c:a", "copy"])
        elif plan.target_audio_codec == "aac":
            cmd.extend([
                "-c:a", "aac",
                "-b:a", plan.target_audio_bitrate or "128k",
            ])
        elif plan.target_audio_codec is None:
            cmd.extend(["-an"])  # No audio stream in source

        # Native metadata mapping (preserves creation date, camera info, GPS)
        cmd.extend(["-map_metadata", "0"])

        # Optimize for streaming / fast web playback
        cmd.extend(["-movflags", "+faststart"])

        # Output destination
        cmd.append(str(temp_dst))

        # Execute FFmpeg
        res = subprocess.run(cmd, capture_output=True, text=True)

        # If hardware accelerated decode failed on unusual source container, retry without -hwaccel
        if res.returncode != 0 and hw_decode:
            cmd_no_hw = [arg for arg in cmd if arg not in ("-hwaccel", "videotoolbox")]
            res = subprocess.run(cmd_no_hw, capture_output=True, text=True)

        if res.returncode != 0 or not temp_dst.exists():
            if temp_dst.exists():
                temp_dst.unlink()
            # If hardware encoder failed (e.g. unsupported profile), try software fallback
            if vcodec in ("hevc_videotoolbox", "h264_videotoolbox"):
                return _fallback_software_transcode(src, final_dst, plan, config, orig_size)

            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            preserve_timestamps(src, dst)
            return False, orig_size, f"Transcoding failed ({res.stderr.strip()[:80]}), copied original"

        new_size = temp_dst.stat().st_size

        # Safety check: if new size doesn't save at least min_saving_ratio (e.g. 5%), keep original
        threshold = orig_size * (1.0 - config.min_saving_ratio)
        if new_size >= threshold:
            temp_dst.unlink()
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            preserve_timestamps(src, dst)
            return True, orig_size, f"Preserved original (savings < {config.min_saving_ratio*100:.0f}%)"

        # Success: atomic move
        temp_dst.replace(final_dst)
        if config.preserve_metadata:
            copy_metadata(src, final_dst, config.hardware.exiftool_path)
        preserve_timestamps(src, final_dst)

        saved_bytes = orig_size - new_size
        pct = (saved_bytes / orig_size) * 100.0
        return True, new_size, f"Optimized {orig_size / (1024*1024):.1f}MB -> {new_size / (1024*1024):.1f}MB (-{pct:.1f}%)"

    except Exception as e:
        if temp_dst.exists():
            temp_dst.unlink()
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        preserve_timestamps(src, dst)
        return False, orig_size, f"Error ({e}), copied original"


def _fallback_software_transcode(
    src: Path,
    dst: Path,
    plan: OptimizationPlan,
    config: OptimizerConfig,
    orig_size: int,
) -> Tuple[bool, int, str]:
    """Fallback to CPU libx264 encoding if hardware VideoToolbox fails."""
    temp_dst = dst.with_name(f"{dst.name}.tmp.mp4")
    try:
        cmd = [
            config.hardware.ffmpeg_path,
            "-v", "error",
            "-y",
            "-i", str(src),
            "-c:v", "libx264",
            "-crf", "23",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(temp_dst),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and temp_dst.exists():
            new_size = temp_dst.stat().st_size
            if new_size < orig_size * (1.0 - config.min_saving_ratio):
                temp_dst.replace(dst)
                if config.preserve_metadata:
                    copy_metadata(src, dst, config.hardware.exiftool_path)
                preserve_timestamps(src, dst)
                pct = ((orig_size - new_size) / orig_size) * 100.0
                return True, new_size, f"Optimized (CPU fallback) -{pct:.1f}%"

        if temp_dst.exists():
            temp_dst.unlink()
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        preserve_timestamps(src, dst)
        return True, orig_size, "Fallback to original copy"
    except Exception:
        if temp_dst.exists():
            temp_dst.unlink()
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        preserve_timestamps(src, dst)
        return False, orig_size, "Transcode error, copied original"
