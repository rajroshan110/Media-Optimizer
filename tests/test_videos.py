"""Tests for video analysis, VideoToolbox hardware acceleration, and optimization."""

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from media_optimizer.config import get_default_config
from media_optimizer.core.analyzer import DecisionAction, MediaType, analyze_video
from media_optimizer.core.video_opt import optimize_video


class TestVideoOptimization(unittest.TestCase):

    def setUp(self):
        self.config = get_default_config()
        self.temp_dir = TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _generate_high_bitrate_video(self, path: Path, width: int = 1920, height: int = 1080, duration: int = 2):
        """Generate a high-bitrate video using mandelbrot fractal generator."""
        cmd = [
            self.config.hardware.ffmpeg_path,
            "-v", "error",
            "-y",
            "-f", "lavfi",
            "-i", f"mandelbrot=size={width}x{height}:rate=30",
            "-t", str(duration),
            "-c:v", "libx264",
            "-crf", "10",
            "-pix_fmt", "yuv420p",
            str(path),
        ]
        subprocess.run(cmd, check=True)

    def _generate_small_video(self, path: Path):
        """Generate a tiny low-bitrate video."""
        cmd = [
            self.config.hardware.ffmpeg_path,
            "-v", "error",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=duration=1:size=320x240:rate=15",
            "-c:v", "libx264",
            "-crf", "30",
            "-pix_fmt", "yuv420p",
            str(path),
        ]
        subprocess.run(cmd, check=True)

    def test_already_small_video_is_preserved(self):
        """A tiny low-bitrate video should be preserved without generational loss."""
        small_path = self.dir_path / "tiny_sample.mp4"
        self._generate_small_video(small_path)

        info, plan = analyze_video(small_path, self.config)
        self.assertIsNotNone(info)
        self.assertEqual(plan.action, DecisionAction.COPY)
        self.assertIn("Already small video", plan.reason)

    def test_high_bitrate_video_optimized(self):
        """High bitrate video should be planned for optimization and reduced significantly."""
        vid_path = self.dir_path / "high_bitrate.mp4"
        dst_path = self.dir_path / "high_bitrate_opt.mp4"
        self._generate_high_bitrate_video(vid_path, 1920, 1080, duration=2)

        info, plan = analyze_video(vid_path, self.config)
        self.assertIsNotNone(info)
        self.assertEqual(info.width, 1920)
        self.assertEqual(info.height, 1080)
        self.assertEqual(plan.action, DecisionAction.OPTIMIZE)
        self.assertIn("hevc", plan.target_video_codec)

        # Transcode
        success, new_size, msg = optimize_video(vid_path, dst_path, plan, self.config)
        self.assertTrue(success)
        self.assertTrue(dst_path.exists())
        self.assertLess(new_size, vid_path.stat().st_size)
        self.assertIn("Optimized", msg)

    def test_portrait_video_not_upscaled(self):
        """Portrait/vertical video (720x1280) must never be upscaled to 1080x1920."""
        vid_path = self.dir_path / "portrait_720x1280.mp4"
        self._generate_high_bitrate_video(vid_path, 720, 1280, duration=2)

        info, plan = analyze_video(vid_path, self.config)
        self.assertIsNotNone(info)
        self.assertEqual(info.width, 720)
        self.assertEqual(info.height, 1280)
        # Dimensions must not be upscaled
        if plan.target_width is not None:
            self.assertLessEqual(plan.target_width, 720)
        if plan.target_height is not None:
            self.assertLessEqual(plan.target_height, 1280)


if __name__ == "__main__":
    unittest.main()
