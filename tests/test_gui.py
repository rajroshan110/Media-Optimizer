"""Tests for GUI fallbacks and web interface."""

import unittest
from unittest.mock import patch
from media_optimizer.web_gui import OptimizerState, find_free_port, format_bytes
import media_optimizer.gui as gui_module


class TestGUIFallback(unittest.TestCase):
    def test_format_bytes(self):
        self.assertEqual(format_bytes(500), "500 B")
        self.assertEqual(format_bytes(1024 * 50), "50.0 KB")
        self.assertEqual(format_bytes(1024 * 1024 * 5), "5.0 MB")
        self.assertEqual(format_bytes(1024 * 1024 * 1024 * 2), "2.00 GB")

    def test_optimizer_state_lifecycle(self):
        state = OptimizerState()
        d = state.to_dict()
        self.assertFalse(d["running"])
        self.assertEqual(d["current"], 0)
        self.assertEqual(d["total"], 0)

        state.reset()
        d2 = state.to_dict()
        self.assertTrue(d2["running"])

    def test_find_free_port(self):
        port = find_free_port(8900)
        self.assertGreaterEqual(port, 8900)
        self.assertLess(port, 65535)

    def test_gui_routing_when_force_web(self):
        with patch("media_optimizer.web_gui.launch_web_gui") as mock_web:
            gui_module.launch_gui(None, force_web=True)
            mock_web.assert_called_once_with(None)

    def test_gui_routing_when_no_tkinter(self):
        with patch.object(gui_module, "HAS_TKINTER", False):
            with patch("media_optimizer.web_gui.launch_web_gui") as mock_web:
                gui_module.launch_gui(None, force_web=False)
                mock_web.assert_called_once_with(None)

    def test_gui_fallback_when_tk_init_fails(self):
        with patch.object(gui_module, "HAS_TKINTER", True):
            with patch("media_optimizer.gui.tk.Tk", side_effect=Exception("Display connection failed")):
                with patch("media_optimizer.web_gui.launch_web_gui") as mock_web:
                    gui_module.launch_gui("/test/path", force_web=False)
                    mock_web.assert_called_once_with("/test/path")


if __name__ == "__main__":
    unittest.main()
