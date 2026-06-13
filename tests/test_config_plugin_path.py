import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from slicebug.config.config import Config


class ConfigPluginPathTest(unittest.TestCase):
    def test_windows_prefers_device_common(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_plugin = root / "plugins" / "device-common"
            next_plugin = root / "plugins" / "device-common-next"
            old_plugin.mkdir(parents=True)
            next_plugin.mkdir(parents=True)
            (old_plugin / "CricutDevice.exe").write_text("old", encoding="utf-8")
            (next_plugin / "CricutDevice.exe").write_text("next", encoding="utf-8")
            config = Config(str(root), None, None, None, None)

            with patch("slicebug.config.config.platform.system", return_value="Windows"):
                self.assertEqual(
                    config.device_plugin_path(),
                    str(old_plugin / "CricutDevice.exe"),
                )

    def test_windows_ignores_device_common_next_without_device_common(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            next_plugin = root / "plugins" / "device-common-next"
            next_plugin.mkdir(parents=True)
            (next_plugin / "CricutDevice.exe").write_text("next", encoding="utf-8")
            config = Config(str(root), None, None, None, None)

            with patch("slicebug.config.config.platform.system", return_value="Windows"):
                self.assertIsNone(config.device_plugin_path())

    def test_macos_uses_device_common(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_plugin = root / "plugins" / "device-common"
            next_plugin = root / "plugins" / "device-common-next"
            old_plugin.mkdir(parents=True)
            next_plugin.mkdir(parents=True)
            (old_plugin / "CricutDevice").write_text("old", encoding="utf-8")
            (next_plugin / "CricutDevice").write_text("next", encoding="utf-8")
            config = Config(str(root), None, None, None, None)

            with patch("slicebug.config.config.platform.system", return_value="Darwin"):
                self.assertEqual(
                    config.device_plugin_path(),
                    str(old_plugin / "CricutDevice"),
                )


if __name__ == "__main__":
    unittest.main()
