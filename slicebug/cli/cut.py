import argparse
import json
import os.path
import platform
from pathlib import Path

from slicebug.cricut.device_plugin import DevicePlugin
from slicebug.cricut.material_settings import MaterialSettings
from slicebug.cricut.windows_helper_patch import (
    prepare_windows_device_plugin as prepare_windows_device_plugin_patch,
)
from slicebug.cricut.windows_helper_proxy import prepare_windows_device_plugin_proxy
from slicebug.cricut.protobufs.NativeModel_pb2 import (
    PBAnalyticMachineSummary,
    PBSize,
    PBUserSettings,
)
from slicebug.cricut.protobufs.Bridge_pb2 import (
    PBBridgeSelectedTools,
    PBCommonBridge,
    PBInteractionHandle,
    PBInteractionStatus,
    PBLogLevel,
    PBMaterialSelected,
    PBMatPathData,
    PBToolInfo,
)
from slicebug.exceptions import ProtocolError, UserError
from slicebug.plan.plan import Plan, PlanPathStep
from slicebug.cricut.tools import HeadType, TOOLS_BY_PB_TOOL_TYPE
from slicebug.debug import log_debug
from slicebug.plan.group_paths import (
    first_pen_path_in_group,
    first_tool_in_group,
    group_and_order_paths,
)


def cut_register_args(subparsers):
    parser = subparsers.add_parser("cut", help="Execute a planned cut.")
    parser.add_argument(
        "plan", type=argparse.FileType("r"), help="Path to your plan file."
    )
    parser.add_argument(
        "--software-buttons",
        action="store_true",
        help=(
            "Use CricutDevice software button simulation messages instead of "
            "waiting for physical Load/Unload and Go buttons. This is needed "
            "for buttonless machines such as Cricut Joy."
        ),
    )
    parser.add_argument(
        "--device-plugin-path",
        help=(
            "Use a specific CricutDevice executable instead of the bootstrapped "
            "copy. Useful for testing the helper directly from the Design Space "
            "installation directory."
        ),
    )

    parser.set_defaults(cmd_handler=cut)
    parser.set_defaults(cmd_needs_profile=True)
    parser.set_defaults(cmd_needs_keys=True)


def send_software_button(dev, status):
    dev.send(
        PBCommonBridge(
            handle=PBInteractionHandle(currentInteraction=999),
            status=status,
        )
    )


MAT_LOAD_IN_PROGRESS_STATUSES = (143, 165, 166, 167)
STARTUP_PING_TIMEOUT_SECONDS = 60.0


def make_start_message(config, interaction):
    # Matches the startup envelope Design Space sends to CricutDevice.
    message = PBCommonBridge(
        interaction=interaction,
        logId="DEVICE",
        authData=PBUserSettings(settings8=config.keys.settings8_raw),
    )
    if platform.system() == "Windows":
        message.logLevel = PBLogLevel.VERBOSE_LOGLEVEL
    return message


def resolve_device_plugin_path(args, config):
    if args.device_plugin_path is not None:
        path = os.path.abspath(os.path.expanduser(args.device_plugin_path))
        if not os.path.exists(path):
            raise UserError(
                f"Device plugin override does not exist: {path}",
                "Check the CricutDevice path and try again.",
            )
        log_debug("cut.device_plugin_override", path=path)
        return path

    path = config.device_plugin_path()
    if path is None:
        raise UserError(
            "Device plugin is missing.",
            "Try running `slicebug bootstrap`.",
        )
    log_debug("cut.device_plugin_configured", path=path)
    return path


def wait_for_mat_loaded(dev):
    while True:
        resp = dev.recv()
        if resp.status == PBInteractionStatus.riMatLoaded:
            return False
        # Some Joy flows move straight from mat-motion messages to WaitClear
        # without an explicit riMatLoaded message in this old protobuf flow.
        if resp.status == PBInteractionStatus.riWaitClear:
            return True
        # Cricut Joy on macOS can emit newer mat-motion statuses that are not
        # named in this older protobuf snapshot. Known from Design Space 8.27:
        # 143=riMatInMotion; 165/166/167 are related mat-aligning/motion states.
        if resp.status in MAT_LOAD_IN_PROGRESS_STATUSES:
            continue
        if resp.status == PBInteractionStatus.riMatUnloaded:
            raise UserError(
                "The mat was unloaded while SliceBug was waiting for it to load.",
                "The Cricut Joy software Load/Unload control is a toggle. "
                "The machine probably already considered the mat loaded, so "
                "sending the virtual Load/Unload command ejected it.",
            )
        raise ProtocolError(
            f"unexpected status while waiting for mat load: {resp.status}"
        )


def handle_software_mat_load(dev):
    input("Insert mat, then press Enter to load it if needed.")

    # Cricut Joy may auto-grab/measure the mat while the CLI is blocked at the
    # prompt. Since the software Load/Unload command is a toggle, first consume
    # any queued mat-load event before sending it; otherwise we can eject a mat
    # that is already loaded.
    resp = dev.recv_if_available(timeout=2.0)
    if resp is not None:
        if resp.status == PBInteractionStatus.riMatLoaded:
            return False
        if resp.status == PBInteractionStatus.riWaitClear:
            return True
        if resp.status in MAT_LOAD_IN_PROGRESS_STATUSES:
            return wait_for_mat_loaded(dev)
        if resp.status == PBInteractionStatus.riMatUnloaded:
            raise UserError(
                "The mat was unloaded before SliceBug sent the software Load command.",
                "Reinsert the mat and retry. SliceBug did not send another "
                "Load/Unload toggle to avoid ejecting it again.",
            )
        raise ProtocolError(
            f"unexpected status before software mat load: {resp.status}"
        )

    send_software_button(dev, PBInteractionStatus.riMATCUTSimulateLoadButtonPressed)
    return wait_for_mat_loaded(dev)


def plan_tool_info(config, material, grouped_paths):
    tools = []

    for tool, _ in grouped_paths:
        tools.append(
            PBToolInfo(
                tool=tool.cricut_pb_tool_type,
                line=tool.cricut_pb_art_type,
                toolFromApi=material.tools[tool.cricut_api_name].pb_tool,
            )
        )

    selected_tools = PBBridgeSelectedTools(tools=tools)

    first_pen_path = first_pen_path_in_group(grouped_paths)
    if first_pen_path is not None:
        selected_tools.firstPen = first_pen_path.color

    return selected_tools


def step_apply_calibration(step, calibration):
    return PlanPathStep(
        step.op, [(x + calibration.x, y + calibration.y) for x, y in step.points]
    )


def plan_mat_path_data(config, plan, grouped_paths):
    paths = []

    for tool, tool_paths in grouped_paths:
        calibration = config.profile.calibration_for_tool(tool)

        for path in tool_paths:
            calibrated_steps = []
            for step in path.steps:
                calibrated_steps.append(step_apply_calibration(step, calibration))

            path_data = " ".join(step.to_svg() for step in calibrated_steps)

            path_pb = PBMatPathData(
                fiducialId=-1,
                pathData=path_data,
                actualPathType=tool.cricut_pb_art_type,
            )

            if path.color is not None:
                path_pb.pathColor = path.color

            paths.append(path_pb)

    return PBMatPathData(
        materialSize=PBSize(
            height=plan.material.height,
            width=plan.material.width,
        ),
        imageData=paths,
    )


def cut_inner(config, dev, plan, software_buttons=False):
    grouped_paths = group_and_order_paths(plan)

    material_settings = MaterialSettings.load(config.profile.material_settings_path())

    if plan.material.cricut_api_global_id not in material_settings.materials:
        raise UserError(
            f"Material with ID {plan.material.cricut_api_global_id} does not exist.",
            "Try `slicebug list-materials` to view a list of supported materials and their IDs, then modify the plan to use a supported material.",
        )

    material = material_settings.materials[plan.material.cricut_api_global_id]

    dev.send(make_start_message(config, PBInteractionStatus.riMATCUT))

    dev.recv(
        PBInteractionStatus.riStartSuccess,
        ping_timeout=STARTUP_PING_TIMEOUT_SECONDS,
    )

    device_connected_resp = dev.recv()

    # Handle macOS-specific handshake (status 1215)
    # This appears to be a newer protocol acknowledgment step
    if device_connected_resp.status == 1215:
        dev.send(
            PBCommonBridge(
                handle=PBInteractionHandle(currentInteraction=999),
                status=1215,
            )
        )
        device_connected_resp = dev.recv()

    match device_connected_resp.status:
        case PBInteractionStatus.riSingleDeviceConnected:
            # great, this is what we're looking for
            pass
        case PBInteractionStatus.riMultipleDevicesConnected:
            # On macOS, the plugin often reports multiple devices even with just USB
            # connected. We'll proceed and let the serial check catch mismatches.
            print("Note: Multiple devices detected, proceeding with default selection.")
            pass
        case PBInteractionStatus.riNoDeviceConnected:
            raise UserError(
                "No Cricut devices connected.",
                "Connect your cutter to your computer and try again.",
            )
        case _:
            raise ProtocolError(
                f"unexpected status after start success: {device_connected_resp.status}"
            )

    dev.send(
        PBCommonBridge(
            handle=PBInteractionHandle(currentInteraction=999),
            status=PBInteractionStatus.riSelectDevice,
            device=device_connected_resp.device,
        )
    )

    dev.recv(PBInteractionStatus.riOpeningDevice)
    machine_summary_resp = dev.recv(
        PBInteractionStatus.riOPENDEVICEGetAnalyticMachineSummary
    )
    serial = machine_summary_resp.device.serial

    if serial != config.profile.serial:
        raise UserError(
            f"Serial of connected device ({serial}) does not match profile ({config.profile.serial}).",
            "Connect the correct device or switch to a different profile with --profile.",
        )

    dev.send(
        PBCommonBridge(
            handle=PBInteractionHandle(currentInteraction=999),
            status=PBInteractionStatus.riOPENDEVICESetAnalyticMachineSummary,
            deviceAnalyticMachineSummary=PBAnalyticMachineSummary(
                firmwareValuesStored="valuesStored",
                primaryUserSet=True,
            ),
        )
    )

    dev.recv(PBInteractionStatus.riDeviceOpenSuccess)
    dev.recv(PBInteractionStatus.riDialChanged)
    dev.recv(PBInteractionStatus.riWaitingOnMaterialSelected)

    dev.send(
        PBCommonBridge(
            handle=PBInteractionHandle(currentInteraction=999),
            status=PBInteractionStatus.riMaterialSelected,
            materialSelectedPayload=PBMaterialSelected(
                selected=True,
                matHeight=plan.mat.height,
                matWidth=plan.mat.width,
            ),
        )
    )

    print("Load the following tools:")
    for head_type in [HeadType.A, HeadType.B]:
        first_tool = first_tool_in_group(grouped_paths, head_type)

        if first_tool is None:
            tool_description = "(nothing)"
        elif first_tool.name == "pen":
            color = first_pen_path_in_group(grouped_paths).color
            tool_description = f"pen ({color})"
        else:
            tool_description = first_tool.name

        print(f"Clamp {head_type.name}: {tool_description}")
    print()

    wait_clear_seen = False
    resp = dev.recv()
    match resp.status:
        case PBInteractionStatus.riWaitOnMatLoad:
            if software_buttons:
                wait_clear_seen = handle_software_mat_load(dev)
            else:
                print("Insert mat and press the Load/Unload button.")
                wait_clear_seen = wait_for_mat_loaded(dev)
        case PBInteractionStatus.riMatLoaded:
            print("Mat is already loaded.")
        case _:
            raise ProtocolError(
                f"unexpected status after material selected: {resp.status}"
            )

    if not wait_clear_seen:
        while True:
            resp = dev.recv()
            if resp.status == PBInteractionStatus.riWaitClear:
                break
            if resp.status in MAT_LOAD_IN_PROGRESS_STATUSES:
                continue
            raise ProtocolError(f"unexpected status before wait clear: {resp.status}")

    dev.recv(PBInteractionStatus.riWaitOnGo)
    if software_buttons:
        input("Press Enter to send software Go and start the cut.")
        send_software_button(
            dev, PBInteractionStatus.riMATCUTSimulateCricutButtonPressed
        )
    else:
        print("Press the Go button.")

    # Handle Go button press sequence - may vary between platforms
    # Keep consuming messages until we get riSendToolArray
    while True:
        resp = dev.recv()
        if resp.status == PBInteractionStatus.riSendToolArray:
            break
        # On macOS, we may get: riWaitClear, riWaitOnGo, riGoPressed, etc.
        # Just keep consuming until we hit riSendToolArray

    dev.send(
        PBCommonBridge(
            handle=PBInteractionHandle(currentInteraction=999),
            status=PBInteractionStatus.riToolInfoReceived,
            toolInfo=plan_tool_info(config, material, grouped_paths),
        )
    )

    dev.recv(PBInteractionStatus.riMATCUTNeedPathData)

    dev.send(
        PBCommonBridge(
            handle=PBInteractionHandle(currentInteraction=999),
            status=PBInteractionStatus.riMATCUTSetPathData,
            matPathData=plan_mat_path_data(config, plan, grouped_paths),
        )
    )

    dev.recv(PBInteractionStatus.riMATCUTProcessingPathData)
    dev.recv(PBInteractionStatus.riMATCUTProcessingPathDataComplete)

    while (resp := dev.recv()).status != PBInteractionStatus.riMATCUTCompleteSuccess:
        match resp.status:
            case PBInteractionStatus.riMATCUTNeedAccessoryChange:
                tool_names = []
                for tool in [resp.accessoryV2.current, resp.accessoryV2.required]:
                    tool_type = TOOLS_BY_PB_TOOL_TYPE[tool.toolType]
                    tool_name = tool_type.name
                    if tool_type.name == "pen":
                        tool_name = f"{tool_type.name} ({tool.color})"
                    tool_names.append(tool_name)

                current, required = tool_names
                if software_buttons:
                    print(f"Replace the {current} with {required}.")
                else:
                    print(f"Replace the {current} with {required} and press Go.")

                dev.recv(PBInteractionStatus.riWaitOnGo)
                if software_buttons:
                    input("Press Enter to send software Go and continue.")
                    send_software_button(
                        dev, PBInteractionStatus.riMATCUTSimulateCricutButtonPressed
                    )
                    resp = dev.recv()
                    if resp.status not in (
                        PBInteractionStatus.riGoPressed,
                        PBInteractionStatus.riWaitClear,
                    ):
                        raise ProtocolError(
                            f"unexpected status after software Go: {resp.status}"
                        )
                    if resp.status != PBInteractionStatus.riWaitClear:
                        dev.recv(PBInteractionStatus.riWaitClear)
                else:
                    dev.recv(PBInteractionStatus.riGoPressed)
                    dev.recv(PBInteractionStatus.riWaitClear)
            case PBInteractionStatus.riDevicePaused:
                dev.recv(PBInteractionStatus.riWaitOnGoOrPause)
                print("Cutting paused. Press Go to resume or Load/Unload to abort cut.")

                choice = dev.recv()
                if choice.status == PBInteractionStatus.riMatUnloaded:
                    print("Cutting aborted.")
                    return
                elif choice.status == PBInteractionStatus.riPausePressed:
                    print("Cutting resumed.")
                    dev.recv(PBInteractionStatus.riWaitClear)
                    dev.recv(PBInteractionStatus.riDeviceResumed)
                else:
                    raise ProtocolError(
                        f"unexpected status after pause: {choice.status}"
                    )
            case PBInteractionStatus.riMATCUTReportTool:
                current_tool = TOOLS_BY_PB_TOOL_TYPE[resp.accessoryV2.current.toolType]
                required_tool = TOOLS_BY_PB_TOOL_TYPE[
                    resp.accessoryV2.required.toolType
                ]
                # TODO: actually implement this properly. The difficulty is
                # that the plugin follows this with riSendToolArray and
                # expects you to send tool info again.
                raise UserError(
                    f"Tool error: expected {required_tool.name}, got {current_tool.name}.",
                    "Sorry, we don't currently support recovering from this, you'll have to restart the whole cut.",
                )
            case (
                PBInteractionStatus.riMATCUTGettingDevicePressureSettings
                | PBInteractionStatus.riMATCUTAccessoryChanged
                | PBInteractionStatus.riDetectingTool
                | PBInteractionStatus.riMATCUTSetProgress
                | PBInteractionStatus.riWaitForEndMoveProgress
                | PBInteractionStatus.riGoPressed
                | PBInteractionStatus.riWaitClear
            ):
                pass
            case _:
                raise ProtocolError(f"unexpected status in cut loop: {resp.status}")

    print("Cutting finished.")

    dev.recv(PBInteractionStatus.riWaitOnMatUnload)
    if software_buttons:
        input("Press Enter to send software Unload.")
        send_software_button(dev, PBInteractionStatus.riMATCUTSimulateLoadButtonPressed)
    else:
        print("Press the Load/Unload button to unload mat.")

    dev.recv(PBInteractionStatus.riMatUnloaded)
    dev.recv(PBInteractionStatus.riMatUnloaded)
    dev.recv(PBInteractionStatus.riNeedRestartInteractionConfirmation)

    # TODO:
    # status: riComplete
    # status: riCloseInteractionSuccess


def prepare_device_plugin_for_cut(device_plugin_path, config):
    proxy_path = prepare_windows_device_plugin_proxy(
        device_plugin_path,
        config.plugin_root(),
    )
    if proxy_path is not None:
        _log_device_plugin_mode("proxy", proxy_path)
        return proxy_path

    prepared_path = prepare_windows_device_plugin_patch(
        device_plugin_path,
        config.plugin_root(),
    )
    _log_device_plugin_mode(_fallback_helper_mode(device_plugin_path, prepared_path), prepared_path)
    return prepared_path


def _fallback_helper_mode(source_path, prepared_path):
    if platform.system() != "Windows":
        return "native"
    if Path(prepared_path).resolve() != Path(source_path).resolve():
        return "patch"
    return "original"


def _log_device_plugin_mode(mode, path):
    log_debug("cut.device_plugin_mode", mode=mode, path=path)
    if platform.system() == "Windows":
        print(f"Windows helper mode: {mode} ({path})")


def cut(args, config):
    device_plugin_path = resolve_device_plugin_path(args, config)
    device_plugin_path = prepare_device_plugin_for_cut(device_plugin_path, config)

    plan = Plan.from_json(json.load(args.plan))

    with DevicePlugin(device_plugin_path, config.keys.cricutdevice_request_key) as dev:
        cut_inner(config, dev, plan, software_buttons=args.software_buttons)
