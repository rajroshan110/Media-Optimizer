"""Unit tests for the media analysis and decision engine."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from media_optimizer.config import get_default_config
from media_optimizer.core.analyzer import DecisionAction, MediaType, analyze_image


class TestAnalyzer(unittest.TestCase):

    def setUp(self):
        self.config = get_default_config()
        self.temp_dir = TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_small_efficient_jpeg_is_preserved(self):
        """A small image with low bpp should be marked COPY to avoid generational loss."""
        img_path = self.dir_path / "tiny_efficient.jpg"
        # Create a small image (e.g. 800x600) saved at low quality/small size (<100KB)
        img = Image.new("RGB", (800, 600), color=(100, 150, 200))
        img.save(img_path, "JPEG", quality=40)

        info, plan = analyze_image(img_path, self.config)
        self.assertIsNotNone(info)
        self.assertEqual(plan.action, DecisionAction.COPY)
        self.assertIn("Already", plan.reason)

    def test_large_high_bpp_photo_is_optimized(self):
        """A large high-entropy photo should be optimized."""
        img_path = self.dir_path / "high_res_photo.jpg"
        # Create noisy high entropy image
        img = Image.frombytes("RGB", (1600, 1200), os.urandom(1600 * 1200 * 3))
        img.save(img_path, "JPEG", quality=98)

        info, plan = analyze_image(img_path, self.config)
        self.assertIsNotNone(info)
        self.assertEqual(plan.action, DecisionAction.OPTIMIZE)
        self.assertEqual(plan.target_format, "JPEG")

    def test_oversized_dimensions_trigger_downscaling(self):
        """An image exceeding max_dimension should have target dimensions scaled down."""
        img_path = self.dir_path / "massive_photo.jpg"
        img = Image.new("RGB", (6000, 4000), color=(200, 100, 50))
        img.save(img_path, "JPEG", quality=95)

        info, plan = analyze_image(img_path, self.config)
        self.assertIsNotNone(info)
        self.assertEqual(plan.action, DecisionAction.OPTIMIZE)
        self.assertLessEqual(max(plan.target_width, plan.target_height), self.config.image_max_dimension)
        self.assertIn("Downscale", plan.reason)

    def test_graphic_screenshot_detected(self):
        """A flat graphic/screenshot PNG that is large should be optimized as PNG, while tiny ones are copied."""
        # 1. Tiny PNG (< 350KB) is already efficient
        img_path = self.dir_path / "tiny_screenshot.png"
        img = Image.new("RGBA", (500, 500), color=(255, 255, 255, 255))
        for y in range(50, 100):
            for x in range(50, 200):
                img.putpixel((x, y), (0, 0, 0, 255))
        img.save(img_path, "PNG")

        info, plan = analyze_image(img_path, self.config)
        self.assertIsNotNone(info)
        self.assertFalse(info.is_photographic)
        self.assertEqual(plan.action, DecisionAction.COPY)

        # 2. Large uncompressed screenshot PNG (> 350KB) should be optimized as PNG
        large_path = self.dir_path / "large_screenshot.png"
        large_img = Image.new("RGBA", (2000, 2000), color=(240, 240, 240, 255))
        # Add graphic elements
        for i in range(0, 2000, 40):
            for j in range(0, 2000, 40):
                large_img.putpixel((i, j), (50, 100, 150, 255))
        large_img.save(large_path, "PNG", compress_level=0)  # Uncompressed

        info2, plan2 = analyze_image(large_path, self.config)
        self.assertIsNotNone(info2)
        self.assertFalse(info2.is_photographic)
        self.assertEqual(plan2.action, DecisionAction.OPTIMIZE)
        self.assertEqual(plan2.target_format, "PNG")


if __name__ == "__main__":
    unittest.main()
