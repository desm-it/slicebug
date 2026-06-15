import hashlib
import json
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from slicebug.debug import log_debug
from slicebug.exceptions import UserError


PROXY_VERSION = "v0.1"
PROXY_PLUGIN_NAME = f"device-common-proxy-{PROXY_VERSION}"
PROXY_METADATA_NAME = "slicebug-helper-proxy.json"

_APP_ROOT_NAME = "Cricut" + "DesignSpace"
_SCOPE_NAME = "@" + "cricut"
_BRIDGE_SCRIPT_NAME = "bridge"
_DISABLE_VALUES = {"1", "true", "TRUE", "yes", "YES", "on", "ON"}

_BRIDGE_SCRIPT = """\
import subprocess
import sys
import threading
from pathlib import Path


BUFFER_SIZE = 65536
SCOPE_NAME = "@" + "cricut"


def main():
    app_root = Path(sys.executable).resolve().parent
    helper = app_root / "node_modules" / SCOPE_NAME / "device-common" / "CricutDevice.exe"
    process = subprocess.Popen(
        [str(helper), "bridge"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        cwd=str(helper.parent),
    )

    stdin_thread = threading.Thread(
        target=pump,
        args=(sys.stdin.buffer, process.stdin, True),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=pump,
        args=(process.stderr, sys.stderr.buffer, False),
        daemon=True,
    )
    stdin_thread.start()
    stderr_thread.start()

    pump(process.stdout, sys.stdout.buffer, False)
    return process.wait()


def pump(source, target, close_target):
    try:
        while True:
            chunk = read_chunk(source)
            if not chunk:
                break
            target.write(chunk)
            target.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        if close_target:
            try:
                target.close()
            except OSError:
                pass


def read_chunk(source):
    read1 = getattr(source, "read1", None)
    if read1 is not None:
        return read1(BUFFER_SIZE)
    return source.read(BUFFER_SIZE)


raise SystemExit(main())
"""


@dataclass(frozen=True)
class _PythonRuntime:
    executable: Path
    support_files: tuple[Path, ...]
    path_entries: tuple[Path, ...]


def prepare_windows_device_plugin_proxy(source_path, plugin_root):
    """Return an electron.exe proxy path for the Windows helper gate, if possible.

    Current Windows helpers trust a development-style parent layout where the
    helper runs from node_modules and its real parent is named electron.exe. This
    function builds that layout in SliceBug's user cache. In a normal Python
    install it copies the local Python interpreter to the required parent name
    and runs the tiny proxy script through it.
    """
    if platform.system() != "Windows":
        return None
    if os.environ.get("SLICEBUG_DISABLE_WINDOWS_HELPER_PROXY") in _DISABLE_VALUES:
        log_debug("device_plugin.proxy.disabled", source_path=source_path)
        return None

    python_runtime = _python_runtime()
    if python_runtime is None:
        log_debug(
            "device_plugin.proxy.unavailable",
            source_path=source_path,
            executable=sys.executable,
            base_executable=getattr(sys, "_base_executable", None),
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
    bridge_script = app_root / _BRIDGE_SCRIPT_NAME

    source_md5 = _file_md5(source)
    metadata = _metadata(source, source_md5, python_runtime)

    if _cached_proxy_is_valid(proxy_exe, helper_exe, bridge_script, cache_dir, metadata):
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
        proxy_source=str(python_runtime.executable),
        source_md5=source_md5,
    )
    _rebuild_proxy_cache(source, python_runtime, cache_dir, metadata)
    return str(proxy_exe)


def _python_runtime():
    for value in (getattr(sys, "_base_executable", None), sys.executable):
        if not value:
            continue
        candidate = Path(value)
        if candidate.exists() and _is_console_python_executable(candidate):
            runtime = _describe_python_runtime(candidate.resolve())
            if runtime is not None:
                return runtime
    return None


def _is_console_python_executable(path):
    name = path.name.lower()
    return re.fullmatch(r"python(?:\d+(?:\.\d+)?)?\.exe", name) is not None


def _describe_python_runtime(executable):
    install_root = executable.parent
    lib_dir = install_root / "Lib"
    if not (lib_dir / "encodings").is_dir():
        return None

    support_files = [executable]
    for pattern in ("python*.dll", "vcruntime*.dll", "ucrtbase.dll"):
        support_files.extend(sorted(install_root.glob(pattern)))

    path_entries = [
        path
        for path in (
            install_root,
            install_root / "DLLs",
            *_python_zip_entries(install_root),
            lib_dir,
        )
        if path.exists()
    ]

    return _PythonRuntime(
        executable=executable,
        support_files=tuple(dict.fromkeys(path.resolve() for path in support_files)),
        path_entries=tuple(dict.fromkeys(path.resolve() for path in path_entries)),
    )


def _python_zip_entries(install_root):
    return sorted(install_root.glob("python*.zip"))


def _metadata(source, source_md5, python_runtime):
    return {
        "proxyVersion": PROXY_VERSION,
        "sourceName": source.name,
        "sourceMd5": source_md5,
        "proxySourceName": python_runtime.executable.name,
        "proxySourceMd5": _file_md5(python_runtime.executable),
        "proxySupportFiles": [
            {
                "name": file.name,
                "md5": _file_md5(file),
            }
            for file in python_runtime.support_files
        ],
        "proxyPathEntries": [str(path) for path in python_runtime.path_entries],
        "runtimeAppRoot": _APP_ROOT_NAME,
        "helperRelativePath": str(
            Path("node_modules") / _SCOPE_NAME / "device-common" / source.name
        ),
    }


def _cached_proxy_is_valid(proxy_exe, helper_exe, bridge_script, cache_dir, expected):
    metadata_path = cache_dir / PROXY_METADATA_NAME
    pth_path = proxy_exe.parent / "electron._pth"
    if (
        not proxy_exe.exists()
        or not helper_exe.exists()
        or not bridge_script.exists()
        or not pth_path.exists()
        or not metadata_path.exists()
    ):
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (
        metadata == expected
        and _file_md5(proxy_exe) == expected["proxySourceMd5"]
        and _file_md5(helper_exe) == expected["sourceMd5"]
        and bridge_script.read_text(encoding="utf-8") == _BRIDGE_SCRIPT
        and _proxy_runtime_files_are_valid(proxy_exe.parent, expected)
        and pth_path.read_text(encoding="utf-8")
        == _pth_contents(expected["proxyPathEntries"])
    )


def _proxy_runtime_files_are_valid(app_root, expected):
    for file in expected["proxySupportFiles"]:
        path = app_root / _runtime_file_name(file["name"])
        if not path.exists() or _file_md5(path) != file["md5"]:
            return False
    return True


def _rebuild_proxy_cache(source, python_runtime, cache_dir, metadata):
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
        for support_file in python_runtime.support_files:
            shutil.copy2(support_file, app_root / _runtime_file_name(support_file.name))
        (app_root / "electron._pth").write_text(
            _pth_contents(str(path) for path in python_runtime.path_entries),
            encoding="utf-8",
        )
        (app_root / _BRIDGE_SCRIPT_NAME).write_text(
            _BRIDGE_SCRIPT,
            encoding="utf-8",
        )
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


def _runtime_file_name(name):
    if name.lower() == "python.exe":
        return "electron.exe"
    return name


def _pth_contents(path_entries):
    return "".join(f"{path}\n" for path in path_entries) + "import site\n"


def _file_md5(path):
    digest = hashlib.md5()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()
