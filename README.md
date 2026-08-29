# Voice Meeter Meeter




## Previous Session

What it is: vm_monitor.py — a borderless, always-on-top tkinter widget showing one LED dot per VoiceMeeter channel. Green = audio present, gray = quiet. Drag with left-click, right-click for quit/toggle-topmost.

Dependency: pip install voicemeeter-api (imports as voicemeeterlib, wraps VoiceMeeter's Remote API DLL — works with Potato).

Architecture:

LevelPoller — background thread, polls vm.bus[i].levels.all (outputs) or vm.strip[i].levels.postfader (inputs) ~20x/sec, pushes active/inactive booleans through a queue.Queue
MonitorWidget — tkinter Tk subclass, drains the queue every 50ms via after() and repaints dots. Never touches VoiceMeeter directly (keeps tkinter thread-safe)

Key config (top of file):

CHANNELS — list of {"label", "kind": "bus"/"strip", "index"} dicts, controls what's shown and in what order
THRESHOLD_DB (-40) — dB level counted as "active"
HOLD_MS (250) — how long a dot stays lit after audio dips, to avoid flicker
Index mapping for Potato is documented in the docstring: strips 0-4 physical/5-7 virtual ins, buses 0-4 = A1-A5, 5-7 = B1-B3

Known open items / good next steps in VS Code:

Bus index order should be verified against the user's actual routing (make noise on each output, confirm correct dot lights up) — indices can shift depending on hardware config
True click-through isn't implemented — needs a Win32 layered-window approach (pywin32) since plain tkinter can't do it
No persistence yet for window position across restarts, if that's wanted
Could add a settings/tray icon instead of hardcoded CHANNELS list



-----

SET UP

```
python -m venv venv
venv\Scripts\activate
pip install voicemeeter-api pyinstaller
python vm_monitor.py
```



RUNNING
```
venv\Scripts\activate
python vm_monitor.py
```


BUILDING

```
venv\Scripts\activate
venv\Scripts\pyinstaller.exe --onefile --windowed --icon=icon.ico --name "VoiceMeeterMeters" vm_monitor.py
```