import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from media_optimizer.config import get_default_config
from media_optimizer.pipeline import OptimizationPipeline


class TestPipeline(unittest.TestCase):

    def setUp(self):
        self.config = get_default_config()
        # Use single thread for deterministic testing
        self.config.image_workers = 2
        self.config.video_workers = 1

        self.temp_in = TemporaryDirectory()
        self.temp_out = TemporaryDirectory()
        self.in_dir = Path(self.temp_in.name)
        self.out_dir = Path(self.temp_out.name)

        # Populate test files
        # 1. Root large JPEG
        self._create_photo(self.in_dir / "large_photo.jpg", 1600, 1200)

        # 2. Subdirectory with a tiny image
        sub1 = self.in_dir / "vacation" / "2026"
        sub1.mkdir(parents=True, exist_ok=True)
        self._create_small_photo(sub1 / "tiny_photo.jpg")

        # 3. Subdirectory with an uncompressed PNG screenshot
        sub2 = self.in_dir / "documents"
        sub2.mkdir(parents=True, exist_ok=True)
        self._create_png(sub2 / "screenshot.png")

    def tearDown(self):
        self.temp_in.cleanup()
        self.temp_out.cleanup()

    def _create_photo(self, path: Path, w: int, h: int):
        img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
        img.save(path, "JPEG", quality=98)

    def _create_small_photo(self, path: Path):
        img = Image.new("RGB", (400, 300), color=(120, 80, 200))
        img.save(path, "JPEG", quality=50)

    def _create_png(self, path: Path):
        img = Image.new("RGBA", (1500, 1500), color=(250, 250, 250, 255))
        for i in range(0, 1500, 50):
            for j in range(0, 1500, 50):
                img.putpixel((i, j), (0, 0, 0, 255))
        img.save(path, "PNG", compress_level=0)

    def test_batch_execution_and_structure_preservation(self):
        """Pipeline should replicate directory structure, optimize files, and leave originals untouched."""
        pipeline = OptimizationPipeline(self.config)
        summary = pipeline.process_batch(self.in_dir, self.out_dir)

        # Original files must exist and remain untouched
        self.assertTrue((self.in_dir / "large_photo.jpg").exists())
        self.assertTrue((self.in_dir / "vacation" / "2026" / "tiny_photo.jpg").exists())
        self.assertTrue((self.in_dir / "documents" / "screenshot.png").exists())

        # Output files must exist in mirrored hierarchy
        out_photo = self.out_dir / "large_photo.jpg"
        out_tiny = self.out_dir / "vacation" / "2026" / "tiny_photo.jpg"
        out_png = self.out_dir / "documents" / "screenshot.png"

        self.assertTrue(out_photo.exists())
        self.assertTrue(out_tiny.exists())
        self.assertTrue(out_png.exists())

        # Large photo and uncompressed PNG should be smaller
        self.assertLess(out_photo.stat().st_size, (self.in_dir / "large_photo.jpg").stat().st_size)
        self.assertLess(out_png.stat().st_size, (self.in_dir / "documents" / "screenshot.png").stat().st_size)

        # Summary assertions
        self.assertEqual(summary.total_files, 3)
        self.assertGreater(summary.saved_bytes, 0)
        self.assertGreater(summary.reduction_percent, 0.0)

    def test_resumability(self):
        """Re-running the pipeline on an already-processed batch should skip completed files."""
        pipeline = OptimizationPipeline(self.config)
        # First run
        summary1 = pipeline.process_batch(self.in_dir, self.out_dir)
        self.assertGreater(summary1.completed, 0)

        # Second run: should recognize all files are already done
        summary2 = pipeline.process_batch(self.in_dir, self.out_dir)
        self.assertEqual(summary2.failed, 0)
        self.assertEqual(summary2.total_files, 3)
        # File sizes should match summary1
        self.assertEqual(summary2.saved_bytes, summary1.saved_bytes)

    def test_non_mp4_video_resumability(self):
        """Non-mp4 videos (.mov/.mkv) converted to .mp4 should be recognized as done on resume."""
        if not self.config.hardware.ffmpeg_path:
            return
        import subprocess
        # Generate a small .mov video
        mov_src = self.in_dir / "sample.mov"
        subprocess.run([
            self.config.hardware.ffmpeg_path, "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=15",
            "-c:v", "libx264", "-crf", "30", "-pix_fmt", "yuv420p",
            str(mov_src),
        ], check=True)

        pipeline = OptimizationPipeline(self.config)
        s1 = pipeline.process_batch(self.in_dir, self.out_dir)

        # Re-run: journal should recognize the output (.mp4) and skip it
        s2 = pipeline.process_batch(self.in_dir, self.out_dir)
        self.assertEqual(s2.failed, 0)
        self.assertEqual(s2.completed, s1.completed)

    def test_single_file_processing(self):
        """Pipeline should support optimizing a single file directly."""
        single_photo = self.in_dir / "single_test.jpg"
        self._create_photo(single_photo, 1920, 1080)

        pipeline = OptimizationPipeline(self.config)
        summary = pipeline.process_batch(single_photo, self.out_dir)

        self.assertEqual(summary.total_files, 1)
        self.assertEqual(summary.completed, 1)
        out_file = self.out_dir / "single_test.jpg"
        self.assertTrue(out_file.exists())
        self.assertLess(out_file.stat().st_size, single_photo.stat().st_size)

    def test_file_list_processing(self):
        """Pipeline should support a list of selected files."""
        f1 = self.in_dir / "file1.jpg"
        f2 = self.in_dir / "file2.jpg"
        self._create_photo(f1, 1600, 1200)
        self._create_photo(f2, 1600, 1200)

        pipeline = OptimizationPipeline(self.config)
        summary = pipeline.process_batch([f1, f2], self.out_dir)

        self.assertEqual(summary.total_files, 2)
        self.assertEqual(summary.completed, 2)
        self.assertTrue((self.out_dir / "file1.jpg").exists())
        self.assertTrue((self.out_dir / "file2.jpg").exists())


    def test_single_file_same_folder_overwrite_prevention(self):
        """When output directory is the file's parent folder, original file must be untouched and _optimized appended."""
        single_photo = self.in_dir / "target_photo.jpg"
        self._create_photo(single_photo, 1920, 1080)
        orig_bytes = single_photo.read_bytes()

        pipeline = OptimizationPipeline(self.config)
        # Output directly into the same folder
        summary = pipeline.process_batch(single_photo, self.in_dir)

        self.assertEqual(summary.total_files, 1)
        self.assertEqual(summary.completed, 1)

        # Original file MUST remain 100% identical and untouched
        self.assertTrue(single_photo.exists())
        self.assertEqual(single_photo.read_bytes(), orig_bytes)

        # Optimized file must exist with _optimized suffix
        opt_photo = self.in_dir / "target_photo_optimized.jpg"
        self.assertTrue(opt_photo.exists())
        self.assertLess(opt_photo.stat().st_size, len(orig_bytes))

    def test_file_list_same_folder_overwrite_prevention(self):
        """When output directory is the files' parent folder, all originals are untouched and _optimized appended."""
        f1 = self.in_dir / "item_a.jpg"
        f2 = self.in_dir / "item_b.jpg"
        self._create_photo(f1, 1600, 1200)
        self._create_photo(f2, 1600, 1200)
        orig_f1_bytes = f1.read_bytes()
        orig_f2_bytes = f2.read_bytes()

        pipeline = OptimizationPipeline(self.config)
        summary = pipeline.process_batch([f1, f2], self.in_dir)

        self.assertEqual(summary.total_files, 2)
        self.assertEqual(summary.completed, 2)

        # Originals untouched
        self.assertEqual(f1.read_bytes(), orig_f1_bytes)
        self.assertEqual(f2.read_bytes(), orig_f2_bytes)

        # Suffix files created
        self.assertTrue((self.in_dir / "item_a_optimized.jpg").exists())
        self.assertTrue((self.in_dir / "item_b_optimized.jpg").exists())


if __name__ == "__main__":
    unittest.main()
