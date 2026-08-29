"""
VoiceMeeter Potato - mini channel activity monitor
====================================================

A tiny, borderless, always-on-top window that shows a vertical level bar
per VoiceMeeter channel (bus and/or strip) you care about. Each bar fills
with the channel's level, turning yellow then red as it gets loud. Drag
it anywhere on your desktop with the left mouse button. Right-click for
a small menu.

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

import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
import winreg
from tkinter import font as tkfont

import pystray
import voicemeeterlib
from PIL import Image


def _resource_path(name):
    # PyInstaller --onefile extracts bundled data files to sys._MEIPASS at runtime.
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _position_file():
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "VoiceMeeterMeters", "position.json")


def _virtual_screen_bounds():
    # Spans all monitors, not just the primary one.
    user32 = ctypes.windll.user32
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, w, h


def _load_saved_position(width, height):
    try:
        with open(_position_file()) as f:
            data = json.load(f)
        x, y = int(data["x"]), int(data["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return None

    vx, vy, vw, vh = _virtual_screen_bounds()
    margin = 50  # keep at least this much of the window on-screen
    if x + width < vx + margin or x + margin > vx + vw or y + height < vy + margin or y + margin > vy + vh:
        return None  # saved spot is off-screen (e.g. a monitor got unplugged)
    return x, y


def _save_position(x, y):
    path = _position_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"x": x, "y": y}, f)
    except OSError:
        pass


STARTUP_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REGISTRY_NAME = "VoiceMeeterMeters"


def _is_launch_at_startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REGISTRY_KEY) as key:
            winreg.QueryValueEx(key, STARTUP_REGISTRY_NAME)
        return True
    except FileNotFoundError:
        return False


def _set_launch_at_startup(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REGISTRY_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_REGISTRY_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(key, STARTUP_REGISTRY_NAME)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# CONFIG - edit this section to match what you want to watch
# ---------------------------------------------------------------------------

KIND = "potato"            # 'basic', 'banana', or 'potato'
LAUNCH_VOICEMEETER = False  # True = auto-launch VM if it isn't running

# Which channels to show, in display order.
CHANNELS = [
    {"label": "I1", "kind": "strip", "index": 0},
    {"label": "I2", "kind": "strip", "index": 1},
    {"label": "A1", "kind": "bus", "index": 0},
    {"label": "A2", "kind": "bus", "index": 1},
    {"label": "B1", "kind": "bus", "index": 5},
    {"label": "B2", "kind": "bus", "index": 6},
    {"label": "B3", "kind": "bus", "index": 7},
]

METER_MIN_DB = -60.0       # dB mapped to the bottom (empty) of the bar
METER_MAX_DB = 0.0         # dB mapped to the top (full) of the bar
YELLOW_DB = -12.0          # bar turns yellow above this level
RED_DB = -3.0              # bar turns red above this level (near clipping)
RELEASE_DB_PER_SEC = 30.0  # how fast the bar falls when level drops,
                           # so it doesn't flicker on quiet passages
POLL_HZ = 20               # how often to sample levels per second
ORIENTATION = "horizontal"  # "horizontal" or "vertical"
OPACITY = 0.92             # 0.0 - 1.0 window transparency

BAR_WIDTH = 16
BAR_HEIGHT = 70

TITLE_TEXT = "Voice Meeter Meters"
TITLE_HEIGHT = 22
TITLE_BG = "#141414"
GITHUB_URL = "https://github.com/Chenzo/voice-meeter-meters"
ICON_PATH = _resource_path("icon.ico")

BG_COLOR = "#1e1e1e"
BAR_OFF = "#3a3a3a"
BAR_GREEN = "#33d17a"
BAR_YELLOW = "#e5c22d"
BAR_RED = "#e5484d"
BAR_BORDER = "#0f0f0f"
TEXT_COLOR = "#cfcfcf"

_METER_SPAN_DB = METER_MAX_DB - METER_MIN_DB
_YELLOW_FRAC = (YELLOW_DB - METER_MIN_DB) / _METER_SPAN_DB
_RED_FRAC = (RED_DB - METER_MIN_DB) / _METER_SPAN_DB

# ---------------------------------------------------------------------------
# Background poller - talks to VoiceMeeter, never touches tkinter widgets
# ---------------------------------------------------------------------------


class LevelPoller(threading.Thread):
    def __init__(self, out_queue: queue.Queue):
        super().__init__(daemon=True)
        self.out_queue = out_queue
        self._stop = threading.Event()
        self._display_db = {i: METER_MIN_DB for i in range(len(CHANNELS))}

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            with voicemeeterlib.api(KIND) as vm:
                if LAUNCH_VOICEMEETER:
                    vm.login()
                interval = 1.0 / POLL_HZ
                last_time = time.time()
                while not self._stop.is_set():
                    now = time.time()
                    dt = now - last_time
                    last_time = now
                    levels = []
                    for i, ch in enumerate(CHANNELS):
                        db = self._read_level_db(vm, ch)
                        target = (
                            METER_MIN_DB
                            if db is None
                            else max(METER_MIN_DB, min(METER_MAX_DB, db))
                        )
                        prev = self._display_db[i]
                        if target >= prev:
                            display = target  # rise instantly, like a real meter
                        else:
                            display = max(target, prev - RELEASE_DB_PER_SEC * dt)
                        self._display_db[i] = display
                        levels.append(display)
                    self.out_queue.put(("levels", levels))
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
        self._restore_position()

        self.queue: queue.Queue = queue.Queue()
        self.poller = LevelPoller(self.queue)
        self.poller.start()
        self.after(50, self._pump_queue)

        self.protocol("WM_DELETE_WINDOW", self._quit)

        self.tray_icon = self._build_tray_icon()
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    # -- system tray --------------------------------------------------------

    def _build_tray_icon(self):
        image = Image.open(ICON_PATH)
        items = [pystray.MenuItem("Show/Hide", self._tray_toggle_visibility, default=True)]
        if getattr(sys, "frozen", False):
            items.append(
                pystray.MenuItem(
                    "Launch at Startup",
                    self._tray_toggle_launch_at_startup,
                    checked=lambda item: _is_launch_at_startup_enabled(),
                )
            )
        items.append(pystray.MenuItem("Quit", self._tray_quit))
        return pystray.Icon("VoiceMeeterMeters", image, TITLE_TEXT, pystray.Menu(*items))

    def _tray_toggle_visibility(self, icon, item):
        self.after(0, self._toggle_visibility)

    def _toggle_visibility(self):
        if self.state() == "withdrawn":
            self.deiconify()
        else:
            self.withdraw()

    def _tray_toggle_launch_at_startup(self, icon, item):
        _set_launch_at_startup(not _is_launch_at_startup_enabled())

    def _tray_quit(self, icon, item):
        self.after(0, self._quit)

    # -- layout --------------------------------------------------------

    def _build_ui(self):
        self._build_title_bar()

        pad = 8
        label_font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        frame = tk.Frame(self, bg=BG_COLOR, padx=pad, pady=pad)
        frame.pack()

        self.bars = []
        for i, ch in enumerate(CHANNELS):
            if ORIENTATION == "horizontal":
                cell = tk.Frame(frame, bg=BG_COLOR)
                cell.grid(row=0, column=i, padx=6)
                canvas = tk.Canvas(
                    cell, width=BAR_WIDTH, height=BAR_HEIGHT, bg=BG_COLOR, highlightthickness=0
                )
                zone_rects = self._make_bar(canvas)
                canvas.pack()
                tk.Label(
                    cell, text=ch["label"], font=label_font, fg=TEXT_COLOR, bg=BG_COLOR
                ).pack()
            else:
                cell = tk.Frame(frame, bg=BG_COLOR)
                cell.grid(row=i, column=0, pady=4, sticky="w")
                canvas = tk.Canvas(
                    cell, width=BAR_WIDTH, height=BAR_HEIGHT, bg=BG_COLOR, highlightthickness=0
                )
                zone_rects = self._make_bar(canvas)
                canvas.grid(row=0, column=0)
                tk.Label(
                    cell, text=ch["label"], font=label_font, fg=TEXT_COLOR, bg=BG_COLOR
                ).grid(row=0, column=1, padx=(6, 0))
            self.bars.append((canvas, *zone_rects))

    def _build_title_bar(self):
        title_font = tkfont.Font(family="Segoe UI", size=8, weight="bold")
        bar = tk.Frame(self, bg=TITLE_BG, height=TITLE_HEIGHT)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)

        tk.Label(
            bar, text=TITLE_TEXT, font=title_font, fg=TEXT_COLOR, bg=TITLE_BG
        ).pack(side="left", padx=8)

        self.close_btn = tk.Label(
            bar, text="✕", font=title_font, fg=TEXT_COLOR, bg=TITLE_BG, cursor="hand2"
        )
        self.close_btn.pack(side="right", padx=8)
        self.close_btn.bind("<Button-1>", lambda e: self._quit())

        self.help_btn = tk.Label(
            bar, text="?", font=title_font, fg=TEXT_COLOR, bg=TITLE_BG, cursor="hand2"
        )
        self.help_btn.pack(side="right")
        self.help_btn.bind("<Button-1>", lambda e: webbrowser.open(GITHUB_URL))

    @staticmethod
    def _make_bar(canvas):
        canvas.create_rectangle(0, 0, BAR_WIDTH, BAR_HEIGHT, fill=BAR_OFF, outline="")
        green_rect = canvas.create_rectangle(0, BAR_HEIGHT, BAR_WIDTH, BAR_HEIGHT, fill=BAR_GREEN, outline="")
        yellow_rect = canvas.create_rectangle(0, BAR_HEIGHT, BAR_WIDTH, BAR_HEIGHT, fill=BAR_YELLOW, outline="")
        red_rect = canvas.create_rectangle(0, BAR_HEIGHT, BAR_WIDTH, BAR_HEIGHT, fill=BAR_RED, outline="")
        canvas.create_rectangle(1, 1, BAR_WIDTH - 1, BAR_HEIGHT - 1, outline=BAR_BORDER)
        return green_rect, yellow_rect, red_rect

    # -- dragging --------------------------------------------------------

    def _bind_drag(self):
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        for child in self.winfo_children():
            self._bind_drag_recursive(child)

    def _bind_drag_recursive(self, widget):
        if widget in (self.close_btn, self.help_btn):
            return
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

    def _restore_position(self):
        self.update_idletasks()
        pos = _load_saved_position(self.winfo_reqwidth(), self.winfo_reqheight())
        if pos is not None:
            self.geometry(f"+{pos[0]}+{pos[1]}")

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
                    for (canvas, green_rect, yellow_rect, red_rect), db in zip(self.bars, payload):
                        self._update_bar(canvas, green_rect, yellow_rect, red_rect, db)
                elif kind == "error":
                    self.title(f"VM error: {payload}")
        except queue.Empty:
            pass
        self.after(50, self._pump_queue)

    @staticmethod
    def _update_bar(canvas, green_rect, yellow_rect, red_rect, db):
        frac = max(0.0, min(1.0, (db - METER_MIN_DB) / _METER_SPAN_DB))
        filled_px = frac * BAR_HEIGHT
        yellow_px = _YELLOW_FRAC * BAR_HEIGHT
        red_px = _RED_FRAC * BAR_HEIGHT

        green_h = min(filled_px, yellow_px)
        yellow_h = max(0.0, min(filled_px, red_px) - yellow_px)
        red_h = max(0.0, filled_px - red_px)

        canvas.coords(green_rect, 0, BAR_HEIGHT - green_h, BAR_WIDTH, BAR_HEIGHT)
        canvas.coords(yellow_rect, 0, BAR_HEIGHT - green_h - yellow_h, BAR_WIDTH, BAR_HEIGHT - green_h)
        canvas.coords(
            red_rect, 0, BAR_HEIGHT - green_h - yellow_h - red_h, BAR_WIDTH, BAR_HEIGHT - green_h - yellow_h
        )

    def _quit(self):
        _save_position(self.winfo_x(), self.winfo_y())
        self.poller.stop()
        self.tray_icon.stop()
        self.destroy()


if __name__ == "__main__":
    app = MonitorWidget()
    app.mainloop()
