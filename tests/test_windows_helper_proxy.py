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


def make_python_runtime(root):
    python_root = Path(root) / "python-runtime"
    python_exe = python_root / "python.exe"
    write_file(python_exe, b"python")
    write_file(python_root / "python314.dll", b"python-dll")
    write_file(python_root / "vcruntime140.dll", b"vc-runtime")
    (python_root / "Lib" / "encodings").mkdir(parents=True)
    return python_exe


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

    def test_windows_returns_none_without_console_python(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "plugins" / "device-common" / "CricutDevice.exe"
            write_file(source, b"helper")

            with patch(
                "slicebug.cricut.windows_helper_proxy.platform.system",
                return_value="Windows",
            ), patch(
                "slicebug.cricut.windows_helper_proxy.sys.executable",
                str(Path(temp_dir) / "slicebug.exe"),
            ), patch(
                "slicebug.cricut.windows_helper_proxy.sys._base_executable",
                str(Path(temp_dir) / "slicebug.exe"),
                create=True,
            ):
                prepared = prepare_windows_device_plugin_proxy(
                    str(source),
                    str(Path(temp_dir) / "plugins"),
                )

            self.assertIsNone(prepared)

    def test_windows_proxy_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "plugins" / "device-common" / "CricutDevice.exe"
            write_file(source, b"helper")
            old_value = os.environ.get("SLICEBUG_DISABLE_WINDOWS_HELPER_PROXY")
            os.environ["SLICEBUG_DISABLE_WINDOWS_HELPER_PROXY"] = "1"
            try:
                with patch(
                    "slicebug.cricut.windows_helper_proxy.platform.system",
                    return_value="Windows",
                ):
                    prepared = prepare_windows_device_plugin_proxy(
                        str(source),
                        str(Path(temp_dir) / "plugins"),
                    )
            finally:
                if old_value is None:
                    os.environ.pop("SLICEBUG_DISABLE_WINDOWS_HELPER_PROXY", None)
                else:
                    os.environ["SLICEBUG_DISABLE_WINDOWS_HELPER_PROXY"] = old_value

            self.assertIsNone(prepared)

    def test_windows_builds_proxy_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            plugin_root = temp_path / "plugins"
            source_dir = plugin_root / "device-common"
            source = source_dir / "CricutDevice.exe"
            python_exe = make_python_runtime(temp_path)
            write_file(source, b"helper")
            (source_dir / "support.dll").write_text("dll", encoding="utf-8")
            (source_dir / "logs").mkdir()
            (source_dir / "logs" / "bridge.log").write_text(
                "old log",
                encoding="utf-8",
            )

            with patch(
                "slicebug.cricut.windows_helper_proxy.platform.system",
                return_value="Windows",
            ), patch(
                "slicebug.cricut.windows_helper_proxy.sys.executable",
                str(python_exe),
            ), patch(
                "slicebug.cricut.windows_helper_proxy.sys._base_executable",
                str(python_exe),
                create=True,
            ):
                prepared = prepare_windows_device_plugin_proxy(
                    str(source),
                    str(plugin_root),
                )

            prepared_path = Path(prepared)
            self.assertEqual(prepared_path.name, "electron.exe")
            self.assertEqual(prepared_path.read_bytes(), b"python")
            self.assertEqual(
                (prepared_path.parent / "python314.dll").read_bytes(),
                b"python-dll",
            )
            self.assertEqual(
                (prepared_path.parent / "vcruntime140.dll").read_bytes(),
                b"vc-runtime",
            )
            pth = (prepared_path.parent / "electron._pth").read_text(encoding="utf-8")
            self.assertIn(str(python_exe.parent), pth)
            self.assertIn(str(python_exe.parent / "Lib"), pth)
            app_root = prepared_path.parent
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
            self.assertEqual(
                (app_root / "bridge").read_text(encoding="utf-8"),
                windows_helper_proxy._BRIDGE_SCRIPT,
            )
            self.assertTrue(
                (prepared_path.parents[1] / windows_helper_proxy.PROXY_METADATA_NAME).exists()
            )

    def test_windows_reuses_valid_proxy_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            plugin_root = temp_path / "plugins"
            source = plugin_root / "device-common" / "CricutDevice.exe"
            python_exe = make_python_runtime(temp_path)
            write_file(source, b"helper")

            with patch(
                "slicebug.cricut.windows_helper_proxy.platform.system",
                return_value="Windows",
            ), patch(
                "slicebug.cricut.windows_helper_proxy.sys.executable",
                str(python_exe),
            ), patch(
                "slicebug.cricut.windows_helper_proxy.sys._base_executable",
                str(python_exe),
                create=True,
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
