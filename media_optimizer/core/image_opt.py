"""Image optimization engine using Pillow and macOS sips."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageOps

from media_optimizer.config import OptimizerConfig
from media_optimizer.core.analyzer import DecisionAction, OptimizationPlan
from media_optimizer.core.metadata import copy_metadata, preserve_timestamps


def optimize_image(
    src: Path,
    dst: Path,
    plan: OptimizationPlan,
    config: OptimizerConfig,
) -> Tuple[bool, int, str]:
    """Optimize an image based on the plan.
    
    Returns:
        (success: bool, final_size_bytes: int, summary_msg: str)
    """
    orig_size = src.stat().st_size
    dst.parent.mkdir(parents=True, exist_ok=True)

    # If plan is COPY or not OPTIMIZE, just copy original
    if plan.action != DecisionAction.OPTIMIZE:
        shutil.copy2(src, dst)
        copy_metadata(src, dst, config.hardware.exiftool_path)
        return True, orig_size, f"Preserved original ({plan.reason})"

    # Atomic write to temporary file
    temp_dst = dst.with_name(f"{dst.name}.tmp{src.suffix}")
    ext = src.suffix.lower()

    try:
        # Determine if we should use sips (e.g. for HEIC/HEIF or when Pillow fails)
        use_sips = ext in (".heic", ".heif") and config.hardware.sips_path

        if use_sips:
            success = _optimize_with_sips(src, temp_dst, plan, config)
        else:
            success = _optimize_with_pillow(src, temp_dst, plan, config)

        if not success or not temp_dst.exists():
            # Fallback: copy original
            if temp_dst.exists():
                temp_dst.unlink()
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            copy_metadata(src, dst, config.hardware.exiftool_path)
            return True, orig_size, "Fallback to original"

        new_size = temp_dst.stat().st_size

        # Safety check: if new size doesn't save at least min_saving_ratio (e.g. 5%), keep original
        threshold = orig_size * (1.0 - config.min_saving_ratio)
        if new_size >= threshold:
            temp_dst.unlink()
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            copy_metadata(src, dst, config.hardware.exiftool_path)
            return True, orig_size, f"Preserved original (savings < {config.min_saving_ratio*100:.0f}%)"

        # Apply metadata and atomic replace
        if temp_dst.suffix != dst.suffix:
            # If format changed (e.g. .heic -> .jpg or .png -> .jpg)
            # update dst filename extension accordingly if needed or keep dst
            pass

        temp_dst.replace(dst)
        preserve_timestamps(src, dst)
        if use_sips and config.preserve_metadata and config.hardware.exiftool_path:
            copy_metadata(src, dst, config.hardware.exiftool_path)

        saved_bytes = orig_size - new_size
        pct = (saved_bytes / orig_size) * 100.0
        return True, new_size, f"Optimized {orig_size / 1024:.1f}KB -> {new_size / 1024:.1f}KB (-{pct:.1f}%)"

    except Exception as e:
        if temp_dst.exists():
            temp_dst.unlink()
        # Fallback to copy
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        copy_metadata(src, dst, config.hardware.exiftool_path)
        return False, orig_size, f"Error ({e}), copied original"


def _optimize_with_pillow(src: Path, dst: Path, plan: OptimizationPlan, config: OptimizerConfig) -> bool:
    """Optimize image using Pillow."""
    try:
        with Image.open(src) as img:
            # Handle EXIF orientation
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass

            target_w = plan.target_width or img.width
            target_h = plan.target_height or img.height

            # Fast downsampling: use bilinear or bicubic (or lanczos if configured)
            filter_name = getattr(config, "image_resample_filter", "bilinear").upper()
            resample_filter = getattr(Image.Resampling, filter_name, Image.Resampling.BILINEAR)
            if (target_w < img.width or target_h < img.height) and target_w > 0 and target_h > 0:
                img = img.resize((target_w, target_h), resample_filter)

            target_fmt = (plan.target_format or img.format or "JPEG").upper()

            if target_fmt == "PNG":
                # Graphic / Screenshot PNG
                img.save(
                    dst,
                    format="PNG",
                    optimize=True,
                    compress_level=config.png_compression_level,
                )
            elif target_fmt in ("JPEG", "JPG"):
                # Photographic JPEG
                # Convert to RGB if RGBA/P/CMYK
                if img.mode in ("RGBA", "LA"):
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[-1])
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                # WhatsApp-like fast baseline JPEG encoding (quality 78-80, 4:2:0 subsampling)
                save_kwargs = {
                    "format": "JPEG",
                    "quality": plan.target_quality or config.jpeg_quality,
                    "subsampling": 2,  # 4:2:0 chroma subsampling (Pillow: 0=4:4:4, 1=4:2:2, 2=4:2:0)
                }
                # Preserve embedded EXIF if present without external process overhead
                exif_data = img.info.get("exif")
                if exif_data:
                    save_kwargs["exif"] = exif_data

                img.save(dst, **save_kwargs)
            elif target_fmt == "WEBP":
                img.save(
                    dst,
                    format="WEBP",
                    quality=config.webp_quality,
                    method=6,  # Highest compression effort
                )
            else:
                img.save(dst, optimize=True)

            return True
    except Exception:
        return False


def _optimize_with_sips(src: Path, dst: Path, plan: OptimizationPlan, config: OptimizerConfig) -> bool:
    """Optimize image using macOS sips tool (ideal for HEIC/HEIF)."""
    sips = config.hardware.sips_path
    if not sips:
        return False

    try:
        # Convert HEIC to high-quality JPEG for maximum size reduction & compatibility
        target_fmt = "jpeg"
        cmd = [
            sips,
            "-s", "format", target_fmt,
            "-s", "formatOptions", str(plan.target_quality or config.jpeg_quality),
        ]

        if plan.target_width and plan.target_height:
            max_dim = max(plan.target_width, plan.target_height)
            cmd.extend(["-Z", str(max_dim)])

        cmd.extend([str(src), "--out", str(dst)])
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False
