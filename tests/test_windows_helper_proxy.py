import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from slicebug.cricut import windows_helper_proxy
from slicebug.cricut.windows_helper_proxy import prepare_windows_device_plugin_proxy


def write_file(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)


def make_stub(root):
    stub = Path(root) / "bundled" / "electron.exe"
    write_file(stub, b"stub-exe")
    return stub


class WindowsHelperProxyTest(unittest.TestCase):
    def test_non_windows_returns_none(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "plugins" / "device-common" / "CricutDevice.exe"
            write_file(source, b"helper")

            with patch(
                "slicebug.cricut.windows_helper_proxy.platform.system",
                return_value="Darwin",
            ):
                prepared = prepare_windows_device_plugin_proxy(
                    str(source),
                    str(Path(temp_dir) / "plugins"),
                )

            self.assertIsNone(prepared)

    def test_windows_returns_none_without_bundled_stub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "plugins" / "device-common" / "CricutDevice.exe"
            write_file(source, b"helper")

            with patch(
                "slicebug.cricut.windows_helper_proxy.platform.system",
                return_value="Windows",
            ), patch(
                "slicebug.cricut.windows_helper_proxy.sys.executable",
                str(Path(temp_dir) / "no-stub" / "slicebug.exe"),
            ), patch.dict(os.environ, {}, clear=False):
                os.environ.pop(windows_helper_proxy._STUB_ENV_OVERRIDE, None)
                prepared = prepare_windows_device_plugin_proxy(
                    str(source),
                    str(Path(temp_dir) / "plugins"),
                )

            self.assertIsNone(prepared)

    def test_windows_proxy_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "plugins" / "device-common" / "CricutDevice.exe"
            write_file(source, b"helper")

            with patch(
                "slicebug.cricut.windows_helper_proxy.platform.system",
                return_value="Windows",
            ), patch.dict(
                os.environ,
                {"SLICEBUG_DISABLE_WINDOWS_HELPER_PROXY": "1"},
            ):
                prepared = prepare_windows_device_plugin_proxy(
                    str(source),
                    str(Path(temp_dir) / "plugins"),
                )

            self.assertIsNone(prepared)

    def test_windows_builds_proxy_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            plugin_root = temp_path / "plugins"
            source_dir = plugin_root / "device-common"
            source = source_dir / "CricutDevice.exe"
            write_file(source, b"helper")
            (source_dir / "support.dll").write_text("dll", encoding="utf-8")
            (source_dir / "logs").mkdir()
            (source_dir / "logs" / "bridge.log").write_text("old log", encoding="utf-8")
            stub = make_stub(temp_path)

            with patch(
                "slicebug.cricut.windows_helper_proxy.platform.system",
                return_value="Windows",
            ), patch.dict(
                os.environ,
                {windows_helper_proxy._STUB_ENV_OVERRIDE: str(stub)},
            ):
                prepared = prepare_windows_device_plugin_proxy(
                    str(source),
                    str(plugin_root),
                )

            prepared_path = Path(prepared)
            self.assertEqual(prepared_path.name, "electron.exe")
            self.assertEqual(prepared_path.read_bytes(), b"stub-exe")

            app_root = prepared_path.parent
            self.assertEqual(app_root.name, windows_helper_proxy._APP_ROOT_NAME)
            helper_dir = (
                app_root
                / "node_modules"
                / windows_helper_proxy._SCOPE_NAME
                / "device-common"
            )
            self.assertEqual((helper_dir / "CricutDevice.exe").read_bytes(), b"helper")
            self.assertEqual((helper_dir / "support.dll").read_text(), "dll")
            self.assertTrue((helper_dir / "logs").exists())
            self.assertFalse((helper_dir / "logs" / "bridge.log").exists())

            # The stub-based proxy carries no Python runtime, _pth, or bridge script.
            self.assertFalse((app_root / "electron._pth").exists())
            self.assertFalse((app_root / "bridge").exists())
            self.assertTrue(
                (
                    prepared_path.parents[1] / windows_helper_proxy.PROXY_METADATA_NAME
                ).exists()
            )

    def test_windows_reuses_valid_proxy_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            plugin_root = temp_path / "plugins"
            source = plugin_root / "device-common" / "CricutDevice.exe"
            write_file(source, b"helper")
            stub = make_stub(temp_path)

            with patch(
                "slicebug.cricut.windows_helper_proxy.platform.system",
                return_value="Windows",
            ), patch.dict(
                os.environ,
                {windows_helper_proxy._STUB_ENV_OVERRIDE: str(stub)},
            ):
                first = prepare_windows_device_plugin_proxy(
                    str(source),
                    str(plugin_root),
                )
                with patch(
                    "slicebug.cricut.windows_helper_proxy._rebuild_proxy_cache"
                ) as rebuild:
                    second = prepare_windows_device_plugin_proxy(
                        str(source),
                        str(plugin_root),
                    )

            self.assertEqual(first, second)
            rebuild.assert_not_called()


if __name__ == "__main__":
    unittest.main()
