import os
import platform

from cx_Freeze import Executable, setup
from setuptools import find_namespace_packages

from slicebug.version import VERSION

include_files = ["README.md", "docs", "examples"]

# Windows bundles a tiny native electron.exe proxy stub next to the frozen helper
# so the device helper's parent-trust gate is satisfied at runtime. CI compiles it
# to windows-helper-proxy/electron.exe before build_exe; it is only bundled on
# Windows builds where the compiled stub is present (never on macOS).
_helper_proxy_stub = os.path.join("windows-helper-proxy", "electron.exe")
if platform.system() == "Windows" and os.path.exists(_helper_proxy_stub):
    include_files.append(
        (_helper_proxy_stub, os.path.join("helper-proxy", "electron.exe"))
    )

setup(
    name="slicebug",
    packages=find_namespace_packages(include=["slicebug*"]),
    version=VERSION,
    description="A CLI for controlling Cricut cutters.",
    executables=[Executable("slicebug/__main__.py", target_name="slicebug")],
    entry_points={
        "console_scripts": [
            "slicebug = slicebug.cli.main:main",
        ]
    },
    options={
        "build_exe": {
            "excludes": ["tkinter"],
            "zip_include_packages": ["*"],
            "zip_exclude_packages": [],
            "include_files": include_files,
        }
    },
)
