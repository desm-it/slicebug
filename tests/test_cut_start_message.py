import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from slicebug.cli.cut import (
    make_start_message,
    prepare_device_plugin_for_cut,
    resolve_device_plugin_path,
)
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

    def test_prepare_device_plugin_for_cut_prefers_windows_proxy(self):
        config = SimpleNamespace(plugin_root=lambda: "plugin-root")

        with patch(
            "slicebug.cli.cut.prepare_windows_device_plugin_proxy",
            return_value="proxy-path",
        ) as proxy, patch(
            "slicebug.cli.cut.prepare_windows_device_plugin_patch"
        ) as patcher, patch(
            "slicebug.cli.cut.platform.system",
            return_value="Windows",
        ), patch(
            "builtins.print"
        ) as print_fn:
            prepared = prepare_device_plugin_for_cut("source-path", config)

        self.assertEqual(prepared, "proxy-path")
        proxy.assert_called_once_with("source-path", "plugin-root")
        patcher.assert_not_called()
        print_fn.assert_called_once_with("Windows helper mode: proxy (proxy-path)")

    def test_prepare_device_plugin_for_cut_uses_patch_fallback(self):
        config = SimpleNamespace(plugin_root=lambda: "plugin-root")

        with patch(
            "slicebug.cli.cut.prepare_windows_device_plugin_proxy",
            return_value=None,
        ) as proxy, patch(
            "slicebug.cli.cut.prepare_windows_device_plugin_patch",
            return_value="patched-path",
        ) as patcher, patch(
            "slicebug.cli.cut.platform.system",
            return_value="Windows",
        ), patch(
            "builtins.print"
        ) as print_fn:
            prepared = prepare_device_plugin_for_cut("source-path", config)

        self.assertEqual(prepared, "patched-path")
        proxy.assert_called_once_with("source-path", "plugin-root")
        patcher.assert_called_once_with("source-path", "plugin-root")
        print_fn.assert_called_once_with("Windows helper mode: patch (patched-path)")

    def test_prepare_device_plugin_for_cut_logs_original_fallback(self):
        config = SimpleNamespace(plugin_root=lambda: "plugin-root")

        with patch(
            "slicebug.cli.cut.prepare_windows_device_plugin_proxy",
            return_value=None,
        ), patch(
            "slicebug.cli.cut.prepare_windows_device_plugin_patch",
            return_value="source-path",
        ), patch(
            "slicebug.cli.cut.platform.system",
            return_value="Windows",
        ), patch(
            "builtins.print"
        ) as print_fn:
            prepared = prepare_device_plugin_for_cut("source-path", config)

        self.assertEqual(prepared, "source-path")
        print_fn.assert_called_once_with("Windows helper mode: original (source-path)")


if __name__ == "__main__":
    unittest.main()
