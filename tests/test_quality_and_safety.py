import os
import platform
import sys
import time
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from PIL import Image

from media_optimizer.config import get_default_config, load_user_config, save_user_config
from media_optimizer.core.analyzer import DecisionAction, MediaType, OptimizationPlan
from media_optimizer.core.image_opt import _find_best_quality_in_least_size, optimize_image
from media_optimizer.core.journal import Journal, BatchSummary, compute_file_fingerprint
from media_optimizer.core.metadata import preserve_timestamps, set_macos_birthtime, validate_metadata
from media_optimizer.core.perceptual import compute_image_ssim, compute_image_psnr, get_calibrated_target_ssim
from media_optimizer.env_check import check_environment, main as env_check_main
from media_optimizer.pipeline import OptimizationPipeline, TaskItem


class TestQualityAndSafety(unittest.TestCase):

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.config = get_default_config()
        self.config.auto_quality = True
        self.config.deep_mode = False

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_sample_image(self, path: Path, size=(400, 300), color=(100, 150, 200)):
        img = Image.new("RGB", size, color=color)
        img.save(path, "JPEG", quality=90)
        return path

    # -------------------------------------------------------------------------
    # 1. Resume Safety Invalidation
    # -------------------------------------------------------------------------

    def test_resume_safety_invalidation_on_size_change(self):
        """Journal must invalidate cached entry if source file size changes at the same path."""
        journal_path = self.work_dir / "journal.db"
        journal = Journal(journal_path)

        src_path = self.work_dir / "photo.jpg"
        self._create_sample_image(src_path, (200, 200))
        dst_path = self.work_dir / "photo_out.jpg"
        self._create_sample_image(dst_path, (200, 200))

        task = TaskItem(
            src_path=src_path,
            rel_path="photo_out.jpg",
            dst_path=dst_path,
            media_type=MediaType.IMAGE,
            size_bytes=src_path.stat().st_size,
            mtime=src_path.stat().st_mtime,
        )
        journal.record_completed(
            rel_path="photo_out.jpg",
            orig_size=task.size_bytes,
            opt_size=dst_path.stat().st_size,
            mtime=task.mtime,
            reason="test",
            config_hash=self.config.get_config_hash(),
            src_path=str(src_path.resolve()),
        )

        # Confirm recognized initially
        self.assertIsNotNone(journal.is_already_done(task, self.config.get_config_hash(), self.work_dir))

        # Modify source file size (append bytes)
        with open(src_path, "ab") as f:
            f.write(b"EXTRABYTES")

        # Create new task object reflecting the modified file on disk
        modified_task = TaskItem(
            src_path=src_path,
            rel_path="photo_out.jpg",
            dst_path=dst_path,
            media_type=MediaType.IMAGE,
            size_bytes=src_path.stat().st_size,
            mtime=src_path.stat().st_mtime,
        )

        # Journal MUST invalidate and return None
        self.assertIsNone(
            journal.is_already_done(modified_task, self.config.get_config_hash(), self.work_dir),
            "Journal should invalidate when source size differs from original record."
        )

    def test_resume_safety_invalidation_on_mtime_change(self):
        """Journal must invalidate cached entry if source file mtime changes by more than 1 second."""
        journal_path = self.work_dir / "journal.db"
        journal = Journal(journal_path)

        src_path = self.work_dir / "photo_mtime.jpg"
        self._create_sample_image(src_path, (200, 200))
        dst_path = self.work_dir / "photo_mtime_out.jpg"
        self._create_sample_image(dst_path, (200, 200))

        task = TaskItem(
            src_path=src_path,
            rel_path="photo_mtime_out.jpg",
            dst_path=dst_path,
            media_type=MediaType.IMAGE,
            size_bytes=src_path.stat().st_size,
            mtime=src_path.stat().st_mtime,
        )
        journal.record_completed(
            rel_path="photo_mtime_out.jpg",
            orig_size=task.size_bytes,
            opt_size=dst_path.stat().st_size,
            mtime=task.mtime,
            reason="test",
            config_hash=self.config.get_config_hash(),
            src_path=str(src_path.resolve()),
        )

        # Artificially shift file mtime forward by 10 seconds
        future_time = time.time() + 10.0
        os.utime(src_path, (future_time, future_time))

        modified_task = TaskItem(
            src_path=src_path,
            rel_path="photo_mtime_out.jpg",
            dst_path=dst_path,
            media_type=MediaType.IMAGE,
            size_bytes=src_path.stat().st_size,
            mtime=src_path.stat().st_mtime,
        )

        # Journal MUST invalidate and return None
        self.assertIsNone(
            journal.is_already_done(modified_task, self.config.get_config_hash(), self.work_dir),
            "Journal should invalidate when source mtime differs by more than 1 second."
        )

    # -------------------------------------------------------------------------
    # 2. macOS Birthtime & Timestamp Preservation
    # -------------------------------------------------------------------------

    def test_timestamp_preservation_including_birthtime(self):
        """preserve_timestamps should preserve mtime, atime, and birthtime on macOS."""
        src_file = self.work_dir / "source_timestamps.jpg"
        dst_file = self.work_dir / "target_timestamps.jpg"
        self._create_sample_image(src_file, (100, 100))
        self._create_sample_image(dst_file, (100, 100))

        # Set specific historical timestamp
        past_time = 1577836800.0  # 2020-01-01 00:00:00 UTC
        os.utime(src_file, (past_time, past_time))

        if platform.system() == "Darwin":
            set_macos_birthtime(src_file, past_time)

        preserve_timestamps(src_file, dst_file)

        dst_stat = dst_file.stat()
        self.assertAlmostEqual(dst_stat.st_mtime, past_time, delta=2.0)
        if hasattr(dst_stat, "st_birthtime"):
            self.assertAlmostEqual(dst_stat.st_birthtime, past_time, delta=2.0)

    # -------------------------------------------------------------------------
    # 3. Perceptual SSIM & Auto-Quality Search
    # -------------------------------------------------------------------------

    def test_compute_fast_ssim_accuracy(self):
        """compute_image_ssim should return ~1.0 for identical images and lower for degraded."""
        img1 = Image.new("RGB", (300, 300), color=(150, 120, 90))
        img2 = img1.copy()

        # Identical images
        ssim_val = compute_image_ssim(img1, img2)
        self.assertAlmostEqual(ssim_val, 1.0, places=3)

        # Degraded image
        img3 = Image.new("RGB", (300, 300), color=(0, 0, 0))
        ssim_degraded = compute_image_ssim(img1, img3)
        self.assertLess(ssim_degraded, 0.5)

    def test_auto_quality_rate_distortion_search(self):
        """_find_best_quality_in_least_size should return an optimal quality factor >= target_ssim."""
        img = Image.new("RGB", (600, 400), color=(200, 100, 50))
        # Draw some variation so it's not a pure flat color
        for x in range(0, 600, 20):
            for y in range(0, 400, 20):
                img.putpixel((x, y), ((x % 255), (y % 255), 100))

        dst_path = self.work_dir / "rd_test.jpg"
        save_kwargs = {"format": "JPEG", "optimize": True}
        quality, ssim = _find_best_quality_in_least_size(
            img=img,
            ref_img=img,
            dst=dst_path,
            save_kwargs=save_kwargs,
            target_ssim=0.92,
            baseline_quality=80,
        )

        self.assertGreaterEqual(quality, 56)
        self.assertLessEqual(quality, 92)
        self.assertGreaterEqual(ssim, 0.88)

    def test_optimize_image_with_auto_quality(self):
        """optimize_image with auto_quality enabled produces valid output with quality >= min_ssim."""
        src_path = self.work_dir / "auto_q_test.jpg"
        dst_path = self.work_dir / "auto_q_out.jpg"

        # Create textured image
        img = Image.new("RGB", (800, 600), color=(100, 150, 200))
        for x in range(0, 800, 10):
            img.putpixel((x, 300), (255, 255, 255))
        img.save(src_path, "JPEG", quality=95)

        plan = OptimizationPlan(
            action=DecisionAction.OPTIMIZE,
            media_type=MediaType.IMAGE,
            reason="Test auto quality",
            target_format="JPEG",
            target_quality=80,
        )

        self.config.auto_quality = True
        self.config.target_ssim = 0.92
        self.config.min_ssim_threshold = 0.88

        success, new_size, msg = optimize_image(src_path, dst_path, plan, self.config)
        self.assertTrue(success)
        self.assertTrue(dst_path.exists())
        self.assertLess(dst_path.stat().st_size, src_path.stat().st_size)

    # -------------------------------------------------------------------------
    # 4. Config Persistence and Hash Sensitivity
    # -------------------------------------------------------------------------

    def test_config_auto_quality_hash_and_persistence(self):
        """auto_quality setting must alter config hash and round-trip through settings file."""
        cfg = get_default_config()
        cfg.auto_quality = True
        hash_default = cfg.get_config_hash()

        # Toggle auto_quality off
        cfg.auto_quality = False
        hash_manual = cfg.get_config_hash()
        self.assertNotEqual(hash_default, hash_manual, "Config hash must differ when auto_quality changes.")

        # Round trip through temporary file
        with NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_cfg_path = Path(tf.name)

        try:
            save_user_config(cfg, temp_cfg_path)
            loaded_cfg = get_default_config()
            load_user_config(loaded_cfg, temp_cfg_path)
            self.assertFalse(loaded_cfg.auto_quality)
        finally:
            if temp_cfg_path.exists():
                temp_cfg_path.unlink()

    def test_config_deep_mode_hash_and_persistence(self):
        """deep_mode setting must alter config hash and round-trip through settings file."""
        cfg = get_default_config()
        cfg.deep_mode = False
        hash_default = cfg.get_config_hash()

        # Toggle deep_mode on
        cfg.deep_mode = True
        hash_deep = cfg.get_config_hash()
        self.assertNotEqual(hash_default, hash_deep, "Config hash must differ when deep_mode changes.")

        # Round trip through temporary file
        with NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_cfg_path = Path(tf.name)

        try:
            save_user_config(cfg, temp_cfg_path)
            loaded_cfg = get_default_config()
            load_user_config(loaded_cfg, temp_cfg_path)
            self.assertTrue(loaded_cfg.deep_mode)
        finally:
            if temp_cfg_path.exists():
                temp_cfg_path.unlink()

    # -------------------------------------------------------------------------
    # 5. Environment Check Diagnostics
    # -------------------------------------------------------------------------

    def test_environment_check_diagnostics(self):
        """check_environment must return valid status dictionaries for all key tools."""
        status = check_environment()
        self.assertIn("python_ok", status)
        self.assertIn("pillow_ok", status)
        self.assertIn("ffmpeg_ok", status)
        self.assertIn("sips_ok", status)
        self.assertIn("hevc_vt_ok", status)
        self.assertIn("exiftool_ok", status)

        # Python and Pillow are guaranteed present in current running environment
        self.assertTrue(status["python_ok"])
        self.assertTrue(status["pillow_ok"])

    def test_env_check_cli_entrypoint(self):
        """env_check.main() entrypoint must execute without raising exceptions."""
        try:
            env_check_main()
        except SystemExit as e:
            self.assertEqual(e.code, 0)

    # -------------------------------------------------------------------------
    # 6. Local-Window SSIM & Calibrated Thresholds
    # -------------------------------------------------------------------------

    def test_local_window_ssim_sensitivity(self):
        """Local SSIM should return 1.0 for identical images and drop sharply on degraded regions."""
        base_img = Image.new("RGB", (200, 200), color=(120, 120, 120))
        # Add high-contrast local pattern (simulating text/edges)
        for x in range(50, 150, 10):
            for y in range(50, 150):
                base_img.putpixel((x, y), (240, 240, 240))

        # 1. Identical image
        ssim_ident = compute_image_ssim(base_img, base_img.copy())
        self.assertAlmostEqual(ssim_ident, 1.0, places=3)

        # 2. Severely degraded local region
        degraded = base_img.copy()
        for x in range(50, 150):
            for y in range(50, 150):
                degraded.putpixel((x, y), (120, 120, 120))  # Erased text pattern

        ssim_deg = compute_image_ssim(base_img, degraded)
        self.assertLess(ssim_deg, 0.85, "Local SSIM should detect erased high-contrast detail.")

    def test_calibrated_thresholds(self):
        """Perceptual targets must be calibrated by content type."""
        self.assertGreater(get_calibrated_target_ssim("screenshot"), get_calibrated_target_ssim("photo"))
        self.assertGreater(get_calibrated_target_ssim("graphic"), get_calibrated_target_ssim("video"))
        self.assertAlmostEqual(get_calibrated_target_ssim("photo"), 0.915, places=3)
        self.assertAlmostEqual(get_calibrated_target_ssim("screenshot"), 0.965, places=3)

    # -------------------------------------------------------------------------
    # 7. Binary Search Rate-Distortion Convergence
    # -------------------------------------------------------------------------

    def test_binary_search_rate_distortion_convergence(self):
        """_find_best_quality_in_least_size should converge to the lowest quality meeting target_ssim."""
        src_path = self.work_dir / "gradient.jpg"
        img = Image.new("RGB", (300, 300))
        for x in range(300):
            for y in range(300):
                img.putpixel((x, y), ((x + y) % 256, (x * 2) % 256, (y * 2) % 256))
        img.save(src_path, "JPEG", quality=92)

        out_path = self.work_dir / "binary_out.jpg"
        save_kwargs = {"format": "JPEG", "subsampling": 2}

        # Request target SSIM
        target = 0.91
        best_q, best_ssim = _find_best_quality_in_least_size(
            img, img, out_path, save_kwargs, target_ssim=target, baseline_quality=80
        )

        self.assertTrue(out_path.exists())
        self.assertGreaterEqual(best_ssim, target - 0.02)
        self.assertGreaterEqual(best_q, 48)
        self.assertLessEqual(best_q, 94)

    # -------------------------------------------------------------------------
    # 8. Intelligent PNG Optimization (Lossless & Palette)
    # -------------------------------------------------------------------------

    def test_intelligent_png_optimization(self):
        """PNG optimization should preserve transparency and optimize graphics cleanly."""
        png_path = self.work_dir / "graphic.png"
        img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        # Draw some solid colored shapes with transparent background
        for x in range(20, 100):
            for y in range(20, 100):
                img.putpixel((x, y), (255, 0, 0, 255))
        img.save(png_path, "PNG")

        dst_path = self.work_dir / "graphic_opt.png"
        plan = OptimizationPlan(
            action=DecisionAction.OPTIMIZE,
            media_type=MediaType.IMAGE,
            reason="Graphic/Screenshot PNG optimization",
            target_format="PNG",
            target_width=200,
            target_height=200,
        )

        success, final_sz, msg = optimize_image(png_path, dst_path, plan, self.config)
        self.assertTrue(success)
        self.assertTrue(dst_path.exists())
        with Image.open(dst_path) as out_img:
            self.assertEqual(out_img.format, "PNG")
            # Alpha transparency must be preserved
            self.assertIn("A", out_img.mode)

    # -------------------------------------------------------------------------
    # 9. Content Fingerprint Resume Safety
    # -------------------------------------------------------------------------

    def test_content_fingerprint_invalidation(self):
        """Journal must invalidate cache if file content changes even if mtime and size are restored."""
        journal_path = self.work_dir / "journal_fp.db"
        journal = Journal(journal_path)

        src_path = self.work_dir / "target_fp.jpg"
        self._create_sample_image(src_path, (200, 200))
        dst_path = self.work_dir / "target_fp_out.jpg"
        self._create_sample_image(dst_path, (200, 200))

        fp_orig = compute_file_fingerprint(src_path)
        self.assertEqual(len(fp_orig), 32)

        task = TaskItem(
            src_path=src_path,
            rel_path="target_fp_out.jpg",
            dst_path=dst_path,
            media_type=MediaType.IMAGE,
            size_bytes=src_path.stat().st_size,
            mtime=src_path.stat().st_mtime,
        )
        journal.record_completed(
            rel_path="target_fp_out.jpg",
            orig_size=task.size_bytes,
            opt_size=dst_path.stat().st_size,
            mtime=task.mtime,
            reason="test",
            config_hash=self.config.get_config_hash(),
            src_path=str(src_path.resolve()),
            deep_mode=True,
        )

        # Cache is valid initially
        self.assertIsNotNone(journal.is_already_done(task, self.config.get_config_hash(), self.work_dir, deep_mode=True))

        # Overwrite content with different bytes but keep EXACT same length and mtime
        st = src_path.stat()
        with open(src_path, "r+b") as f:
            f.seek(100)
            f.write(b"CORRUPT_BYTES_DATA")
        os.utime(src_path, (st.st_atime, st.st_mtime))

        # Size and mtime are identical
        self.assertEqual(src_path.stat().st_size, task.size_bytes)
        self.assertAlmostEqual(src_path.stat().st_mtime, task.mtime, places=2)

        # But fingerprint has changed!
        fp_new = compute_file_fingerprint(src_path)
        self.assertNotEqual(fp_orig, fp_new)

        # Journal must invalidate when deep_mode=True!
        self.assertIsNone(
            journal.is_already_done(task, self.config.get_config_hash(), self.work_dir, deep_mode=True),
            "Journal should detect fingerprint mismatch and invalidate cache."
        )

    def test_auto_mode_fast_path_skips_expensive_ssim(self):
        """Auto mode with deep_mode=False (default) encodes once and skips SSIM eval."""
        self.assertFalse(self.config.deep_mode)
        self.assertTrue(self.config.auto_quality)

        src_path = self.work_dir / "fast_photo.jpg"
        self._create_sample_image(src_path, (400, 400))
        dst_path = self.work_dir / "fast_photo_out.jpg"

        plan = OptimizationPlan(
            action=DecisionAction.OPTIMIZE,
            media_type=MediaType.IMAGE,
            reason="Auto photographic JPEG compression",
            target_format="JPEG",
            target_quality=80,
            target_width=400,
            target_height=400,
        )

        success, final_sz, msg = optimize_image(src_path, dst_path, plan, self.config)
        self.assertTrue(success)
        self.assertTrue(dst_path.exists())
        self.assertGreater(final_sz, 0)

    # -------------------------------------------------------------------------
    # 10. Field-by-Field Metadata Validation
    # -------------------------------------------------------------------------

    def test_field_by_field_metadata_validation(self):
        """validate_metadata should accurately report preserved and dropped fields."""
        src_path = self.work_dir / "meta_src.jpg"
        dst_path = self.work_dir / "meta_dst.jpg"
        self._create_sample_image(src_path, (100, 100))
        self._create_sample_image(dst_path, (100, 100))

        report = validate_metadata(src_path, dst_path, exiftool_path=self.config.hardware.exiftool_path)
        self.assertIn("preserved", report)
        self.assertIn("dropped", report)
        self.assertIn("status", report)

    # -------------------------------------------------------------------------
    # 11. Resolution vs Compression Breakdown
    # -------------------------------------------------------------------------

    def test_resolution_vs_compression_breakdown(self):
        """BatchSummary report should clearly separate spatial resizing vs codec efficiency."""
        summary = BatchSummary(
            total_files=10,
            completed=8,
            skipped=2,
            failed=0,
            original_bytes=100 * 1024 * 1024,
            optimized_bytes=40 * 1024 * 1024,
            saved_bytes=60 * 1024 * 1024,
            reduction_percent=60.0,
            res_saved_bytes=35 * 1024 * 1024,
            codec_saved_bytes=25 * 1024 * 1024,
        )
        report = summary.format_report()
        self.assertIn("Spatial Resizing", report)
        self.assertIn("Codec Efficiency", report)
        self.assertIn("Space saved:", report)


if __name__ == "__main__":
    unittest.main()
