"""Unit tests for the benchmarking module (SSIM, PSNR, WhatsApp comparison)."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from media_optimizer.benchmark import MediaBenchmark, compute_image_ssim_psnr
from media_optimizer.config import get_default_config
from media_optimizer.core.analyzer import analyze_image
from media_optimizer.core.image_opt import optimize_image


class TestBenchmark(unittest.TestCase):

    def setUp(self):
        self.config = get_default_config()
        self.temp_dir = TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ssim_psnr_identical_images(self):
        """Identical images should have SSIM ~ 1.0 and high PSNR."""
        p1 = self.dir_path / "img1.jpg"
        p2 = self.dir_path / "img2.jpg"

        img = Image.frombytes("RGB", (400, 400), os.urandom(400 * 400 * 3))
        img.save(p1, "JPEG", quality=95)
        img.save(p2, "JPEG", quality=95)

        ssim, psnr = compute_image_ssim_psnr(p1, p2)
        self.assertAlmostEqual(ssim, 1.0, places=2)
        self.assertGreater(psnr, 50.0)

    def test_benchmark_report_generation(self):
        """MediaBenchmark should generate a formatted markdown comparison table."""
        orig_p = self.dir_path / "camera_photo.jpg"
        opt_p = self.dir_path / "opt_photo.jpg"

        img = Image.frombytes("RGB", (800, 800), os.urandom(800 * 800 * 3))
        img.save(orig_p, "JPEG", quality=98)

        info, plan = analyze_image(orig_p, self.config)
        optimize_image(orig_p, opt_p, plan, self.config)

        bench = MediaBenchmark()
        results = bench.compare(orig_p, opt_p)
        report = bench.format_markdown_report(results)

        self.assertIn("Benchmark Comparison", report)
        self.assertIn("File Size", report)
        self.assertIn("SSIM", report)
        self.assertIn("PSNR", report)


if __name__ == "__main__":
    unittest.main()
