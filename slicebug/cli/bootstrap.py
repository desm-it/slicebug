import base64
import hashlib
import io
import json
import os.path
import platform
import re
import shutil
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass

from slicebug.config.keys import Keys
from slicebug.config.machine_profile import MachineProfile, MachineProfiles
from slicebug.debug import log_debug
from slicebug.exceptions import UserError


def _get_usvg_download_info():
    """Return (url, sha256, archive_member) for the current platform."""
    system = platform.system()
    if system == "Darwin":
        return (
            "https://github.com/linebender/resvg/releases/download/v0.27.0/usvg-macos-x86_64.zip",
            "48c0ca0fbe0a7e195c84545a6924a7aec526070a98facc5c54829620d8e49887",
            "usvg",
        )
    else:  # Windows and others default to Windows
        return (
            "https://github.com/linebender/resvg/releases/download/v0.27.0/usvg-win64.zip",
            "fc30023106bc846ba43713a620b638a04cae761a9fa899b7bd31f4ef9236b96d",
            "usvg.exe",
        )


def _get_default_cds_path():
    """Return the default Cricut Design Space installation path for the current platform."""
    system = platform.system()
    if system == "Darwin":
        return "/Applications/Cricut Design Space.app/Contents/Resources"
    else:  # Windows
        return os.path.expanduser("~/AppData/Local/Programs/Cricut Design Space")


def bootstrap_register_args(subparsers):
    parser = subparsers.add_parser(
        "bootstrap",
        help="Prepare slicebug for use by copying required information from Cricut Design Space.",
    )
    parser.add_argument(
        "--design-space-path",
        help="Path to where Cricut Design Space is installed. Defaults to %(default)s, you likely don't need to change this.",
        default=_get_default_cds_path(),
    )
    parser.add_argument(
        "--design-space-profile-path",
        help="Path to where Cricut Design Space stores your user data. Defaults to %(default)s, you likely don't need to change this.",
        default=os.path.expanduser("~/.cricut-design-space"),
    )

    parser.set_defaults(cmd_handler=bootstrap)
    parser.set_defaults(cmd_needs_profile=False)
    parser.set_defaults(cmd_needs_keys=False)


@dataclass(frozen=True)
class CdsUserData:
    name: str
    has_user_settings: bool
    machine_serials: list[str]
    user_settings_mtime: float


def _user_settings_path(cds_profile_root, cds_user):
    return os.path.join(
        cds_profile_root, "LocalData", cds_user, "UserSessionData", "UserSettings"
    )


def _machine_settings_root(cds_profile_root, cds_user):
    return os.path.join(cds_profile_root, "LocalData", cds_user, "MaterialSettings")


def inspect_cds_user(cds_profile_root, cds_user):
    user_settings_path = _user_settings_path(cds_profile_root, cds_user)
    has_user_settings = os.path.isfile(user_settings_path)
    user_settings_mtime = (
        os.path.getmtime(user_settings_path) if has_user_settings else 0.0
    )
    machine_serials = []
    material_settings_root = _machine_settings_root(cds_profile_root, cds_user)
    if os.path.isdir(material_settings_root):
        machine_serials = sorted(
            subdir.name
            for subdir in os.scandir(material_settings_root)
            if subdir.is_dir()
            and os.path.isfile(os.path.join(subdir.path, "MaterialSettings"))
        )
    return CdsUserData(
        name=cds_user,
        has_user_settings=has_user_settings,
        machine_serials=machine_serials,
        user_settings_mtime=user_settings_mtime,
    )


def choose_keys_user(cds_profile_root, cds_users):
    inspected_users = [inspect_cds_user(cds_profile_root, user) for user in cds_users]
    users_with_settings = [user for user in inspected_users if user.has_user_settings]
    users_with_settings_and_profiles = [
        user for user in users_with_settings if len(user.machine_serials) > 0
    ]

    if len(users_with_settings_and_profiles) == 1:
        chosen = users_with_settings_and_profiles[0]
        reason = "only user with keys and machine profiles"
    elif len(users_with_settings_and_profiles) > 1:
        chosen = max(
            users_with_settings_and_profiles,
            key=lambda user: user.user_settings_mtime,
        )
        reason = "newest user settings among users with machine profiles"
    elif len(users_with_settings) > 0:
        chosen = max(users_with_settings, key=lambda user: user.user_settings_mtime)
        reason = "newest user settings"
    elif len(inspected_users) > 0:
        chosen = inspected_users[0]
        reason = "first local data user without user settings"
    else:
        raise UserError(
            "No user data found in the CDS profile.",
            "Ensure that CDS has been used to make at least one cut.",
        )

    log_debug(
        "bootstrap.keys_user_selected",
        chosen_user=chosen.name,
        reason=reason,
        users=[
            {
                "name": user.name,
                "has_user_settings": user.has_user_settings,
                "machine_serials": user.machine_serials,
                "user_settings_mtime": user.user_settings_mtime,
            }
            for user in inspected_users
        ],
    )
    print(f"Using Design Space user data from {chosen.name} ({reason}).")
    return chosen.name


def import_keys(cds_root, cds_profile_root, cds_user, config):
    xor = lambda data, key: bytes(v ^ key[i % len(key)] for i, v in enumerate(data))

    print("Locating obfuscation key.")
    # On macOS, cds_root is already Contents/Resources
    # On Windows, app.asar is in cds_root/resources/
    if platform.system() == "Darwin":
        asar_path = os.path.join(cds_root, "app.asar")
    else:
        asar_path = os.path.join(cds_root, "resources", "app.asar")
    with open(asar_path, "rb") as f:
        asar = f.read()
        # this matches ([0x01, 0x02, ...]) with exactly 64 elements.
        obfuscation_key_pattern = (
            rb"""\(\[((?:0x[0-9a-f]{1,2},){63}(?:0x[0-9a-f]{1,2}))\]\)"""
        )
        matches = list(re.finditer(obfuscation_key_pattern, asar))
        assert len(matches) == 1
        obfuscation_key = bytes(
            int(x, 16) for x in matches[0].group(1).decode().split(",")
        )

    user_settings_path = _user_settings_path(cds_profile_root, cds_user)
    print(f"Importing keys from {user_settings_path}.")

    with open(user_settings_path) as f:
        user_settings = json.load(f)

    settings3_sha512 = hashlib.sha512(user_settings["settings3"].encode()).digest()
    settings2 = xor(
        base64.b64decode(user_settings["settings2"].encode()), settings3_sha512
    )
    cricutdevice_request_key_b64 = xor(settings2, obfuscation_key)
    cricutdevice_request_key = base64.b64decode(cricutdevice_request_key_b64)

    settings8_raw = xor(
        base64.b64decode(user_settings["settings8"].encode()), settings3_sha512
    ).decode()

    keys = Keys(
        cricutdevice_request_key=cricutdevice_request_key,
        settings8_raw=settings8_raw,
    )

    keys.save(config.config_root)
    print("Keys imported.")
    print()


def import_plugins(cds_root, config):
    # On macOS, cds_root is already Contents/Resources, so plugins is directly inside
    # On Windows, it's cds_root/resources/plugins
    if platform.system() == "Darwin":
        plugin_dir = os.path.join(cds_root, "plugins")
    else:
        plugin_dir = os.path.join(cds_root, "resources", "plugins")
    print(f"Importing plugins from {cds_root}.")

    for destination_plugin, source_plugin in _device_plugin_imports(plugin_dir):
        source = os.path.join(plugin_dir, source_plugin)
        destination = os.path.join(config.plugin_root(), destination_plugin)
        print(f"Importing plugin {destination_plugin} from {source_plugin}.")
        log_debug(
            "bootstrap.plugin_source_selected",
            destination_plugin=destination_plugin,
            source_plugin=source_plugin,
            source=source,
            destination=destination,
            platform=platform.system(),
        )
        if os.path.exists(destination):
            print(f"Removing existing plugin {destination_plugin}.")
            shutil.rmtree(destination)
        shutil.copytree(source, destination)

    print("Plugins imported.")
    print()


def _device_plugin_imports(plugin_dir):
    device_candidates = ["device-common"]

    checked = []
    for source_plugin in device_candidates:
        source = os.path.join(plugin_dir, source_plugin)
        checked.append(
            {
                "plugin": source_plugin,
                "path": source,
                "exists": os.path.isdir(source),
            }
        )
        if os.path.isdir(source):
            return [(source_plugin, source_plugin)]

    log_debug(
        "bootstrap.plugin_source_missing",
        destination_plugin="device-common",
        candidates=checked,
        platform=platform.system(),
    )
    raise UserError(
        "Design Space device helper plugin was not found.",
        "Reinstall or update Design Space, then run `slicebug bootstrap` again.",
    )


def import_machine_profiles(cds_profile_root, cds_users, config):
    # TODO: make this more user-friendly for the case where the same serial appears for multiple users?
    machines_found = []
    for user in cds_users:
        material_settings_root = _machine_settings_root(cds_profile_root, user)
        if not os.path.isdir(material_settings_root):
            continue
        machines_found.extend(
            (subdir.name, subdir.path)
            for subdir in os.scandir(material_settings_root)
            if subdir.is_dir()
        )

    if len(machines_found) == 0:
        print("No machine profiles found.")
        print(
            "You will not be able to execute cuts with slicebug without a machine profile."
        )
        print(
            "Make sure that you have logged into Cricut Design Space and made at least one cut there, then run this again."
        )
        profiles_to_import = {}
    elif len(machines_found) == 1:
        print(
            f"Found one machine {machines_found[0][0]}. Importing and setting it as default."
        )
        profiles_to_import = {"default": machines_found[0]}
    else:
        found_serials = [serial for serial, _ in machines_found]
        print(f"Found multiple machines: {found_serials}.")
        print("We will now ask you to set a name for each one.")
        print(
            "When cutting, you will need to provide the name of the profile for the machine you want to use."
        )
        print(
            "For example, if you name your machine 'maker', you will need to supply `--profile maker` on the command line."
        )
        print(
            "If you name a machine 'default', it will be used by default when multiple machines are found."
        )
        print("If you name a machine '-', it will not be imported.")
        print()

        profiles_to_import = {}
        for serial, path in machines_found:
            name = input(f"Name for {serial}: ")
            if (name == "-") or (name == ""):
                continue
            profiles_to_import[name] = (serial, path)

    profiles_root = MachineProfiles.profiles_root(config.config_root)
    machine_profiles = MachineProfiles(profiles={})

    for name, (serial, path) in profiles_to_import.items():
        print(f"Importing machine {serial}.")
        profile_root = os.path.join(profiles_root, serial)
        os.makedirs(profile_root, exist_ok=True)

        profile = MachineProfile(
            serial=serial, profile_root=profile_root, calibration_records=[]
        )

        shutil.copyfile(
            os.path.join(path, "MaterialSettings"),
            profile.material_settings_path(),
        )

        machine_profiles.profiles[name] = profile

    machine_profiles.save(config.config_root)
    print("Machines imported.")
    print()


def download_usvg(config):
    usvg_url, usvg_sha256, usvg_member = _get_usvg_download_info()

    print("Downloading usvg from Github.")
    response = urllib.request.urlopen(usvg_url)
    zip_bytes = response.read()
    zip_sha256_actual = hashlib.sha256(zip_bytes).hexdigest()

    if zip_sha256_actual != usvg_sha256:
        raise UserError(
            f"Could not download usvg. Expected to see a file with hash {usvg_sha256}, saw {zip_sha256_actual}.",
            "Check your network connection.",
        )

    print("Extracting usvg.")
    usvg_dir = os.path.join(config.plugin_root(), "usvg")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_struct:
        zip_struct.extract(usvg_member, usvg_dir)

    # Make executable on Unix systems
    if platform.system() != "Windows":
        usvg_path = os.path.join(usvg_dir, usvg_member)
        os.chmod(usvg_path, 0o755)

    print("usvg extracted.")


def bootstrap(args, config):
    config.create_dirs()

    if not os.path.isdir(args.design_space_path):
        raise UserError(
            f"Cricut Design Space not found at {args.design_space_path}.",
            "Ensure that CDS is installed. If needed, specify a different path using --design-space-path.",
        )

    if not os.path.isdir(args.design_space_profile_path):
        raise UserError(
            f"Cricut Design Space profile not found at {args.design_space_profile_path}.",
            "Ensure that CDS is installed and has been used to make at least one cut. "
            "If needed, specify a different path using --design-space-profile-path.",
        )

    cds_users = [
        subdir.name
        for subdir in os.scandir(
            os.path.join(args.design_space_profile_path, "LocalData")
        )
        if subdir.is_dir()
    ]

    if len(cds_users) == 0:
        raise UserError(
            f"No user data found in the CDS profile.",
            "Ensure that CDS has been used to make at least one cut.",
        )

    keys_user = choose_keys_user(args.design_space_profile_path, cds_users)

    import_plugins(args.design_space_path, config)
    import_keys(
        args.design_space_path, args.design_space_profile_path, keys_user, config
    )
    import_machine_profiles(args.design_space_profile_path, cds_users, config)
    download_usvg(config)
