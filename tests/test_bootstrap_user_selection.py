import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from slicebug.cli.bootstrap import choose_keys_user, import_plugins, inspect_cds_user


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


if __name__ == "__main__":
    unittest.main()
