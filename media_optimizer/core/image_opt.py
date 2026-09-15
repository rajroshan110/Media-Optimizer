"""Image optimization engine using Pillow and macOS sips."""

import io
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAS_HEIF = True
except ImportError:
    HAS_HEIF = False

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
        use_sips = ext in (".heic", ".heif") and config.hardware.sips_path and not HAS_HEIF

        eval_ssim = None
        chosen_q = None
        if use_sips:
            success = _optimize_with_sips(src, temp_dst, plan, config)
        else:
            success, eval_ssim, chosen_q = _optimize_with_pillow(src, temp_dst, plan, config)

        if not success or not temp_dst.exists():
            # Fallback: copy original
            if temp_dst.exists():
                temp_dst.unlink()
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            return True, orig_size, "Fallback to original"

        new_size = temp_dst.stat().st_size

        # Safety check: if new size doesn't save at least min_saving_ratio (e.g. 5%), keep original
        threshold = orig_size * (1.0 - config.min_saving_ratio)
        if new_size >= threshold:
            temp_dst.unlink()
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            return True, orig_size, f"Preserved original (savings < {config.min_saving_ratio*100:.0f}%)"

        # Perceptual quality guardrail: abort if structural similarity is below minimum acceptable threshold
        if eval_ssim is not None and eval_ssim < config.min_ssim_threshold:
            temp_dst.unlink()
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            return True, orig_size, f"Preserved original (perceptual quality guardrail: SSIM {eval_ssim:.2f} < {config.min_ssim_threshold})"

        # Apply metadata and atomic replace
        if temp_dst.suffix != dst.suffix:
            pass

        temp_dst.replace(dst)
        preserve_timestamps(src, dst)
        if use_sips and config.preserve_metadata and config.hardware.exiftool_path:
            copy_metadata(src, dst, config.hardware.exiftool_path)
            preserve_timestamps(src, dst)

        final_size = dst.stat().st_size
        saved_bytes = orig_size - final_size
        pct = (saved_bytes / orig_size) * 100.0 if orig_size > 0 else 0.0
        
        details = [f"-{pct:.1f}%"]
        if chosen_q is not None:
            details.append(f"Q:{chosen_q}")
        if eval_ssim is not None:
            details.append(f"SSIM:{eval_ssim:.2f}")
        detail_str = ", ".join(details)
        return True, final_size, f"Optimized {orig_size / 1024:.1f}KB -> {final_size / 1024:.1f}KB ({detail_str})"

    except Exception as e:
        if temp_dst.exists():
            temp_dst.unlink()
        # Fallback to copy
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        return False, orig_size, f"Error ({e}), copied original"


from media_optimizer.core.perceptual import compute_image_ssim, get_calibrated_target_ssim


def _find_best_quality_in_least_size(
    img: Image.Image,
    ref_img: Image.Image,
    dst: Path,
    save_kwargs: dict,
    target_ssim: float = 0.915,
    baseline_quality: int = 80,
    min_quality: int = 56,
    max_quality: int = 94,
) -> Tuple[int, float]:
    """Search for the minimum quality setting that delivers target perceptual fidelity.
    
    Uses binary search over the quality range [min_quality, max_quality] to locate
    the lowest quality factor where local MSSIM >= target_ssim.
    """
    def encode_and_eval(q: int) -> Tuple[bytes, float]:
        kw = dict(save_kwargs)
        kw["quality"] = q
        buf = io.BytesIO()
        img.save(buf, **kw)
        raw = buf.getvalue()
        buf.seek(0)
        with Image.open(buf) as cand:
            s = compute_image_ssim(ref_img, cand, eval_max_dimension=512)
        return raw, s

    # 1. First evaluate at baseline to establish known reference
    base_raw, base_ssim = encode_and_eval(baseline_quality)
    best_candidate = None

    if base_ssim >= target_ssim:
        best_candidate = (baseline_quality, base_raw, base_ssim)
        low = min_quality
        high = baseline_quality - 1
    else:
        low = baseline_quality + 1
        high = max_quality

    # 2. Binary search for lowest quality that achieves target_ssim
    while low <= high:
        mid = (low + high) // 2
        raw, ssim_val = encode_and_eval(mid)

        if ssim_val >= target_ssim:
            # Perceptually acceptable: record candidate and try lower quality to save space
            best_candidate = (mid, raw, ssim_val)
            
            # EARLY EXIT: If the score perfectly borders the target (within 0.005), stop searching
            if ssim_val <= target_ssim + 0.005:
                break
                
            high = mid - 1
        else:
            # Below target quality: must search higher
            low = mid + 1

    # If even max_quality didn't meet target, select the highest quality evaluated
    if best_candidate is None:
        raw_max, ssim_max = encode_and_eval(max_quality)
        if ssim_max >= base_ssim:
            best_candidate = (max_quality, raw_max, ssim_max)
        else:
            best_candidate = (baseline_quality, base_raw, base_ssim)

    best_q, best_raw, best_ssim = best_candidate

    with open(dst, "wb") as f:
        f.write(best_raw)

    return best_q, best_ssim


def _optimize_with_pillow(src: Path, dst: Path, plan: OptimizationPlan, config: OptimizerConfig) -> Tuple[bool, Optional[float], Optional[int]]:
    """Optimize image using Pillow with adaptive rate-distortion search (Deep Mode) or single-pass encode (Auto Mode)."""
    try:
        with Image.open(src) as orig_img:
            # Handle EXIF orientation
            try:
                img = ImageOps.exif_transpose(orig_img)
            except Exception:
                img = orig_img


            target_w = plan.target_width or img.width
            target_h = plan.target_height or img.height

            target_fmt = (plan.target_format or img.format or "JPEG").upper()
            eval_ssim = None
            chosen_q = None
            is_deep = getattr(config, "deep_mode", False)

            # Reference copy for perceptual validation — only needed in Deep Mode
            ref_eval = img.copy() if is_deep else None

            # Fast downsampling: use bilinear or bicubic (or lanczos if configured)
            filter_name = getattr(config, "image_resample_filter", "bilinear").upper()
            resample_filter = getattr(Image.Resampling, filter_name, Image.Resampling.BILINEAR)
            if (target_w < img.width or target_h < img.height) and target_w > 0 and target_h > 0:
                img = img.resize((target_w, target_h), resample_filter)


            is_graphic = any(k in plan.reason.lower() for k in ("graphic", "screenshot", "ui", "diagram"))
            calibrated_target = get_calibrated_target_ssim("graphic" if is_graphic else "photo")
            active_target_ssim = max(config.target_ssim, calibrated_target) if config.auto_quality else config.target_ssim

            if target_fmt == "PNG":
                # Intelligent PNG Optimization: Lossless vs Adaptive Palette Quantization
                buf_lossless = io.BytesIO()
                img.save(
                    buf_lossless,
                    format="PNG",
                    optimize=True,
                    compress_level=config.png_compression_level,
                )
                raw_lossless = buf_lossless.getvalue()

                best_png_raw = raw_lossless
                chosen_q = "lossless"

                # If Deep Mode + Auto Mode is on, evaluate whether adaptive palette quantization preserves visual fidelity
                # Fast path: Skip quantization for large photos (e.g. >3 Megapixels) as it's slow and always looks terrible
                is_huge_photo = (img.width * img.height > 3_000_000) and not is_graphic
                
                if is_deep and config.auto_quality and img.mode in ("RGB", "RGBA") and not is_huge_photo:
                    try:
                        # Attempt clean palette reduction (up to 256 colors)
                        quant_img = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG if not is_graphic else Image.Dither.NONE)
                        buf_quant = io.BytesIO()
                        quant_img.save(buf_quant, format="PNG", optimize=True, compress_level=config.png_compression_level)
                        raw_quant = buf_quant.getvalue()

                        # Verify structural similarity of quantized candidate
                        buf_quant.seek(0)
                        with Image.open(buf_quant) as cand_quant:
                            quant_ssim = compute_image_ssim(ref_eval, cand_quant, eval_max_dimension=512)

                        # Only use palette quantization if it meets strict graphic threshold and saves size
                        if quant_ssim >= calibrated_target and len(raw_quant) < len(raw_lossless) * 0.85:
                            best_png_raw = raw_quant
                            chosen_q = "palette-256"
                            eval_ssim = quant_ssim
                    except Exception:
                        pass

                with open(dst, "wb") as f:
                    f.write(best_png_raw)

                if is_deep and eval_ssim is None:
                    with Image.open(dst) as saved_img:
                        eval_ssim = compute_image_ssim(ref_eval, saved_img, eval_max_dimension=512)

            elif target_fmt in ("JPEG", "JPG"):
                # Photographic JPEG
                # Convert to RGB if RGBA/P/CMYK
                if img.mode in ("RGBA", "LA"):
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[-1])
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                save_kwargs = {
                    "format": "JPEG",
                    "subsampling": 2,  # 4:2:0 chroma subsampling
                }
                exif_data = img.info.get("exif")
                if exif_data:
                    save_kwargs["exif"] = exif_data

                base_q = plan.target_quality or config.jpeg_quality
                if is_deep and config.auto_quality:
                    chosen_q, eval_ssim = _find_best_quality_in_least_size(
                        img, ref_eval, dst, save_kwargs,
                        target_ssim=active_target_ssim,
                        baseline_quality=base_q
                    )
                else:
                    chosen_q = base_q
                    save_kwargs["quality"] = chosen_q
                    img.save(dst, **save_kwargs)
                    if is_deep:
                        with Image.open(dst) as saved_img:
                            eval_ssim = compute_image_ssim(ref_eval, saved_img, eval_max_dimension=512)
            elif target_fmt == "WEBP":
                img.save(
                    dst,
                    format="WEBP",
                    quality=config.webp_quality,
                    method=6,
                )
                if is_deep:
                    with Image.open(dst) as saved_img:
                        eval_ssim = compute_image_ssim(ref_eval, saved_img, eval_max_dimension=512)
                chosen_q = config.webp_quality
            else:
                img.save(dst, optimize=True)
                if is_deep:
                    with Image.open(dst) as saved_img:
                        eval_ssim = compute_image_ssim(ref_eval, saved_img, eval_max_dimension=512)

            return True, eval_ssim, chosen_q
    except Exception:
        return False, None, None


def _optimize_with_sips(src: Path, dst: Path, plan: OptimizationPlan, config: OptimizerConfig) -> bool:
    """Optimize image using macOS sips tool (ideal for HEIC/HEIF)."""
    sips = config.hardware.sips_path
    if not sips:
        return False

    try:
        # Check target format from plan (e.g., 'heic' or 'jpeg')
        target_fmt = (plan.target_format or "jpeg").lower()
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
