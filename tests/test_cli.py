"""Tests for CLI arguments and licensing output."""

import io
import sys
import unittest
from unittest.mock import patch

from media_optimizer.cli import main, LICENSE_TEXT


class TestCLI(unittest.TestCase):
    def test_cli_license_flag(self):
        with patch.object(sys, "argv", ["media-optimizer", "--license"]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                main()
                output = mock_stdout.getvalue()
                self.assertIn("MIT License", output)
                self.assertIn("Raj Roshan", output)
                self.assertIn("FFmpeg", output)
                self.assertIn("ExifTool", output)
                self.assertIn("docs/LICENSE.md", output)

    def test_cli_version_flag(self):
        with patch.object(sys, "argv", ["media-optimizer", "--version"]):
            with self.assertRaises(SystemExit) as cm:
                with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                    main()
            self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
