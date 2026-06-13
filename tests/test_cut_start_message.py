import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from slicebug.cli.cut import make_start_message, resolve_device_plugin_path
from slicebug.cricut.protobufs.Bridge_pb2 import PBInteractionStatus, PBLogLevel
from slicebug.exceptions import UserError


class CutStartMessageTest(unittest.TestCase):
    def test_start_message_matches_design_space_envelope_on_macos(self):
        config = SimpleNamespace(
            keys=SimpleNamespace(settings8_raw=b"settings8-from-bootstrap")
        )

        with patch("slicebug.cli.cut.platform.system", return_value="Darwin"):
            message = make_start_message(config, PBInteractionStatus.riMATCUT)

        self.assertEqual(message.interaction, PBInteractionStatus.riMATCUT)
        self.assertEqual(message.logId, "DEVICE")
        self.assertEqual(message.logLevel, PBLogLevel.ERROR_LOGLEVEL)
        self.assertNotIn(b"\xc8\x08", message.SerializeToString())
        self.assertEqual(message.authData.settings8, "settings8-from-bootstrap")

    def test_start_message_requests_verbose_helper_logs_on_windows(self):
        config = SimpleNamespace(
            keys=SimpleNamespace(settings8_raw=b"settings8-from-bootstrap")
        )

        with patch("slicebug.cli.cut.platform.system", return_value="Windows"):
            message = make_start_message(config, PBInteractionStatus.riMATCUT)

        self.assertEqual(message.interaction, PBInteractionStatus.riMATCUT)
        self.assertEqual(message.logId, "DEVICE")
        self.assertEqual(message.logLevel, PBLogLevel.VERBOSE_LOGLEVEL)
        self.assertIn(b"\xc8\x08\x05", message.SerializeToString())

    def test_device_plugin_override_path_wins(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            override = Path(temp_dir) / "CricutDevice.exe"
            override.write_text("helper", encoding="utf-8")
            args = SimpleNamespace(device_plugin_path=str(override))
            config = SimpleNamespace(device_plugin_path=lambda: "configured")

            self.assertEqual(resolve_device_plugin_path(args, config), str(override))

    def test_missing_device_plugin_override_path_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            override = Path(temp_dir) / "missing" / "CricutDevice.exe"
            args = SimpleNamespace(device_plugin_path=str(override))
            config = SimpleNamespace(device_plugin_path=lambda: "configured")

            with self.assertRaises(UserError):
                resolve_device_plugin_path(args, config)

    def test_device_plugin_uses_configured_path_without_override(self):
        args = SimpleNamespace(device_plugin_path=None)
        config = SimpleNamespace(device_plugin_path=lambda: "configured")

        self.assertEqual(resolve_device_plugin_path(args, config), "configured")


if __name__ == "__main__":
    unittest.main()
