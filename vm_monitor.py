"""
VoiceMeeter Potato - mini channel activity monitor
====================================================

A tiny, borderless, always-on-top window that shows a colored LED per
VoiceMeeter channel (bus and/or strip) you care about. Glows green when
audio is present on that channel, gray when it's quiet. Drag it anywhere
on your desktop with the left mouse button. Right-click for a small menu.

Requirements
------------
    pip install voicemeeter-api

VoiceMeeter Potato must already be running (or this script will launch it -
see `LAUNCH_VOICEMEETER` below).

Configuration
-------------
Edit the CHANNELS list below to pick exactly which channels to show, and
in what order. Each entry is a dict:

    {"label": "A1", "kind": "bus",   "index": 0}
    {"label": "Mic", "kind": "strip", "index": 0}

For VoiceMeeter POTATO the index mapping is:

    STRIPS (inputs), index 0-7:
        0-4 = physical Hardware Ins  1-5  (labelled "IN 1".."IN 5" in the UI)
        5-7 = virtual  Voicemeeter Ins 1-3  ("VAIO", "AUX", "VAIO3")

    BUSES (outputs), index 0-7:
        0-4 = physical A1-A5 (hardware outputs)
        5-7 = virtual  B1-B3

So "A1, A2, B1, B2, B3" (as you described) = bus indices 0, 1, 5, 6, 7.
That's the default configuration below - change it to match your routing.
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import voicemeeterlib

# ---------------------------------------------------------------------------
# CONFIG - edit this section to match what you want to watch
# ---------------------------------------------------------------------------

KIND = "potato"            # 'basic', 'banana', or 'potato'
LAUNCH_VOICEMEETER = False  # True = auto-launch VM if it isn't running

# Which channels to show, in display order.
CHANNELS = [
    {"label": "A1", "kind": "bus", "index": 0},
    {"label": "A2", "kind": "bus", "index": 1},
    {"label": "B1", "kind": "bus", "index": 5},
    {"label": "B2", "kind": "bus", "index": 6},
    {"label": "B3", "kind": "bus", "index": 7},
]

THRESHOLD_DB = -40.0     # louder than this = "active" (green)
HOLD_MS = 250             # keep the LED lit this long after audio drops,
                           # so it doesn't flicker on quiet passages
POLL_HZ = 20               # how often to sample levels per second
ORIENTATION = "horizontal"  # "horizontal" or "vertical"
OPACITY = 0.92             # 0.0 - 1.0 window transparency

BG_COLOR = "#1e1e1e"
LED_ON = "#33d17a"
LED_OFF = "#4a4a4a"
LED_BORDER = "#0f0f0f"
TEXT_COLOR = "#cfcfcf"

# ---------------------------------------------------------------------------
# Background poller - talks to VoiceMeeter, never touches tkinter widgets
# ---------------------------------------------------------------------------


class LevelPoller(threading.Thread):
    def __init__(self, out_queue: queue.Queue):
        super().__init__(daemon=True)
        self.out_queue = out_queue
        self._stop = threading.Event()
        self._last_active_at = {i: 0.0 for i in range(len(CHANNELS))}

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            with voicemeeterlib.api(KIND) as vm:
                if LAUNCH_VOICEMEETER:
                    vm.login()
                interval = 1.0 / POLL_HZ
                while not self._stop.is_set():
                    now = time.time()
                    states = []
                    for i, ch in enumerate(CHANNELS):
                        db = self._read_level_db(vm, ch)
                        if db is not None and db > THRESHOLD_DB:
                            self._last_active_at[i] = now
                        active = (now - self._last_active_at[i]) * 1000 <= HOLD_MS
                        states.append(active)
                    self.out_queue.put(("levels", states))
                    time.sleep(interval)
        except Exception as exc:  # e.g. VoiceMeeter not running
            self.out_queue.put(("error", str(exc)))

    @staticmethod
    def _read_level_db(vm, ch):
        try:
            if ch["kind"] == "bus":
                vals = vm.bus[ch["index"]].levels.all
            else:
                vals = vm.strip[ch["index"]].levels.postfader
            if not vals:
                return None
            if isinstance(vals, (list, tuple)):
                return max(vals)
            return vals
        except Exception:
            return None


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


class MonitorWidget(tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)      # no title bar / borders
        self.attributes("-topmost", True)
        self.attributes("-alpha", OPACITY)
        self.configure(bg=BG_COLOR)

        self._drag = {"x": 0, "y": 0}
        self._build_ui()
        self._bind_drag()
        self._bind_menu()

        self.queue: queue.Queue = queue.Queue()
        self.poller = LevelPoller(self.queue)
        self.poller.start()
        self.after(50, self._pump_queue)

        self.protocol("WM_DELETE_WINDOW", self._quit)

    # -- layout --------------------------------------------------------

    def _build_ui(self):
        pad = 8
        label_font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        frame = tk.Frame(self, bg=BG_COLOR, padx=pad, pady=pad)
        frame.pack()

        self.dots = []
        for i, ch in enumerate(CHANNELS):
            if ORIENTATION == "horizontal":
                cell = tk.Frame(frame, bg=BG_COLOR)
                cell.grid(row=0, column=i, padx=6)
                canvas = tk.Canvas(
                    cell, width=18, height=18, bg=BG_COLOR, highlightthickness=0
                )
                dot = canvas.create_oval(2, 2, 16, 16, fill=LED_OFF, outline=LED_BORDER)
                canvas.pack()
                tk.Label(
                    cell, text=ch["label"], font=label_font, fg=TEXT_COLOR, bg=BG_COLOR
                ).pack()
            else:
                cell = tk.Frame(frame, bg=BG_COLOR)
                cell.grid(row=i, column=0, pady=4, sticky="w")
                canvas = tk.Canvas(
                    cell, width=18, height=18, bg=BG_COLOR, highlightthickness=0
                )
                dot = canvas.create_oval(2, 2, 16, 16, fill=LED_OFF, outline=LED_BORDER)
                canvas.grid(row=0, column=0)
                tk.Label(
                    cell, text=ch["label"], font=label_font, fg=TEXT_COLOR, bg=BG_COLOR
                ).grid(row=0, column=1, padx=(6, 0))
            self.dots.append((canvas, dot))

    # -- dragging --------------------------------------------------------

    def _bind_drag(self):
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        for child in self.winfo_children():
            self._bind_drag_recursive(child)

    def _bind_drag_recursive(self, widget):
        widget.bind("<ButtonPress-1>", self._on_press)
        widget.bind("<B1-Motion>", self._on_motion)
        for child in widget.winfo_children():
            self._bind_drag_recursive(child)

    def _on_press(self, event):
        self._drag["x"] = event.x_root - self.winfo_x()
        self._drag["y"] = event.y_root - self.winfo_y()

    def _on_motion(self, event):
        x = event.x_root - self._drag["x"]
        y = event.y_root - self._drag["y"]
        self.geometry(f"+{x}+{y}")

    # -- right-click menu --------------------------------------------------------

    def _bind_menu(self):
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Toggle always-on-top", command=self._toggle_topmost)
        self.menu.add_command(label="Quit", command=self._quit)
        self.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        for child in self.winfo_children():
            self._bind_menu_recursive(child)

    def _bind_menu_recursive(self, widget):
        widget.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        for child in widget.winfo_children():
            self._bind_menu_recursive(child)

    def _toggle_topmost(self):
        cur = self.attributes("-topmost")
        self.attributes("-topmost", not cur)

    # -- data pump --------------------------------------------------------

    def _pump_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "levels":
                    for (canvas, dot), active in zip(self.dots, payload):
                        canvas.itemconfig(dot, fill=LED_ON if active else LED_OFF)
                elif kind == "error":
                    self.title(f"VM error: {payload}")
        except queue.Empty:
            pass
        self.after(50, self._pump_queue)

    def _quit(self):
        self.poller.stop()
        self.destroy()


if __name__ == "__main__":
    app = MonitorWidget()
    app.mainloop()
