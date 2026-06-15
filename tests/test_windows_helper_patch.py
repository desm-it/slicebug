import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from slicebug.cricut import windows_helper_patch
from slicebug.cricut.windows_helper_patch import (
    PATCH_OFFSET,
    PATCH_ORIGINAL_BYTES,
    PATCHED_BYTES,
    prepare_windows_device_plugin,
)
from slicebug.exceptions import UserError


def write_helper(path, gate_bytes=PATCH_ORIGINAL_BYTES):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as helper:
        helper.write(b"\x00" * PATCH_OFFSET)
        helper.write(gate_bytes)
        helper.write(b"helper tail")


def read_gate_bytes(path):
    with path.open("rb") as helper:
        helper.seek(PATCH_OFFSET)
        return helper.read(len(PATCHED_BYTES))


class WindowsHelperPatchTest(unittest.TestCase):
    def test_non_windows_returns_original_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "plugins" / "device-common" / "CricutDevice.exe"
            write_helper(source)

            with patch(
                "slicebug.cricut.windows_helper_patch.platform.system",
                return_value="Darwin",
            ):
                prepared = prepare_windows_device_plugin(
                    str(source),
                    str(Path(temp_dir) / "plugins"),
                )

            self.assertEqual(prepared, str(source))

    def test_windows_copies_and_patches_helper_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir) / "plugins"
            source_dir = plugin_root / "device-common"
            source = source_dir / "CricutDevice.exe"
            write_helper(source)
            (source_dir / "crashpad_handler.exe").write_text(
                "crashpad",
                encoding="utf-8",
            )
            (source_dir / "logs").mkdir()
            (source_dir / "logs" / "bridge.log").write_text(
                "old log",
                encoding="utf-8",
            )
            source_md5 = windows_helper_patch._file_md5(source)

            with patch(
                "slicebug.cricut.windows_helper_patch.platform.system",
                return_value="Windows",
            ):
                prepared = prepare_windows_device_plugin(
                    str(source),
                    str(plugin_root),
                    known_hashes={source_md5},
                )

            prepared_path = Path(prepared)
            self.assertEqual(
                prepared_path.parent.name,
                windows_helper_patch.PATCHED_PLUGIN_NAME,
            )
            self.assertEqual(read_gate_bytes(prepared_path), PATCHED_BYTES)
            self.assertEqual(read_gate_bytes(source), PATCH_ORIGINAL_BYTES)
            self.assertTrue((prepared_path.parent / "crashpad_handler.exe").exists())
            self.assertTrue((prepared_path.parent / "logs").exists())
            self.assertFalse((prepared_path.parent / "logs" / "bridge.log").exists())

    def test_windows_rejects_unknown_helper_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir) / "plugins"
            source = plugin_root / "device-common" / "CricutDevice.exe"
            write_helper(source)

            with patch(
                "slicebug.cricut.windows_helper_patch.platform.system",
                return_value="Windows",
            ):
                with self.assertRaises(UserError):
                    prepare_windows_device_plugin(
                        str(source),
                        str(plugin_root),
                        known_hashes={"00000000000000000000000000000000"},
                    )

    def test_windows_patch_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir) / "plugins"
            source = plugin_root / "device-common" / "CricutDevice.exe"
            write_helper(source)
            old_value = os.environ.get("SLICEBUG_DISABLE_WINDOWS_HELPER_PATCH")
            os.environ["SLICEBUG_DISABLE_WINDOWS_HELPER_PATCH"] = "1"
            try:
                with patch(
                    "slicebug.cricut.windows_helper_patch.platform.system",
                    return_value="Windows",
                ):
                    prepared = prepare_windows_device_plugin(
                        str(source),
                        str(plugin_root),
                        known_hashes={windows_helper_patch._file_md5(source)},
                    )
            finally:
                if old_value is None:
                    os.environ.pop("SLICEBUG_DISABLE_WINDOWS_HELPER_PATCH", None)
                else:
                    os.environ["SLICEBUG_DISABLE_WINDOWS_HELPER_PATCH"] = old_value

            self.assertEqual(prepared, str(source))
            self.assertEqual(read_gate_bytes(source), PATCH_ORIGINAL_BYTES)

    def test_windows_second_call_reuses_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir) / "plugins"
            source = plugin_root / "device-common" / "CricutDevice.exe"
            write_helper(source)
            known = {windows_helper_patch._file_md5(source)}

            with patch(
                "slicebug.cricut.windows_helper_patch.platform.system",
                return_value="Windows",
            ):
                first = prepare_windows_device_plugin(
                    str(source), str(plugin_root), known_hashes=known
                )
                with patch(
                    "slicebug.cricut.windows_helper_patch._rebuild_cache"
                ) as rebuild:
                    second = prepare_windows_device_plugin(
                        str(source), str(plugin_root), known_hashes=known
                    )

            self.assertEqual(first, second)
            rebuild.assert_not_called()
            self.assertEqual(read_gate_bytes(Path(second)), PATCHED_BYTES)

    def test_windows_stale_cache_metadata_triggers_rebuild(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir) / "plugins"
            source = plugin_root / "device-common" / "CricutDevice.exe"
            write_helper(source)
            known = {windows_helper_patch._file_md5(source)}

            with patch(
                "slicebug.cricut.windows_helper_patch.platform.system",
                return_value="Windows",
            ):
                prepared = prepare_windows_device_plugin(
                    str(source), str(plugin_root), known_hashes=known
                )
                metadata_path = (
                    Path(prepared).parent / windows_helper_patch.PATCH_METADATA_NAME
                )
                # Corrupt the cache metadata so the next call must rebuild.
                metadata_path.write_text('{"patchVersion": "stale"}', encoding="utf-8")

                with patch(
                    "slicebug.cricut.windows_helper_patch._rebuild_cache",
                    wraps=windows_helper_patch._rebuild_cache,
                ) as rebuild:
                    prepared_again = prepare_windows_device_plugin(
                        str(source), str(plugin_root), known_hashes=known
                    )

            rebuild.assert_called_once()
            self.assertEqual(prepared_again, prepared)
            self.assertEqual(read_gate_bytes(Path(prepared_again)), PATCHED_BYTES)

    def test_windows_already_patched_source_is_used_directly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir) / "plugins"
            source = plugin_root / "device-common" / "CricutDevice.exe"
            write_helper(source, gate_bytes=PATCHED_BYTES)

            with patch(
                "slicebug.cricut.windows_helper_patch.platform.system",
                return_value="Windows",
            ):
                # An already-patched source short-circuits before the hash check,
                # so even a bogus allow-list is never consulted.
                with patch(
                    "slicebug.cricut.windows_helper_patch._rebuild_cache"
                ) as rebuild:
                    prepared = prepare_windows_device_plugin(
                        str(source), str(plugin_root), known_hashes={"unused"}
                    )

            self.assertEqual(prepared, str(source))
            rebuild.assert_not_called()

    def test_patch_helper_rejects_unexpected_gate_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            helper = Path(temp_dir) / "CricutDevice.exe"
            write_helper(helper, gate_bytes=b"\xde\xad\xbe\xef\x90")

            with self.assertRaises(UserError):
                windows_helper_patch._patch_helper(helper)

            # The helper must be left untouched when the bytes are unexpected.
            self.assertEqual(read_gate_bytes(helper), b"\xde\xad\xbe\xef\x90")


if __name__ == "__main__":
    unittest.main()
