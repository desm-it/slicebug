import hashlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path

from slicebug.debug import log_debug
from slicebug.exceptions import UserError


PROXY_VERSION = "v0.2"
PROXY_PLUGIN_NAME = f"device-common-proxy-{PROXY_VERSION}"
PROXY_METADATA_NAME = "slicebug-helper-proxy.json"

_APP_ROOT_NAME = "Cricut" + "DesignSpace"
_SCOPE_NAME = "@" + "cricut"
_DISABLE_VALUES = {"1", "true", "TRUE", "yes", "YES", "on", "ON"}
_STUB_ENV_OVERRIDE = "SLICEBUG_WINDOWS_HELPER_PROXY_STUB"
_BUNDLED_STUB_RELATIVE = ("helper-proxy", "electron.exe")


def prepare_windows_device_plugin_proxy(source_path, plugin_root):
    """Return an electron.exe proxy path for the Windows helper gate, if possible.

    Current Windows helpers only run their bridge protocol when their parent
    process is named electron.exe and the helper sits under a
    node_modules/<scope>/device-common layout. This builds that layout in
    SliceBug's user cache using a tiny prebuilt native proxy (electron.exe, bundled
    with SliceBug on Windows) that relays stdin/stdout/stderr to the real helper.
    We never modify Cricut's installed or bootstrapped helper; we copy it into the
    cache beside the proxy. When the bundled stub is unavailable we return None and
    the caller falls back to the compatibility (patch) path.
    """
    if platform.system() != "Windows":
        return None
    if os.environ.get("SLICEBUG_DISABLE_WINDOWS_HELPER_PROXY") in _DISABLE_VALUES:
        log_debug("device_plugin.proxy.disabled", source_path=source_path)
        return None

    stub = _bundled_proxy_stub()
    if stub is None:
        log_debug(
            "device_plugin.proxy.unavailable",
            source_path=source_path,
            executable=sys.executable,
        )
        return None

    source = Path(source_path).resolve()
    if source.name.lower() != "cricutdevice.exe":
        log_debug(
            "device_plugin.proxy.unsupported_helper_name",
            source_path=str(source),
            source_name=source.name,
        )
        return None

    cache_dir = Path(plugin_root).resolve() / PROXY_PLUGIN_NAME
    app_root = cache_dir / _APP_ROOT_NAME
    proxy_exe = app_root / "electron.exe"
    helper_exe = _helper_dir(app_root) / source.name

    source_md5 = _file_md5(source)
    stub_md5 = _file_md5(stub)
    metadata = _metadata(source, source_md5, stub, stub_md5)

    if _cached_proxy_is_valid(proxy_exe, helper_exe, cache_dir, metadata):
        log_debug(
            "device_plugin.proxy.cache_hit",
            source_path=str(source),
            proxy_path=str(proxy_exe),
            source_md5=source_md5,
        )
        return str(proxy_exe)

    log_debug(
        "device_plugin.proxy.cache_rebuild",
        source_path=str(source),
        cache_dir=str(cache_dir),
        proxy_stub=str(stub),
        source_md5=source_md5,
    )
    _rebuild_proxy_cache(source, stub, cache_dir, metadata)
    return str(proxy_exe)


def _bundled_proxy_stub():
    """Locate the prebuilt electron.exe proxy stub bundled with SliceBug.

    An explicit override wins (useful for tests and manual runs); otherwise the
    stub ships next to the frozen executable at helper-proxy/electron.exe.
    """
    candidates = []
    override = os.environ.get(_STUB_ENV_OVERRIDE)
    if override:
        candidates.append(Path(override))
    if sys.executable:
        candidates.append(
            Path(sys.executable).resolve().parent.joinpath(*_BUNDLED_STUB_RELATIVE)
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _metadata(source, source_md5, stub, stub_md5):
    return {
        "proxyVersion": PROXY_VERSION,
        "sourceName": source.name,
        "sourceMd5": source_md5,
        "stubName": stub.name,
        "stubMd5": stub_md5,
        "runtimeAppRoot": _APP_ROOT_NAME,
        "helperRelativePath": str(
            Path("node_modules") / _SCOPE_NAME / "device-common" / source.name
        ),
    }


def _cached_proxy_is_valid(proxy_exe, helper_exe, cache_dir, expected):
    metadata_path = cache_dir / PROXY_METADATA_NAME
    if (
        not proxy_exe.exists()
        or not helper_exe.exists()
        or not metadata_path.exists()
    ):
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (
        metadata == expected
        and _file_md5(proxy_exe) == expected["stubMd5"]
        and _file_md5(helper_exe) == expected["sourceMd5"]
    )


def _rebuild_proxy_cache(source, stub, cache_dir, metadata):
    tmp_dir = cache_dir.with_name(cache_dir.name + ".tmp")
    try:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)

        app_root = tmp_dir / _APP_ROOT_NAME
        helper_dir = _helper_dir(app_root)
        helper_dir.mkdir(parents=True)
        app_root.mkdir(parents=True, exist_ok=True)

        source_dir = source.parent
        for item in source_dir.iterdir():
            if item.name.lower() == "logs":
                continue
            target = helper_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, ignore=shutil.ignore_patterns("*.log"))
            elif item.is_file():
                shutil.copy2(item, target)

        (helper_dir / "logs").mkdir(exist_ok=True)
        shutil.copy2(stub, app_root / "electron.exe")
        (tmp_dir / PROXY_METADATA_NAME).write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        tmp_dir.rename(cache_dir)
    except OSError as error:
        log_debug(
            "device_plugin.proxy.cache_rebuild_failed",
            cache_dir=str(cache_dir),
            error=f"{type(error).__name__}: {error}",
        )
        raise UserError(
            f"Could not prepare the Windows helper launch cache at {cache_dir}: "
            f"{error}.",
            "Close any running Cricut helper or Design Space process so the "
            "cached helper is not locked, then try again. You can also disable "
            "this launch path and use the compatibility path.",
        ) from error


def _helper_dir(app_root):
    return app_root / "node_modules" / _SCOPE_NAME / "device-common"


def _file_md5(path):
    digest = hashlib.md5()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()
