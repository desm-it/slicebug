import hashlib
import json
import os
import platform
import shutil
from pathlib import Path

from slicebug.debug import log_debug
from slicebug.exceptions import UserError


PATCH_VERSION = "v0.4"
PATCHED_PLUGIN_NAME = f"device-common-patched-{PATCH_VERSION}"
PATCH_METADATA_NAME = "slicebug-helper-patch.json"

SUPPORTED_HELPER_MD5 = {
    "EDEF022BE4E8B0B246A46B130A8F72CF",
}

PATCH_OFFSET = 0x28483
PATCH_ORIGINAL_BYTES = bytes.fromhex("E8 B8 F7 00 00")
PATCHED_BYTES = bytes.fromhex("B0 01 90 90 90")

_DISABLE_VALUES = {"1", "true", "TRUE", "yes", "YES", "on", "ON"}


def prepare_windows_device_plugin(source_path, plugin_root, known_hashes=None):
    """Return a CricutDevice path suitable for the legacy bridge protocol.

    CricutDevice.exe on current Windows Design Space builds drops standalone
    bridge/method frames unless a startup parent-trust flag is true. We never
    patch Cricut's installed or bootstrapped helper in place; instead, when
    running on Windows, we copy the helper into a SliceBug-managed cache and
    patch that copy after verifying the source hash and bytes.
    """
    if platform.system() != "Windows":
        return source_path
    if os.environ.get("SLICEBUG_DISABLE_WINDOWS_HELPER_PATCH") in _DISABLE_VALUES:
        log_debug("device_plugin.patch.disabled", source_path=source_path)
        return source_path

    source = Path(source_path).resolve()
    if _has_bytes(source, PATCHED_BYTES):
        log_debug("device_plugin.patch.source_already_patched", path=str(source))
        return str(source)

    supported_hashes = known_hashes or SUPPORTED_HELPER_MD5
    source_md5 = _file_md5(source)
    if source_md5 not in supported_hashes:
        raise UserError(
            f"Unsupported CricutDevice.exe version: md5 {source_md5}.",
            "SliceBug can only apply the Windows helper gate patch to known "
            "helper builds. Run `slicebug bootstrap` after updating Design "
            "Space, or set SLICEBUG_DISABLE_WINDOWS_HELPER_PATCH=1 to try the "
            "unpatched helper.",
        )

    cache_dir = Path(plugin_root).resolve() / PATCHED_PLUGIN_NAME
    patched_exe = cache_dir / source.name
    metadata = _metadata(source, source_md5)

    if _cached_copy_is_valid(patched_exe, cache_dir / PATCH_METADATA_NAME, metadata):
        log_debug(
            "device_plugin.patch.cache_hit",
            source_path=str(source),
            patched_path=str(patched_exe),
            source_md5=source_md5,
        )
        return str(patched_exe)

    log_debug(
        "device_plugin.patch.cache_rebuild",
        source_path=str(source),
        cache_dir=str(cache_dir),
        source_md5=source_md5,
    )
    _rebuild_cache(source, cache_dir, metadata)
    _patch_helper(cache_dir / source.name)
    return str(patched_exe)


def _metadata(source, source_md5):
    return {
        "patchVersion": PATCH_VERSION,
        "sourceName": source.name,
        "sourceMd5": source_md5,
        "patchOffset": PATCH_OFFSET,
        "originalBytes": PATCH_ORIGINAL_BYTES.hex(),
        "patchedBytes": PATCHED_BYTES.hex(),
    }


def _cached_copy_is_valid(patched_exe, metadata_path, expected_metadata):
    if not patched_exe.exists() or not metadata_path.exists():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return metadata == expected_metadata and _has_bytes(patched_exe, PATCHED_BYTES)


def _rebuild_cache(source, cache_dir, metadata):
    tmp_dir = cache_dir.with_name(cache_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    source_dir = source.parent
    for item in source_dir.iterdir():
        if item.name.lower() == "logs":
            continue
        target = tmp_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("*.log"))
        elif item.is_file():
            shutil.copy2(item, target)
    (tmp_dir / "logs").mkdir(exist_ok=True)
    (tmp_dir / PATCH_METADATA_NAME).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    tmp_dir.rename(cache_dir)


def _patch_helper(path):
    with path.open("r+b") as helper:
        helper.seek(PATCH_OFFSET)
        current = helper.read(len(PATCH_ORIGINAL_BYTES))
        if current == PATCHED_BYTES:
            return
        if current != PATCH_ORIGINAL_BYTES:
            raise UserError(
                "CricutDevice.exe did not contain the expected gate bytes.",
                "The helper build may have changed. Re-run bootstrap after "
                "updating SliceBug, or disable the Windows helper patch.",
            )
        helper.seek(PATCH_OFFSET)
        helper.write(PATCHED_BYTES)


def _has_bytes(path, expected):
    try:
        with path.open("rb") as helper:
            helper.seek(PATCH_OFFSET)
            return helper.read(len(expected)) == expected
    except OSError:
        return False


def _file_md5(path):
    digest = hashlib.md5()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()
