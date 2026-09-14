"""Unit tests for real-time activity log generation and WhatsApp profile tuning."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from media_optimizer.config import get_default_config
from media_optimizer.core.analyzer import DecisionAction, MediaType, analyze_file, analyze_image, analyze_video
from media_optimizer.core.image_opt import optimize_image
from media_optimizer.pipeline import OptimizationPipeline
from media_optimizer.web_gui import OptimizerState


class TestActivityLogAndWhatsAppProfile(unittest.TestCase):

    def setUp(self):
        self.config = get_default_config()
        self.config.image_workers = 2
        self.config.video_workers = 1
        self.temp_in = TemporaryDirectory()
        self.temp_out = TemporaryDirectory()
        self.in_dir = Path(self.temp_in.name)
        self.out_dir = Path(self.temp_out.name)

    def tearDown(self):
        self.temp_in.cleanup()
        self.temp_out.cleanup()

    def _create_photo(self, path: Path, w: int, h: int, quality: int = 95):
        img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
        img.save(path, "JPEG", quality=quality)

    def test_pipeline_emits_activity_logs(self):
        """Pipeline should emit real-time activity logs for scanning, start, completion, and summary."""
        # Create test photos
        p1 = self.in_dir / "photo1.jpg"
        p2 = self.in_dir / "photo2.jpg"
        self._create_photo(p1, 2400, 1800)
        self._create_photo(p2, 1200, 900)

        pipeline = OptimizationPipeline(self.config)
        activity_events = []
        started_items = []
        finished_items = []

        def on_activity(msg, level):
            activity_events.append({"msg": msg, "level": level})

        def on_item_start(rel_p, orig_sz, reason):
            started_items.append({"rel_p": rel_p, "orig_sz": orig_sz, "reason": reason})

        def on_item_finish(rel_p, orig_sz, opt_sz, msg):
            finished_items.append({"rel_p": rel_p, "msg": msg})

        summary = pipeline.process_batch(
            self.in_dir,
            self.out_dir,
            on_activity=on_activity,
            on_item_start=on_item_start,
            on_item_finish=on_item_finish,
        )

        # 1. Activity log should NOT be empty!
        self.assertGreater(len(activity_events), 0)

        # 2. Check for scanning and discovery logs
        msgs = [e["msg"] for e in activity_events]
        levels = [e["level"] for e in activity_events]
        print("LEVELS:", levels)

        self.assertTrue(any("Scanning" in m for m in msgs))
        self.assertTrue(any("Discovered 2 media items" in m for m in msgs))

        # 3. Check for item start and item complete logs
        self.assertIn("start", levels)
        self.assertTrue(any("saved" in l or "copied" in l for l in levels))
        self.assertIn("complete", levels)

        # 4. Item callbacks
        self.assertEqual(len(started_items), 2)
        self.assertEqual(len(finished_items), 2)

    def test_pipeline_resume_activity_logs(self):
        """When resuming a batch with already-completed files, informative logs should be emitted."""
        p1 = self.in_dir / "already_done.jpg"
        self._create_photo(p1, 2200, 1600)

        pipeline = OptimizationPipeline(self.config)
        pipeline.process_batch(self.in_dir, self.out_dir)

        # Run second time: should log resume information
        second_run_logs = []
        pipeline.process_batch(
            self.in_dir,
            self.out_dir,
            on_activity=lambda msg, lvl: second_run_logs.append({"msg": msg, "lvl": lvl})
        )

        msgs = [l["msg"] for l in second_run_logs]
        self.assertTrue(any("already" in m.lower() or "resuming" in m.lower() for m in msgs))

    def test_optimizer_state_logging(self):
        """Web GUI OptimizerState should collect logs with levels and timestamps."""
        state = OptimizerState()
        state.reset()
        self.assertEqual(len(state.logs), 0)

        state.add_log("info", "Starting test run")
        state.add_log("start", "Optimizing photo.jpg", rel_path="photo.jpg")
        state.add_log("saved", "Optimized 4MB -> 400KB (-90%)", rel_path="photo.jpg")

        d = state.to_dict()
        self.assertEqual(len(d["logs"]), 3)
        self.assertEqual(d["logs"][0]["level"], "info")
        self.assertEqual(d["logs"][1]["level"], "start")
        self.assertEqual(d["logs"][2]["level"], "saved")
        self.assertIn("photo.jpg", d["logs"][1]["rel_path"])
        self.assertTrue(all("time" in l for l in d["logs"]))

    def test_whatsapp_photo_profile_downscale_and_quality(self):
        """Oversized camera photo (e.g. 4032x3024) should be downscaled to 2048px max dimension at quality 80."""
        cam_photo = self.in_dir / "iphone_photo.jpg"
        # Simulate an iPhone photo: 4032x3024
        self._create_photo(cam_photo, 4032, 3024, quality=95)

        info, plan = analyze_image(cam_photo, self.config)
        self.assertEqual(plan.action, DecisionAction.OPTIMIZE)
        self.assertIn("Downscale", plan.reason)
        self.assertEqual(max(plan.target_width, plan.target_height), self.config.image_max_dimension)
        self.assertLessEqual(plan.target_width, 2048)
        self.assertLessEqual(plan.target_height, 2048)
        self.assertEqual(plan.target_quality, 80)

        out_photo = self.out_dir / "iphone_photo.jpg"
        success, new_size, msg = optimize_image(cam_photo, out_photo, plan, self.config)
        self.assertTrue(success)
        self.assertLess(new_size, cam_photo.stat().st_size)
        # Should save > 60%
        pct_saved = ((cam_photo.stat().st_size - new_size) / cam_photo.stat().st_size) * 100
        self.assertGreater(pct_saved, 60.0)

    def test_empty_directory_activity_logging(self):
        """Scanning an empty directory should emit clear 'No supported media files' activity logs."""
        empty_in = self.in_dir / "empty_folder"
        empty_in.mkdir()
        empty_out = self.out_dir / "empty_out"

        logs = []
        pipeline = OptimizationPipeline(self.config)
        summary = pipeline.process_batch(
            empty_in,
            empty_out,
            on_activity=lambda msg, lvl: logs.append({"msg": msg, "lvl": lvl}),
        )
        self.assertEqual(summary.total_files, 0)
        msgs = [l["msg"] for l in logs]
        self.assertTrue(any("No supported media files" in m for m in msgs))
        self.assertFalse(any("All files are already up-to-date" in m for m in msgs))

    def test_silent_video_analyzer_and_no_audio(self):
        """A video without audio should have target_audio_codec as None and encode with -an."""
        import subprocess
        silent_src = self.in_dir / "silent.mp4"
        cmd = [
            self.config.hardware.ffmpeg_path,
            "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(silent_src),
        ]
        subprocess.run(cmd, check=True)

        info, plan = analyze_video(silent_src, self.config)
        self.assertIsNone(plan.target_audio_codec)
        self.assertIsNone(plan.target_audio_bitrate)

    def test_web_gui_template_contains_initialization_and_smart_scroll(self):
        """Web GUI HTML template must initialize on DOMContentLoaded and check scroll position."""
        from media_optimizer.web_gui import HTML_TEMPLATE
        self.assertIn("DOMContentLoaded", HTML_TEMPLATE)
        self.assertIn("isNearBottom", HTML_TEMPLATE)
        self.assertIn("escapeHtml", HTML_TEMPLATE)

    def test_fast_resampling_option(self):
        """Image optimization must support bilinear, bicubic, and lanczos resampling options."""
        p = self.in_dir / "resample_test.jpg"
        self._create_photo(p, 3000, 2000)

        for f in ["bilinear", "bicubic", "lanczos"]:
            self.config.image_resample_filter = f
            info, plan = analyze_image(p, self.config)
            out_p = self.out_dir / f"resample_{f}.jpg"
            success, sz, msg = optimize_image(p, out_p, plan, self.config)
            self.assertTrue(success)
            self.assertTrue(out_p.exists())


if __name__ == "__main__":
    unittest.main()
