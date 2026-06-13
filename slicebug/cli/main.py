import argparse
import hashlib
import os.path
import sys
import traceback

from slicebug.cli.bootstrap import bootstrap_register_args
from slicebug.cli.cut import cut_register_args
from slicebug.cli.list_materials import list_materials_register_args
from slicebug.cli.list_tools import list_tools_register_args
from slicebug.cli.plan import plan_register_args
from slicebug.config.config import Config
from slicebug.debug import debug_log_path, log_debug, log_exception
from slicebug.exceptions import UserError
from slicebug.version import VERSION


def _sha256_prefix(data, length=16):
    if data is None:
        return None
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()[:length]


def _file_sha256_prefix(path, length=16):
    if path is None or not os.path.exists(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:length]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--profile", help="pick a machine profile to use")

    subparsers = parser.add_subparsers()

    bootstrap_register_args(subparsers)
    list_materials_register_args(subparsers)
    list_tools_register_args(subparsers)
    plan_register_args(subparsers)
    cut_register_args(subparsers)

    args = parser.parse_args()
    log_debug(
        "cli.start",
        argv=sys.argv[1:],
        version=VERSION,
        debug_log_path=debug_log_path(),
    )

    if "cmd_handler" not in args:
        parser.print_help()
        sys.exit(1)

    try:
        config_root = os.path.expanduser("~/.slicebug")
        config = Config.load(config_root, args.profile)
        log_debug(
            "cli.config_loaded",
            config_root=config_root,
            profile_name=config.profile_name,
            has_keys=config.keys is not None,
            request_key_sha256=_sha256_prefix(
                config.keys.cricutdevice_request_key if config.keys else None
            ),
            settings8_sha256=_sha256_prefix(
                config.keys.settings8_raw if config.keys else None
            ),
            has_profile=config.profile is not None,
            profile_serial=config.profile.serial if config.profile else None,
            device_plugin_path=config.device_plugin_path(),
            device_plugin_sha256=_file_sha256_prefix(config.device_plugin_path()),
            command=getattr(args.cmd_handler, "__name__", str(args.cmd_handler)),
        )

        if args.cmd_needs_profile and config.profile is None:
            raise UserError(
                "A machine profile is required to run this command, but it was not found.",
                "Try running `slicebug bootstrap`.",
            )

        if args.cmd_needs_keys and config.keys is None:
            raise UserError(
                "Keys are required to run this command, but they were not found.",
                "Try running `slicebug bootstrap`.",
            )

        args.cmd_handler(args, config)
        log_debug(
            "cli.complete",
            command=getattr(args.cmd_handler, "__name__", str(args.cmd_handler)),
        )
    except UserError as err:
        log_exception("cli.user_error", err)
        message, resolution = err.args
        print(f"Error: {message}", file=sys.stderr)
        if resolution is not None:
            print(resolution, file=sys.stderr)
        sys.exit(1)
    except Exception as err:
        log_exception("cli.unexpected_error", err)
        traceback.print_exception(err)
        print("", file=sys.stderr)
        print("An unexpected error has occurred!", file=sys.stderr)
        print(
            "This might be a bug in slicebug or unexpected Cricut behavior.",
            file=sys.stderr,
        )
        print(
            "Try again. If the error persists, send a copy or screenshot of this "
            "error message (including the details above) to slicebug developers.",
            file=sys.stderr,
        )
        sys.exit(1)
