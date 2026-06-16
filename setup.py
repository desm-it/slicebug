from cx_Freeze import Executable, setup
from setuptools import find_namespace_packages

from slicebug.version import VERSION

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
            "include_files": ["README.md", "docs", "examples"],
        }
    },
)
