# Cricut Joy support notes

## Hardware/UI difference

Cricut Joy does not have physical Go or Load/Unload buttons. Any SliceBug flow that prints instructions such as:

- `Press the Go button.`
- `Press the Load/Unload button.`
- `Insert mat and press the Load/Unload button.`

is not sufficient for Cricut Joy support. The CLI needs to send the equivalent software/protocol commands or expose explicit CLI actions for Go and unload.

## Current local observations

- Machine profile imported by bootstrap: `V060224J5416`.
- Bootstrap works on macOS with the `hoff/macos-support` branch.
- Cricut Joy mat being tested: `4.5 x 12 in`.
- When the example cut reached mat-load state, pressing/triggering load produced protocol status `143` before normal continuation. `143` is not named in the current `Bridge_pb2.py` enum snapshot.
- The cut flow can reach `Press the Go button.` after tolerating status `143`, but Cricut Joy needs a software Go command.

## Software button candidates

The generated `PBInteractionStatus` enum already contains three likely software-button simulation messages:

- `riMATCUTSimulateLoadButtonPressed = 719`
- `riMATCUTSimulateCricutButtonPressed = 720`
- `riMATCUTSimulatePauseButtonPressed = 721`

These are present in SliceBug's existing protobuf snapshot and in strings from Cricut Design Space's bundled `app.asar`, so the first implementation path is to send `PBCommonBridge` messages with these statuses when the plugin reports a wait state.

Current experimental mapping:

- Load mat: after `riWaitOnMatLoad`, send `riMATCUTSimulateLoadButtonPressed`.
- Go/start cut: after `riWaitOnGo`, send `riMATCUTSimulateCricutButtonPressed`.
- Unload mat: after `riWaitOnMatUnload`, send `riMATCUTSimulateLoadButtonPressed` again; this appears to represent the combined Load/Unload button.

To keep the flow safe, the CLI should still prompt and wait for Enter before sending software Load, Go, or Unload. Sending software Go can start an actual cut.

## Local changes currently tracked

- `setup.py`: packaging fixes so editable install works locally.
- `slicebug/cli/plan.py`: hardcoded mat dimensions to `4.5 x 12 in` for Cricut Joy experiments.
- `examples/blobs.json`: edited example plan mat/material width to `4.5 in`.
- `slicebug/cli/cut.py`: tolerate status `143` during mat-load flow and add experimental `--software-buttons` support for buttonless machines such as Cricut Joy.

## Next protocol work

Validate how Cricut Design Space sends these operations for Cricut Joy:

1. Software Load/Unload mat.
2. Software Go / start cut.
3. Any Joy-specific statuses around mat loading, Go, pause, cancel, and unload.

Remaining places to inspect:

- Cricut Design Space `app.asar` UI/controller code.
- `device-common/CricutDevice` protobuf traffic while using Design Space.
- `Bridge_pb2.py` enum gaps around unnamed status `143`.
- Runtime logs from the CricutDevice plugin while Design Space performs load/go/unload on the same Joy.
- Existing `PBInteractionStatus` values related to `riGoPressed`, `riMatUnloaded`, `riSetRestartInteractionConfirmation`, and `riComplete`.
