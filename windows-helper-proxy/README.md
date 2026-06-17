# Windows helper proxy stub

`electron_stub.c` compiles to a tiny native `electron.exe` that SliceBug uses on
Windows to launch Cricut's `CricutDevice.exe`.

The current Windows helper only runs its bridge protocol when its parent process
is named `electron.exe` and it lives under a `node_modules/@cricut/device-common/`
layout. SliceBug stages that layout in its cache
(`slicebug/cricut/windows_helper_proxy.py`) and uses this stub as the parent. The
stub spawns the real helper beside it and relays stdin/stdout/stderr verbatim, so
SliceBug's bridge protocol is unchanged and no Python runtime is needed at launch.

Built in CI (`.github/workflows/build-exe.yml`, Windows job) with MSVC:

    cl /nologo /O1 /MT electron_stub.c /Feelectron.exe

then bundled into the frozen app at `helper-proxy/electron.exe` (see `setup.py`).
Windows only; it is not built or bundled on macOS. The compiled `electron.exe`
and its `.obj` are build artifacts and are git-ignored.
