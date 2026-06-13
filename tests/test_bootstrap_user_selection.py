import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from slicebug.cli.bootstrap import choose_keys_user, import_plugins, inspect_cds_user
from slicebug.exceptions import UserError


def make_user(root, name, *, has_settings=True, serials=(), mtime=1):
    user_root = Path(root) / "LocalData" / name
    if has_settings:
        settings_path = user_root / "UserSessionData" / "UserSettings"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{}", encoding="utf-8")
        os.utime(settings_path, (mtime, mtime))

    for serial in serials:
        material_settings = user_root / "MaterialSettings" / serial / "MaterialSettings"
        material_settings.parent.mkdir(parents=True, exist_ok=True)
        material_settings.write_text("{}", encoding="utf-8")


class BootstrapUserSelectionTest(unittest.TestCase):
    def test_inspect_cds_user_reports_settings_and_machine_profiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            make_user(temp_dir, "user-a", serials=["JOY123"], mtime=10)

            inspected = inspect_cds_user(temp_dir, "user-a")

            self.assertEqual(inspected.name, "user-a")
            self.assertTrue(inspected.has_user_settings)
            self.assertEqual(inspected.machine_serials, ["JOY123"])
            self.assertEqual(inspected.user_settings_mtime, 10)

    def test_choose_keys_user_prefers_only_user_with_settings_and_profiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            make_user(temp_dir, "stale-login", has_settings=True, serials=(), mtime=50)
            make_user(
                temp_dir,
                "cutter-login",
                has_settings=True,
                serials=["JOY123"],
                mtime=10,
            )

            self.assertEqual(
                choose_keys_user(temp_dir, ["stale-login", "cutter-login"]),
                "cutter-login",
            )

    def test_choose_keys_user_uses_newest_profile_user_when_multiple_have_profiles(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            make_user(temp_dir, "old-cutter-login", serials=["JOY123"], mtime=10)
            make_user(temp_dir, "new-cutter-login", serials=["JOY456"], mtime=70)

            self.assertEqual(
                choose_keys_user(temp_dir, ["old-cutter-login", "new-cutter-login"]),
                "new-cutter-login",
            )

    def test_choose_keys_user_falls_back_to_newest_settings_without_profiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            make_user(temp_dir, "old-login", serials=(), mtime=10)
            make_user(temp_dir, "new-login", serials=(), mtime=70)

            self.assertEqual(
                choose_keys_user(temp_dir, ["old-login", "new-login"]),
                "new-login",
            )

    def test_import_plugins_replaces_stale_device_common_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cds_root = temp_path / "Design Space"
            source = cds_root / "resources" / "plugins" / "device-common"
            source.mkdir(parents=True)
            (source / "CricutDevice.exe").write_text("fresh", encoding="utf-8")

            config_root = temp_path / ".slicebug"
            destination = config_root / "plugins" / "device-common"
            destination.mkdir(parents=True)
            (destination / "stale.dll").write_text("stale", encoding="utf-8")

            config = type(
                "Config",
                (),
                {"plugin_root": lambda _self: str(config_root / "plugins")},
            )()

            with patch(
                "slicebug.cli.bootstrap.platform.system", return_value="Windows"
            ):
                import_plugins(str(cds_root), config)

            self.assertTrue((destination / "CricutDevice.exe").exists())
            self.assertFalse((destination / "stale.dll").exists())

    def test_import_plugins_prefers_windows_device_common(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cds_root = temp_path / "Design Space"
            plugin_root = cds_root / "resources" / "plugins"
            old_source = plugin_root / "device-common"
            next_source = plugin_root / "device-common-next"
            io_source = plugin_root / "cricut-device-io"
            old_source.mkdir(parents=True)
            next_source.mkdir(parents=True)
            io_source.mkdir(parents=True)
            (old_source / "CricutDevice.exe").write_text("old", encoding="utf-8")
            (next_source / "CricutDevice.exe").write_text("next", encoding="utf-8")
            (io_source / "CricutDeviceIO.exe").write_text("io", encoding="utf-8")

            config_root = temp_path / ".slicebug"
            config = type(
                "Config",
                (),
                {"plugin_root": lambda _self: str(config_root / "plugins")},
            )()

            with patch(
                "slicebug.cli.bootstrap.platform.system", return_value="Windows"
            ):
                import_plugins(str(cds_root), config)

            old_destination = config_root / "plugins" / "device-common"
            next_destination = config_root / "plugins" / "device-common-next"
            io_destination = config_root / "plugins" / "cricut-device-io"
            # Windows now mirrors macOS / Design Space default: use device-common,
            # and do not copy the unused cricut-device-io sibling.
            self.assertEqual(
                (old_destination / "CricutDevice.exe").read_text(encoding="utf-8"),
                "old",
            )
            self.assertFalse(next_destination.exists())
            self.assertFalse(io_destination.exists())

    def test_import_plugins_ignores_windows_device_common_next_without_device_common(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cds_root = temp_path / "Design Space"
            plugin_root = cds_root / "resources" / "plugins"
            next_source = plugin_root / "device-common-next"
            next_source.mkdir(parents=True)
            (next_source / "CricutDevice.exe").write_text("next", encoding="utf-8")

            config_root = temp_path / ".slicebug"
            config = type(
                "Config",
                (),
                {"plugin_root": lambda _self: str(config_root / "plugins")},
            )()

            with patch(
                "slicebug.cli.bootstrap.platform.system", return_value="Windows"
            ):
                with self.assertRaises(UserError):
                    import_plugins(str(cds_root), config)

            self.assertFalse((config_root / "plugins" / "device-common-next").exists())

    def test_import_plugins_keeps_macos_on_device_common(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cds_root = temp_path / "Design Space"
            plugin_root = cds_root / "plugins"
            old_source = plugin_root / "device-common"
            next_source = plugin_root / "device-common-next"
            old_source.mkdir(parents=True)
            next_source.mkdir(parents=True)
            (old_source / "CricutDevice").write_text("old", encoding="utf-8")
            (next_source / "CricutDevice").write_text("next", encoding="utf-8")

            config_root = temp_path / ".slicebug"
            config = type(
                "Config",
                (),
                {"plugin_root": lambda _self: str(config_root / "plugins")},
            )()

            with patch(
                "slicebug.cli.bootstrap.platform.system", return_value="Darwin"
            ):
                import_plugins(str(cds_root), config)

            destination = config_root / "plugins" / "device-common"
            self.assertEqual(
                (destination / "CricutDevice").read_text(encoding="utf-8"),
                "old",
            )


if __name__ == "__main__":
    unittest.main()
