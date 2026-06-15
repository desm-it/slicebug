# slicebug
slicebug is a command-line tool for preparing and executing cutting jobs on Cricut cutters.

slicebug interacts with cutters by reusing undocumented components of Cricut Design Space. It is not developed or authorized by Cricut. Using slicebug might damage your cutter.

# Requirements
- Windows or macOS
- Cricut Design Space installed, signed in, and used for at least one cut with the machine you want to use
- Your cutter connected, awake, and not connected to another computer when running `slicebug cut`
- On macOS: ensure only one Cricut device is paired (remove any Bluetooth-paired devices from System Settings > Bluetooth if you're connecting via USB)

slicebug is developed in Python 3.10. You don't need Python to run it, just download a compiled version by clicking the "Releases" section on the right.

## Tested machines

- Original Cricut Maker
- Cricut Joy

## Running from source

You can run slicebug directly from source on macOS or Windows using Python:

```
# Create a virtual environment
python -m venv .venv

# Activate it on macOS
source .venv/bin/activate

# Or activate it on Windows PowerShell
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install cryptography protobuf

# Bootstrap slicebug (copies required data from Cricut Design Space)
python -m slicebug bootstrap

# List available materials
python -m slicebug list-materials

# Execute a cut plan
python -m slicebug cut examples/blobs.json
```

# Usage example

slicebug is a command-line utility, so you'll need a terminal to use it. On Windows, I recommend [Windows Terminal](https://aka.ms/terminal).

After downloading and unpacking slicebug, go to the directory where you unpacked it:

```
PS C:\Users\Bill> cd Downloads\slicebug
PS C:\Users\Bill\Downloads\slicebug>
```

The first time you use slicebug, you'll need to "bootstrap" it. This will copy some information over from your install of Cricut Design Space:

```
PS C:\Users\Bill\Downloads\slicebug> .\slicebug.exe bootstrap
Importing plugins from C:\Users\Bill\AppData\Local\Program\Cricut Design Space.
...
Machines imported.
```

If bootstrap finds multiple saved machine profiles, it asks you to name them. Later commands can select one by putting `--profile name` before the command:

```
PS C:\Users\Bill\Downloads\slicebug> .\slicebug.exe --profile joy list-materials
```

Take a quick look at the output and make sure there aren't any errors. If everything went well, try using the `list-materials` and `list-tools` commands:

```
PS C:\Users\Bill\Downloads\slicebug> .\slicebug.exe list-materials
...
Cardstock:
  ...
  - [218] Light Cardstock - 65 lb (176 gsm)
  - [ 19] Medium Cardstock - 80 lb (216 gsm)
  ...
...
PS C:\Users\Bill\Downloads\slicebug> .\slicebug.exe list-tools 218
Tools for Light Cardstock - 65 lb (176 gsm):
  - scoring_stylus
  - scoring_wheel
  - pen
  ...
```

To cut something out, you'll first need to create a _plan_. A plan is a file containing full instructions for how to cut a single mat: what to cut and with which tools. slicebug includes a command that can create a plan from an SVG, picking tools based on stroke color. Choose the mat dimensions with either a named preset or an explicit size:

```
PS C:\Users\Bill\Downloads\slicebug> .\slicebug.exe plan examples\blobs.svg blobs_plan.json `
>> --material 218 `
>> --mat-preset maker-standard `
>> --map 000000:fine_point_blade `
>> --map ff0000:pen `
>> --map 0000ff:pen
```

You can also pass a custom mat size in inches:

```
PS C:\Users\Bill\Downloads\slicebug> .\slicebug.exe plan examples\blobs.svg blobs_plan.json `
>> --material 218 `
>> --mat-size 4.5x12 `
>> --map 000000:fine_point_blade
```

Common presets include `joy-standard` and `joy-standard-long` (4.5 x 12 in), `joy-standard-short` (4.5 x 6.5 in), `joy-card` (4.5 x 6.25 in), `maker-standard` (12 x 12 in), and `maker-long` (12 x 24 in). The `joy-card` preset selects the card mat dimensions only; card-mat-specific machine behavior is not implemented separately. If the SVG is larger than the selected mat, slicebug prints a warning and still writes the plan for backwards compatibility; pass `--reject-oversize` to fail instead.

(The tick \` at the end of a line means that the command continues on the next line. Try `slicebug plan --help` to learn about other options that this command accepts.)
```
Found 3 paths:
 - 1 paths with stroke color #000000, mapped to fine_point_blade.
 - 1 paths with stroke color #0000ff, mapped to pen.
 - 1 paths with stroke color #ff0000, mapped to pen.
```
You should now have a `blobs_plan.json` file you can use for a cut:
```
PS C:\Users\Bill\Downloads\slicebug> .\slicebug.exe cut blobs_plan.json
Load the following tools:
Clamp A: pen (#000000)
Clamp B: fine_point_blade

Insert mat and press the Load/Unload button.
...
```

For buttonless machines such as Cricut Joy, use software button prompts:

```
PS C:\Users\Bill\Downloads\slicebug> .\slicebug.exe cut --software-buttons blobs_plan.json
```

Just follow the instructions and your cut should complete!

If startup times out or slicebug says the helper is busy, close Cricut Design Space, make sure the cutter is awake, and try again.

# Debug logging

Debug logging is off by default. To write a debug log for troubleshooting, put `--log` before the command:

```
PS C:\Users\Bill\Downloads\slicebug> .\slicebug.exe --log cut blobs_plan.json
```

Release builds write `slicebug-debug.log` next to `slicebug.exe` by default. When running from source, the default path is next to the Python executable. Set `SLICEBUG_DEBUG_LOG` to choose a different path.

# Things that don't work yet

- Machine coverage is still limited
  - Basic cutting has been tested on the Original Cricut Maker and Cricut Joy.
  - Buttonless machines can use `--software-buttons`.
  - Smart Materials and other machine-specific workflows are not supported yet.
- Operating systems other than Windows and macOS
  - Linux:
  	- CricutDevice.exe does not run under Wine, but perhaps it does under one of the forks?
    - `slicebug plan` works under Linux already if you copy the bootstrapped files from a Windows machine and manually install usvg.
- Print then Cut
  - Should be doable.
