"""
Triki Audio - the Triki bottle cap as a desk audio controller.

  * twist clockwise / counter-clockwise  -> volume up / down
  * press the button                     -> mute / unmute the microphone
  * flip face up / face down             -> switch the default output device

Runs in the system tray, can start with Windows and reconnects on its own.

    python triki_audio.py          # open the window
    python triki_audio.py --tray   # start hidden in the tray (used at sign-in)

Needs: pip install -r requirements.txt
"""

import argparse
import asyncio
import json
import math
import sys
import threading
import time
from collections import deque
from pathlib import Path
import tkinter as tk

import customtkinter as ctk

try:
    import triki
except ImportError:  # not installed: use the copy next to this project
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "triki-py" / "src"))
    import triki
from triki.orientation import dot

try:
    from triki.contrib.winaudio import AudioController
    AUDIO_IMPORT_ERROR = ""
except Exception as _err:  # comtypes missing / not Windows
    AudioController = None
    AUDIO_IMPORT_ERROR = str(_err)

import actions
import system
import theme
from actions import ACTIONS, ARG_HINTS, DEFAULTS, GESTURES, LABEL_TO_ID
from cap_view import CapView, Scope
from overlay import MutedBadge, Toast
from theme import Icon
from tray import Tray, cap_image

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "triki_audio_config.json"
OLD_CONFIG_FILE = HERE / "triki_cap_config.json"
ICON_FILE = HERE / "triki_audio.ico"
NO_DEVICE = "Don't switch"
LAYOUTS = {"Auto": "auto", "ABC=gyro, DEF=accel": "gyro_first", "ABC=accel, DEF=gyro": "accel_first"}
RETRY_S = 5.0


# ============================================================== small widgets
class Card(ctk.CTkFrame):
    """Rounded panel with an icon + title header and an optional widget on the right."""

    def __init__(self, master, icon, title, fonts, icon_colour=theme.ACCENT, **kw):
        super().__init__(master, fg_color=theme.SURFACE, corner_radius=16,
                         border_width=1, border_color=theme.BORDER, **kw)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(head, text=icon, font=fonts["icon"], text_color=icon_colour, width=22).pack(side="left")
        ctk.CTkLabel(head, text=title, font=fonts["h2"], text_color=theme.TEXT).pack(side="left", padx=(8, 0))
        self.head = head
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x", padx=16, pady=(4, 16))

    def head_switch(self, variable, command=None):
        sw = ctk.CTkSwitch(self.head, text="", variable=variable, onvalue=True, offvalue=False, width=44,
                           command=command, progress_color=theme.ACCENT, button_color=theme.TEXT,
                           button_hover_color="#ffffff", fg_color=theme.SURFACE_3)
        sw.pack(side="right")
        return sw


class Chip(ctk.CTkFrame):
    """Small stat tile: caption + value."""

    def __init__(self, master, caption, fonts):
        super().__init__(master, fg_color=theme.SURFACE_2, corner_radius=12)
        ctk.CTkLabel(self, text=caption.upper(), font=fonts["tiny"], text_color=theme.FAINT)\
            .pack(anchor="w", padx=12, pady=(8, 0))
        self.value = ctk.CTkLabel(self, text="-", font=fonts["value"], text_color=theme.TEXT)
        self.value.pack(anchor="w", padx=12, pady=(0, 8))
        self._text = None

    def set(self, text, colour=theme.TEXT):
        if (text, colour) != self._text:
            self._text = (text, colour)
            self.value.configure(text=text, text_color=colour)


# ============================================================== app
class App(ctk.CTk):
    def __init__(self, start_hidden=False):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=theme.BG)
        system.set_app_id()
        self.title("Triki Audio")
        self.geometry("1200x800")
        self.minsize(1000, 700)
        self._set_icon()

        f = lambda size, weight="normal": ctk.CTkFont(family=theme.FONT, size=size, weight=weight)
        self.fonts = {
            "title": f(22, "bold"), "h2": f(15, "bold"), "body": f(13), "small": f(12), "tiny": f(10, "bold"),
            "value": f(16, "bold"), "big": f(40, "bold"), "button": f(13, "bold"),
            "icon": ctk.CTkFont(family=theme.ICON_FONT, size=16),
            "icon_big": ctk.CTkFont(family=theme.ICON_FONT, size=20),
        }

        # ---- settings (persisted)
        V = lambda cls, v: cls(self, value=v)
        self.layout = V(tk.StringVar, "Auto")
        self.gyro_scale = V(tk.DoubleVar, 0.070)
        self.odr = V(tk.DoubleVar, 104.0)
        self.kp = V(tk.DoubleVar, 1.5)
        self.invert_gyro = V(tk.BooleanVar, False)
        self.vol_enabled = V(tk.BooleanVar, True)
        self.vol_deg_per_100 = V(tk.DoubleVar, 360.0)
        self.knob_deadzone = V(tk.DoubleVar, 10.0)
        self.vol_invert = V(tk.BooleanVar, False)
        self.vol_osd = V(tk.BooleanVar, True)
        self.switch_enabled = V(tk.BooleanVar, True)
        self.switch_hold = V(tk.DoubleVar, 0.6)
        self.auto_connect = V(tk.BooleanVar, True)
        self.close_to_tray = V(tk.BooleanVar, True)
        self.notify = V(tk.BooleanVar, True)
        self.mic_badge = V(tk.BooleanVar, True)
        self.upside_down = V(tk.BooleanVar, True)   # this Triki's sensor sits flipped in the cap
        self.badge_pos = None
        self.tap_threshold = V(tk.DoubleVar, 0.15)
        self.double_tap_window = V(tk.DoubleVar, 0.5)
        self.action_vars = {g: V(tk.StringVar, ACTIONS[DEFAULTS[g]][0]) for g in GESTURES}
        self.action_args = {g: V(tk.StringVar, "") for g in GESTURES}
        self._impact_hold = 0.0        # strongest knock since the last screen update (triki thread)
        self.impact_peak = 0.0
        self.flash_text, self.flash_until = "", 0.0
        self.dev_up = V(tk.StringVar, NO_DEVICE)
        self.dev_down = V(tk.StringVar, NO_DEVICE)
        self.ble_choice = V(tk.StringVar, "")
        self.startup_var = V(tk.BooleanVar, system.is_startup_enabled())
        self.dev_ids = {"up": "", "down": ""}
        self.ble_address = ""
        self.ble_name = ""

        # ---- runtime state
        self._calls = deque()          # functions queued from other threads
        self.cap = None                # triki.BackgroundTriki
        self.simulated = False
        self.want_connected = False
        self.conn_state = "idle"       # idle / connecting / retry / calibrating / live / demo
        self.retry_at = 0.0
        self.connecting = False
        self.history = deque(maxlen=300)
        self.inbox = deque()
        self.rate, self._rate_mark = 0.0, (time.time(), 0)
        self.ble_devices = {}          # option label -> address
        self.out_devices = {}          # audio device name -> id
        self.status_text = "Ready"
        self.status_until = 0.0
        self._hidden_hint_shown = False

        self.audio = None
        self.audio_error = AUDIO_IMPORT_ERROR
        if AudioController is not None:
            try:
                self.audio = AudioController()   # this (Tk) thread owns all COM calls
            except Exception as ex:
                self.audio_error = str(ex)
        self.vol_level = None
        self.vol_read_at = 0.0
        self.mic_muted = None
        self.key_accum = 0.0

        self.load_config()
        self.tray = Tray(self)
        self._build()
        self.refresh_audio_devices()
        self.toast = Toast(self)
        self.badge = MutedBadge(self, on_moved=self._badge_moved)
        self.badge.pos = self.badge_pos
        self.tray.start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if start_hidden and self.tray.available:
            self.withdraw()
        if self.auto_connect.get() and self.ble_address:
            self.after(300, self.connect)
        self._tick()

    # ------------------------------------------------------------------ layout
    def _set_icon(self):
        try:
            if not ICON_FILE.exists():
                cap_image(256).save(ICON_FILE, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
            self.iconbitmap(str(ICON_FILE))
        except Exception:
            pass

    def _build(self):
        F = self.fonts
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ---- header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 10))
        self._logo = ctk.CTkImage(cap_image(96), size=(40, 40))
        ctk.CTkLabel(head, image=self._logo, text="").pack(side="left")
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(titles, text="Triki Audio", font=F["title"], text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(titles, text="Twist for volume · flip for output · click and tap for anything",
                     font=F["small"], text_color=theme.MUTED).pack(anchor="w")

        self.conn_btn = ctk.CTkButton(head, text="Connect", width=130, height=38, corner_radius=19,
                                      font=F["button"], command=self.toggle_connection,
                                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                      text_color=theme.ACCENT_TEXT)
        self.conn_btn.pack(side="right")
        pill = ctk.CTkFrame(head, fg_color=theme.SURFACE, corner_radius=19, height=38,
                            border_width=1, border_color=theme.BORDER)
        pill.pack(side="right", padx=(0, 10))
        self.pill_dot = ctk.CTkLabel(pill, text="●", font=F["small"], text_color=theme.FAINT)
        self.pill_dot.pack(side="left", padx=(14, 6), pady=6)
        self.pill_text = ctk.CTkLabel(pill, text="Not connected", font=F["body"], text_color=theme.TEXT)
        self.pill_text.pack(side="left", padx=(0, 16), pady=6)

        # ---- body
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=24)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        stage = ctk.CTkFrame(body, fg_color=theme.SURFACE, corner_radius=20,
                             border_width=1, border_color=theme.BORDER)
        stage.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self.view = CapView(stage, bg=theme.SURFACE)
        self.view.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        chips = ctk.CTkFrame(stage, fg_color="transparent")
        chips.pack(fill="x", padx=16, pady=16)
        self.chip_face = Chip(chips, "Position", F)
        self.chip_tilt = Chip(chips, "Tilt", F)
        self.chip_twist = Chip(chips, "Twist", F)
        self.chip_button = Chip(chips, "Button", F)
        for i, chip in enumerate((self.chip_face, self.chip_tilt, self.chip_twist, self.chip_button)):
            chips.grid_columnconfigure(i, weight=1, uniform="chips")
            chip.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))

        tabs = ctk.CTkTabview(body, width=410, fg_color=theme.BG, corner_radius=16,
                              segmented_button_fg_color=theme.SURFACE,
                              segmented_button_selected_color=theme.SURFACE_3,
                              segmented_button_selected_hover_color=theme.SURFACE_3,
                              segmented_button_unselected_color=theme.SURFACE,
                              segmented_button_unselected_hover_color=theme.SURFACE_2,
                              text_color=theme.TEXT, anchor="w")
        tabs.grid(row=0, column=1, sticky="ns")
        tabs._segmented_button.configure(font=F["button"], height=34)
        self.tabs = tabs
        for name in ("Controls", "Actions", "Device", "Sensor"):
            tabs.add(name)
        self._build_controls(self._scroll(tabs.tab("Controls")))
        self._build_actions(self._scroll(tabs.tab("Actions")))
        self._build_device(self._scroll(tabs.tab("Device")))
        self._build_sensor(self._scroll(tabs.tab("Sensor")))

        # ---- footer
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=26, pady=(8, 12))
        self.status_label = ctk.CTkLabel(foot, text="", font=F["small"], text_color=theme.MUTED, anchor="w")
        self.status_label.pack(side="left")
        ctk.CTkLabel(foot, text=f"triki {triki.__version__}", font=F["small"], text_color=theme.FAINT)\
            .pack(side="right")

    def _scroll(self, tab):
        frame = ctk.CTkScrollableFrame(tab, fg_color="transparent", width=380,
                                       scrollbar_button_color=theme.SURFACE_2,
                                       scrollbar_button_hover_color=theme.SURFACE_3)
        frame.pack(fill="both", expand=True)
        return frame

    def _label(self, parent, text, muted=True, font="small", **pack):
        lbl = ctk.CTkLabel(parent, text=text, font=self.fonts[font], anchor="w", justify="left",
                           text_color=theme.MUTED if muted else theme.TEXT, wraplength=330)
        lbl.pack(fill="x", **pack)
        return lbl

    def _switch_row(self, parent, text, variable, command=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkLabel(row, text=text, font=self.fonts["body"], text_color=theme.TEXT).pack(side="left")
        ctk.CTkSwitch(row, text="", variable=variable, onvalue=True, offvalue=False, width=44, command=command,
                      progress_color=theme.ACCENT, button_color=theme.TEXT, button_hover_color="#ffffff",
                      fg_color=theme.SURFACE_3).pack(side="right")
        return row

    def _slider_row(self, parent, text, variable, lo, hi, fmt, steps=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(8, 2))
        ctk.CTkLabel(row, text=text, font=self.fonts["body"], text_color=theme.TEXT).pack(side="left")
        val = ctk.CTkLabel(row, text="", font=self.fonts["small"], text_color=theme.MUTED)
        val.pack(side="right")
        ctk.CTkSlider(parent, from_=lo, to=hi, variable=variable, number_of_steps=steps, height=16,
                      progress_color=theme.ACCENT, button_color=theme.ACCENT, button_hover_color=theme.ACCENT_HOVER,
                      fg_color=theme.SURFACE_3).pack(fill="x", pady=(2, 4))

        def refresh(*_):
            try:
                val.configure(text=fmt(variable.get()))
            except (tk.TclError, ValueError):
                pass
        variable.trace_add("write", refresh)
        refresh()

    def _option(self, parent, variable, values, command=None, width=200):
        return ctk.CTkOptionMenu(parent, variable=variable, values=values or [""], command=command, width=width,
                                 height=32, corner_radius=10, font=self.fonts["body"],
                                 dropdown_font=self.fonts["body"], fg_color=theme.SURFACE_2,
                                 button_color=theme.SURFACE_3, button_hover_color=theme.BORDER,
                                 dropdown_fg_color=theme.SURFACE_2, dropdown_hover_color=theme.SURFACE_3,
                                 text_color=theme.TEXT, dynamic_resizing=False)

    def _button(self, parent, text, command, primary=False, **kw):
        if primary:
            colours = dict(fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color=theme.ACCENT_TEXT)
        else:
            colours = dict(fg_color=theme.SURFACE_2, hover_color=theme.SURFACE_3, text_color=theme.TEXT,
                           border_width=1, border_color=theme.BORDER)
        return ctk.CTkButton(parent, text=text, command=command, height=34, corner_radius=10,
                             font=self.fonts["button"], **colours, **kw)

    # ---- tab: controls
    def _build_controls(self, root):
        F = self.fonts
        vol = Card(root, Icon.VOLUME, "Volume", F)
        vol.pack(fill="x", pady=(4, 12))
        vol.head_switch(self.vol_enabled)
        row = ctk.CTkFrame(vol.body, fg_color="transparent")
        row.pack(fill="x")
        self.vol_big = ctk.CTkLabel(row, text="--", font=F["big"], text_color=theme.TEXT)
        self.vol_big.pack(side="left")
        ctk.CTkLabel(row, text="Twist clockwise\nto turn it up", font=F["small"], text_color=theme.MUTED,
                     justify="right").pack(side="right")
        self.vol_bar = ctk.CTkProgressBar(vol.body, height=8, corner_radius=4, progress_color=theme.ACCENT,
                                          fg_color=theme.SURFACE_3)
        self.vol_bar.pack(fill="x", pady=(4, 8))
        self.vol_bar.set(0)
        self._slider_row(vol.body, "Full sweep", self.vol_deg_per_100, 90, 1080,
                         lambda v: f"{v:.0f}° for 0-100%", steps=33)
        self._switch_row(vol.body, "Windows volume overlay", self.vol_osd)
        self._switch_row(vol.body, "Reverse direction", self.vol_invert)

        mic = Card(root, Icon.MIC, "Microphone", F)
        mic.pack(fill="x", pady=(0, 12))
        self.mic_btn = ctk.CTkButton(mic.body, text="Microphone", height=46, corner_radius=23,
                                     font=F["button"], command=self.toggle_mic_manual)
        self.mic_btn.pack(fill="x", pady=(2, 8))
        self._label(mic.body, "Click the cap's button to mute or unmute (change it on the Actions tab). "
                              "You can also click here.")

        out = Card(root, Icon.SPEAKERS, "Output device", F)
        out.pack(fill="x", pady=(0, 12))
        out.head_switch(self.switch_enabled)
        self.face_rows = {}
        for face, label, icon, var in (("up", "Face up", Icon.UP, self.dev_up),
                                       ("down", "Face down", Icon.DOWN, self.dev_down)):
            r = ctk.CTkFrame(out.body, fg_color=theme.SURFACE_2, corner_radius=12, border_width=1,
                             border_color=theme.SURFACE_2)
            r.pack(fill="x", pady=4)
            top = ctk.CTkFrame(r, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(8, 4))
            ctk.CTkLabel(top, text=icon, font=F["icon"], text_color=theme.MUTED, width=20).pack(side="left")
            ctk.CTkLabel(top, text=label, font=F["body"], text_color=theme.TEXT).pack(side="left", padx=(6, 0))
            menu = self._option(r, var, [NO_DEVICE], command=lambda _v, f=face: self._pick_device(f), width=300)
            menu.pack(fill="x", padx=12, pady=(0, 10))
            self.face_rows[face] = (r, menu)
        self._slider_row(out.body, "Hold before switching", self.switch_hold, 0.1, 2.0,
                         lambda v: f"{v:.1f} s", steps=19)
        self._button(out.body, "Refresh devices", self.refresh_audio_devices).pack(anchor="w", pady=(8, 0))

        note = Card(root, Icon.MIC_OFF, "Notifications", F, icon_colour=theme.BAD)
        note.pack(fill="x", pady=(0, 12))
        self._switch_row(note.body, "Pop-ups (mic, output, volume)", self.notify)
        self._switch_row(note.body, "Muted icon while mic is muted", self.mic_badge)
        self._label(note.body, "The muted icon stays on top of other windows. Drag it wherever you like.",
                    pady=(4, 6))
        self._button(note.body, "Reset icon position", lambda: self.badge.reset_position()).pack(anchor="w")

    # ---- tab: actions
    def _build_actions(self, root):
        F = self.fonts
        card = Card(root, "\uE7C9", "Gestures", F)
        card.pack(fill="x", pady=(4, 12))
        self.arg_entries = {}
        labels = [label for label, _i, _a in ACTIONS.values()]
        for g, text in GESTURES.items():
            row = ctk.CTkFrame(card.body, fg_color=theme.SURFACE_2, corner_radius=12)
            row.pack(fill="x", pady=4)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=10)
            ctk.CTkLabel(inner, text=text, font=F["body"], text_color=theme.TEXT, anchor="w").pack(fill="x")
            self._option(inner, self.action_vars[g], labels, width=300,
                         command=lambda _v, g=g: self._action_changed(g)).pack(fill="x", pady=(4, 0))
            box = ctk.CTkFrame(inner, fg_color="transparent")
            entry = ctk.CTkEntry(box, textvariable=self.action_args[g], height=32, corner_radius=8,
                                 fg_color=theme.BG, border_color=theme.BORDER, text_color=theme.TEXT,
                                 font=F["body"])
            entry.pack(fill="x")
            entry.bind("<FocusOut>", lambda _e: self.save_config())
            box.hint = ctk.CTkLabel(box, text="", font=F["small"], text_color=theme.FAINT, anchor="w")
            box.hint.pack(fill="x")
            self.arg_entries[g] = box
            aid = self._action_id(g)
            if ACTIONS[aid][2]:
                box.hint.configure(text=ARG_HINTS[aid])
                box.pack(fill="x", pady=(6, 0))
        self._label(card.body, "Twist (volume) and flip (output device) are set on the Controls tab. "
                               "Actions only run while a real Triki is connected.", pady=(6, 0))

        taps = Card(root, "\uE945", "Table taps", F, icon_colour=theme.INFO)
        taps.pack(fill="x", pady=(0, 12))
        self._label(taps.body, "Knock on the desk next to the cap while it lies still. Pressing the "
                               "button or moving the cap never counts as a tap.", pady=(0, 6))
        row = ctk.CTkFrame(taps.body, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(row, text="Strongest knock", font=F["body"], text_color=theme.TEXT).pack(side="left")
        self.tap_value = ctk.CTkLabel(row, text="0.00 g", font=F["small"], text_color=theme.MUTED)
        self.tap_value.pack(side="right")
        self.tap_meter = ctk.CTkProgressBar(taps.body, height=10, corner_radius=5, fg_color=theme.SURFACE_3,
                                           progress_color=theme.FAINT)
        self.tap_meter.pack(fill="x", pady=(4, 2))
        self.tap_meter.set(0)
        ctk.CTkLabel(taps.body, text="the middle of the bar is the threshold", font=F["small"],
                     text_color=theme.FAINT).pack(anchor="center")
        self.tap_flash = ctk.CTkLabel(taps.body, text="", font=F["button"], text_color=theme.MUTED)
        self.tap_flash.pack(fill="x", pady=(6, 0))
        self._slider_row(taps.body, "Knock needed", self.tap_threshold, 0.03, 0.6,
                         lambda v: f"{v:.2f} g", steps=57)
        self._label(taps.body, "Lower = more sensitive. If typing or setting down a mug triggers it, raise it.",
                    pady=(0, 4))
        self._slider_row(taps.body, "Time between the two knocks", self.double_tap_window, 0.2, 1.0,
                         lambda v: f"up to {v:.2f} s", steps=16)

    # ---- tab: device
    def _build_device(self, root):
        F = self.fonts
        cap = Card(root, Icon.BLUETOOTH, "Triki", F, icon_colour=theme.INFO)
        cap.pack(fill="x", pady=(4, 12))
        row = ctk.CTkFrame(cap.body, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        self.ble_menu = self._option(row, self.ble_choice, [], width=250)
        self.ble_menu.pack(side="left", fill="x", expand=True)
        self.scan_btn = self._button(row, "Scan", self.scan, width=80)
        self.scan_btn.pack(side="right", padx=(8, 0))
        if self.ble_address:
            label = f"{self.ble_name or 'Triki'}  ·  {self.ble_address}"
            self.ble_devices[label] = self.ble_address
            self.ble_menu.configure(values=[label])
            self.ble_choice.set(label)
        else:
            self.ble_choice.set("Press Scan to find your Triki")
        btns = ctk.CTkFrame(cap.body, fg_color="transparent")
        btns.pack(fill="x", pady=(4, 6))
        self._button(btns, "Calibrate", self.calibrate, width=110).pack(side="left")
        self._button(btns, "Zero pose", self.zero, width=110).pack(side="left", padx=8)
        self._button(btns, "Try demo", self.demo, width=110).pack(side="right")
        self._switch_row(cap.body, "Connect automatically", self.auto_connect)
        self._switch_row(cap.body, "Sensor is upside down", self.upside_down)
        self._label(cap.body, "Calibrate with the cap lying still. If the 3D cap shows the wrong side up, "
                              "flip 'Sensor is upside down'. Zero pose makes the current position count "
                              "as 'face up' until the next calibration and resets yaw drift.", pady=(4, 0))

        bg = Card(root, Icon.POWER, "Background", F, icon_colour=theme.GOOD)
        bg.pack(fill="x", pady=(0, 12))
        self._switch_row(bg.body, "Start with Windows", self.startup_var, command=self._startup_switched)
        self._switch_row(bg.body, "Close button hides to tray", self.close_to_tray)
        tip = ("Right-click the cap icon in the taskbar tray for quick controls. To keep it always visible, "
               "drag it from the ^ overflow onto the taskbar.") if self.tray.available \
            else "Install pystray to enable the tray icon."
        self._label(bg.body, tip, pady=(4, 0))

        gest = Card(root, Icon.SETTINGS, "Gestures", F, icon_colour=theme.MUTED)
        gest.pack(fill="x", pady=(0, 12))
        self._slider_row(gest.body, "Twist deadzone", self.knob_deadzone, 0, 60, lambda v: f"{v:.0f}°/s", steps=60)
        self._label(gest.body, "Twists slower than this are ignored, so the volume doesn't drift.", pady=(2, 0))

    # ---- tab: sensor
    def _build_sensor(self, root):
        F = self.fonts
        grid = ctk.CTkFrame(root, fg_color="transparent")
        grid.pack(fill="x", pady=(4, 8))
        self.stat = {}
        for i, name in enumerate(("Roll", "Pitch", "Yaw", "|a|", "Rate", "Frames")):
            chip = Chip(grid, name, F)
            chip.grid(row=i // 3, column=i % 3, sticky="ew", padx=(0 if i % 3 == 0 else 6, 0), pady=3)
            grid.grid_columnconfigure(i % 3, weight=1, uniform="stats")
            self.stat[name] = chip
        self.gyro_scope = Scope(root, "Gyroscope", 360, "°/s")
        self.gyro_scope.pack(fill="x", pady=4)
        self.acc_scope = Scope(root, "Accelerometer", 2.0, "g")
        self.acc_scope.pack(fill="x", pady=4)

        card = Card(root, Icon.SENSOR, "Sensor model", F, icon_colour=theme.MUTED)
        card.pack(fill="x", pady=(8, 12))
        r = ctk.CTkFrame(card.body, fg_color="transparent")
        r.pack(fill="x", pady=3)
        ctk.CTkLabel(r, text="Axis layout", font=F["body"], text_color=theme.TEXT).pack(side="left")
        self._option(r, self.layout, list(LAYOUTS), width=190).pack(side="right")
        for text, var in (("Gyro °/s per LSB", self.gyro_scale), ("Sample rate (Hz)", self.odr)):
            r = ctk.CTkFrame(card.body, fg_color="transparent")
            r.pack(fill="x", pady=3)
            ctk.CTkLabel(r, text=text, font=F["body"], text_color=theme.TEXT).pack(side="left")
            ctk.CTkEntry(r, textvariable=var, width=90, height=30, corner_radius=8, fg_color=theme.SURFACE_2,
                         border_color=theme.BORDER, text_color=theme.TEXT, font=F["body"]).pack(side="right")
        self._slider_row(card.body, "Accelerometer trust", self.kp, 0, 6, lambda v: f"Kp {v:.1f}", steps=60)
        self._switch_row(card.body, "Invert gyro", self.invert_gyro)
        self._switch_row(card.body, "Show axes in 3D view", tk.BooleanVar(self, False),
                         command=lambda: setattr(self.view, "show_axes", not self.view.show_axes))
        self._label(card.body, "Axis layout applies at the next calibration.", pady=(4, 0))

    # ------------------------------------------------------------- threads -> Tk
    def call_soon(self, fn):
        """Run fn on the Tk thread (safe from any thread)."""
        self._calls.append(fn)

    def set_status(self, text, seconds=6.0):
        def apply():
            self.status_text, self.status_until = text, time.time() + seconds
        self.call_soon(apply)

    # ------------------------------------------------------------- connection
    def _apply_config(self, cfg):
        try:
            cfg.layout = LAYOUTS.get(self.layout.get(), "auto")
            cfg.gyro_scale = float(self.gyro_scale.get())
            cfg.sample_rate = max(1.0, float(self.odr.get()))
            cfg.kp = float(self.kp.get())
            cfg.invert_gyro = bool(self.invert_gyro.get())
            cfg.twist_deadzone_dps = float(self.knob_deadzone.get())
            cfg.flip_hold_s = float(self.switch_hold.get())
            cfg.upside_down = bool(self.upside_down.get())
            cfg.tap_threshold_g = float(self.tap_threshold.get())
            cfg.double_tap_s = float(self.double_tap_window.get())
        except (tk.TclError, ValueError):
            pass  # half-typed value - keep the previous settings
        return cfg

    def _start(self, address=None, simulate=False):
        if self.connecting or self.cap is not None:
            return
        self.connecting = True
        self.simulated = simulate
        self.conn_state = "connecting"
        cap = triki.BackgroundTriki(address, self._apply_config(triki.Config()), simulate=simulate)
        cap.on("frame", self._on_frame)
        for name in ("calibrated", "twist", "flip", "tap", *GESTURES, "single_click"):
            cap.on(name, self.inbox.append)   # handled on the Tk thread

        def worker():
            try:
                cap.start(timeout=25)
            except Exception as ex:
                self.call_soon(lambda: self._connect_failed(ex))
                return
            self.call_soon(lambda: self._connected(cap))
        threading.Thread(target=worker, daemon=True).start()

    def _connected(self, cap):
        self.connecting = False
        if not self.want_connected and not self.simulated:  # cancelled meanwhile
            threading.Thread(target=cap.stop, daemon=True).start()
            self.conn_state = "idle"
            return
        self.cap = cap
        self.history.clear()
        self.conn_state = "calibrating"
        self.set_status("Keep the cap still for a moment while it calibrates")

    def _connect_failed(self, ex):
        self.connecting = False
        msg = str(ex) or ex.__class__.__name__
        if self.want_connected and not self.simulated:
            self.conn_state, self.retry_at = "retry", time.time() + RETRY_S
            self.set_status(f"Couldn't connect ({msg}) - retrying", RETRY_S)
        else:
            self.conn_state = "idle"
            self.set_status(f"Couldn't connect: {msg}")

    def _drop_cap(self):
        cap, self.cap = self.cap, None
        if cap is not None:
            threading.Thread(target=cap.stop, daemon=True).start()

    def connect(self):
        address = self.ble_devices.get(self.ble_choice.get()) or self.ble_address
        if not address:
            self.show_window()
            self.tabs.set("Device")
            self.set_status("Scan and pick your Triki first")
            return
        if self.simulated:
            self._drop_cap()
        self.ble_address = address
        self.ble_name = self.ble_choice.get().split("  ·  ")[0] if "  ·  " in self.ble_choice.get() else self.ble_name
        self.want_connected = True
        self.save_config()
        self._start(address)

    def disconnect(self):
        self.want_connected = False
        self._drop_cap()
        self.conn_state = "idle"
        self.set_status("Disconnected")

    def toggle_connection(self):
        if self.want_connected or self.simulated and self.cap:
            self.disconnect()
            self.simulated = False
        else:
            self.connect()

    def demo(self):
        self.want_connected = False
        self._drop_cap()
        self.after(200, lambda: self._start(simulate=True))
        self.set_status("Demo mode - motion is simulated, your audio is left alone")

    def calibrate(self):
        if self.cap:
            self.cap.calibrate()
            self.conn_state = "calibrating"
            self.set_status("Calibrating - keep the cap still")

    def zero(self):
        if self.cap:
            self.cap.zero()
            self.set_status("Current position is now 'face up'")

    def scan(self):
        self.scan_btn.configure(state="disabled", text="...")
        self.set_status("Scanning for Bluetooth devices...")

        def worker():
            try:
                found = asyncio.run(triki.scan(6.0))
            except Exception as ex:
                self.set_status(f"Scan failed: {ex}")
                found = None

            def update():
                self.scan_btn.configure(state="normal", text="Scan")
                if found is None:
                    return
                found.sort(key=lambda d: not d.likely_triki)
                self.ble_devices = {f"{d.name}  ·  {d.address}": d.address for d in found}
                labels = list(self.ble_devices) or ["No devices found"]
                self.ble_menu.configure(values=labels)
                self.ble_choice.set(labels[0])
                n = sum(d.likely_triki for d in found)
                self.set_status(f"Found {len(found)} device(s), {n} Triki" if found else "No devices found")
            self.call_soon(update)
        threading.Thread(target=worker, daemon=True).start()

    @property
    def live(self):
        """True when gestures should drive the PC's audio."""
        return (self.cap is not None and not self.simulated and self.cap.is_connected
                and not self.cap.state.calibrating and self.audio is not None)

    # ------------------------------------------------------------- audio
    def refresh_audio_devices(self):
        if not self.audio:
            self.set_status(f"Audio control unavailable: {self.audio_error}", 30)
            return
        try:
            devs = self.audio.list_outputs()
        except Exception as ex:
            self.set_status(f"Couldn't list audio devices: {ex}")
            return
        self.out_devices = {}
        for dev_id, name in devs:
            label = name if name not in self.out_devices else f"{name} ({dev_id[-6:]})"
            self.out_devices[label] = dev_id
        names = [NO_DEVICE] + list(self.out_devices)
        by_id = {v: k for k, v in self.out_devices.items()}
        for face, var in (("up", self.dev_up), ("down", self.dev_down)):
            self.face_rows[face][1].configure(values=names)
            var.set(by_id.get(self.dev_ids[face], NO_DEVICE))

    def _device_name(self, dev_id):
        return next((n for n, i in self.out_devices.items() if i == dev_id), "output device")

    def _pick_device(self, face):
        var = self.dev_up if face == "up" else self.dev_down
        self.dev_ids[face] = self.out_devices.get(var.get(), "")
        self.save_config()
        if self.cap:
            self._switch_output(self.cap.state.face)

    def _switch_output(self, face):
        if not (self.live and self.switch_enabled.get() and face):
            return
        target = self.dev_ids.get(face)
        if target and target != self.audio.default_output_id():
            self.audio.set_default_output(target)
            self.vol_level = None
            name = self._device_name(target)
            self.set_status(f"Output switched to {name}")
            if self.notify.get():
                self.toast.show(Icon.HEADPHONES if face == "down" else Icon.SPEAKERS, name,
                                "Output device", theme.INFO)

    def _change_volume(self, degrees):
        step = degrees / max(1.0, float(self.vol_deg_per_100.get()))
        if self.vol_invert.get():
            step = -step
        if self.vol_level is None:
            self.vol_level = self.audio.get_volume()
        if self.vol_osd.get():
            # real volume keys: Windows steps 2 % per press and shows its own overlay
            self.key_accum += step * 100
            keys = max(-10, min(10, int(self.key_accum / 2)))  # rest follows next tick
            if keys:
                self.key_accum -= keys * 2
                self.audio.press_volume_key(keys > 0, abs(keys))
                self.vol_level = max(0.0, min(1.0, self.vol_level + keys * 0.02))
        else:
            before = self.vol_level
            self.vol_level = self.audio.set_volume(self.vol_level + step)
            if self.notify.get() and round(before * 100) != round(self.vol_level * 100):
                self.toast.show(Icon.VOLUME, f"Volume {self.vol_level * 100:.0f}%", seconds=0.9)
        self.vol_read_at = time.time()

    def toggle_mic_manual(self):
        if self.audio:
            self._toggle_mic()

    def _toggle_mic(self):
        self.mic_muted = self.audio.toggle_mic()
        if self.mic_muted is None:
            self.set_status("No microphone found")
            return
        self.set_status("Microphone muted" if self.mic_muted else "Microphone on")
        if self.notify.get():
            if self.mic_muted:
                self.toast.show(Icon.MIC_OFF, "Microphone muted", colour=theme.BAD)
            else:
                self.toast.show(Icon.MIC, "Microphone on", colour=theme.GOOD)

    def _handle_events(self):
        turned = 0.0
        while self.inbox:
            e = self.inbox.popleft()
            if e.name == "calibrated":
                if e.value["still"]:
                    self.set_status("Calibrated - ready")
                else:
                    self.set_status("The cap moved during calibration - press Calibrate to redo it")
                self._switch_output(e.state.face)   # output follows the cap from the start
            elif e.name == "twist":
                turned += e.value
            elif e.name in GESTURES or e.name == "single_click":
                self._gesture(e.name)
            elif e.name == "tap":
                self._flash(f"Tap  ({e.value:.2f} g)")
            elif e.name == "flip":
                self._switch_output(e.value)
        if (turned or abs(self.key_accum) >= 2) and self.live and self.vol_enabled.get():
            self._change_volume(turned)

    # ------------------------------------------------------------- actions
    def _on_frame(self, e):
        """Runs on the triki thread for every sample - keep it tiny."""
        self.history.append((e.state.gyro_dps, e.state.accel_g))
        if e.state.impact_g > self._impact_hold:
            self._impact_hold = e.state.impact_g

    def _flash(self, text, seconds=1.2):
        self.flash_text, self.flash_until = text, time.time() + seconds

    def _action_id(self, gesture):
        return LABEL_TO_ID.get(self.action_vars[gesture].get(), "none")

    def _gesture(self, name):
        if name in ("click", "single_click"):
            # With a double-click action set, a click must wait to see whether a second one follows.
            wait = self._action_id("double_click") != "none"
            if (name == "single_click") != wait:
                return
            name = "click"
        if name == "double_tap":
            self._flash("Double tap!", 1.5)
        aid = self._action_id(name)
        if aid == "none":
            return
        label = ACTIONS[aid][0]
        if not self.live:
            if self.simulated:
                self.set_status(f"Demo: {GESTURES[name].lower()} would run '{label}'")
            return
        self._run_action(aid, self.action_args[name].get())

    def _run_action(self, aid, arg=""):
        label, icon, _needs_arg = ACTIONS[aid]
        try:
            if aid == "mic":
                self._toggle_mic()
                return
            if aid == "output_cycle":
                self._cycle_output()
                return
            if aid == "play_pause":
                actions.tap_key(actions.VK["playpause"])
            elif aid == "next":
                actions.tap_key(actions.VK["nexttrack"])
            elif aid == "prev":
                actions.tap_key(actions.VK["prevtrack"])
            elif aid == "mute":
                actions.tap_key(actions.VK["volumemute"])   # Windows shows its own overlay
                return
            elif aid == "shortcut":
                actions.send_shortcut(arg)
                label = arg.strip() or label
            elif aid == "open":
                actions.open_target(arg)
                label = "Opening " + Path(arg.strip().strip('"')).name if arg.strip() else label
            if self.notify.get():
                self.toast.show(icon, label, seconds=1.0)
        except Exception as ex:
            self.set_status(f"'{label}' failed: {ex}")

    def _cycle_output(self):
        devs = self.audio.list_outputs()
        if len(devs) < 2:
            self.set_status("Only one output device - nothing to switch to")
            return
        ids = [d for d, _ in devs]
        cur = self.audio.default_output_id()
        dev_id, name = devs[(ids.index(cur) + 1) % len(devs)] if cur in ids else devs[0]
        self.audio.set_default_output(dev_id)
        self.vol_level = None
        self.set_status(f"Output switched to {name}")
        if self.notify.get():
            self.toast.show(Icon.SPEAKERS, name, "Output device", theme.INFO)

    def _action_changed(self, gesture):
        box = self.arg_entries[gesture]
        aid = self._action_id(gesture)
        if ACTIONS[aid][2]:
            box.hint.configure(text=ARG_HINTS[aid])
            box.pack(fill="x", pady=(6, 0))
        else:
            box.pack_forget()
        self.save_config()

    def _audio_tick(self, now):
        if self.audio is None:
            self.inbox.clear()
            return
        try:
            self._handle_events()
            if self.vol_level is None or now - self.vol_read_at > 1.0:
                self.vol_level, self.vol_read_at = self.audio.get_volume(), now
                self.mic_muted = self.audio.get_mic_muted()   # follow changes made elsewhere
        except Exception as ex:
            self.set_status(f"Audio error: {ex}")
            self.vol_level = None

    # ------------------------------------------------------------- main loop
    def _tick(self):
        now = time.time()
        while self._calls:
            try:
                self._calls.popleft()()
            except Exception as ex:
                self.status_text = f"Error: {ex}"

        cap = self.cap
        if cap is not None:
            self._apply_config(cap.config)
            if not cap.is_connected:
                self._drop_cap()
                cap = None
                if self.want_connected and not self.simulated:
                    self.conn_state, self.retry_at = "retry", now + RETRY_S
                    self.set_status("Connection lost - reconnecting")
                else:
                    self.conn_state = "idle"
            elif self.conn_state in ("calibrating", "connecting"):
                if not cap.state.calibrating:
                    self.conn_state = "demo" if self.simulated else "live"
        if self.conn_state == "retry" and now >= self.retry_at and self.want_connected:
            self._start(self.ble_address)

        self._audio_tick(now)
        st = cap.state if cap else triki.State()
        if now - self._rate_mark[0] >= 1.0:
            self.rate = max(0.0, (st.frame - self._rate_mark[1]) / (now - self._rate_mark[0]))
            self._rate_mark = (now, st.frame)
        self._update_chrome(now)
        if self.state() == "normal":
            self._render(st)
        self.after(33, self._tick)

    def _conn_display(self, now):
        s = self.conn_state
        if s == "live":
            return "Connected", theme.GOOD
        if s == "demo":
            return "Demo mode", theme.INFO
        if s == "calibrating":
            return "Calibrating - keep still", theme.INFO
        if s == "connecting":
            return "Connecting...", theme.WARN
        if s == "retry":
            return f"Reconnecting in {max(0, math.ceil(self.retry_at - now))} s", theme.WARN
        return "Not connected", theme.FAINT

    def tray_status_text(self):
        text, _ = self._conn_display(time.time())
        mic = "" if self.mic_muted is None else (" · mic muted" if self.mic_muted else " · mic on")
        return text + mic

    def _update_chrome(self, now):
        text, colour = self._conn_display(now)
        self._set(self.pill_text, text=text)
        self._set(self.pill_dot, text_color=colour)
        connected_ui = self.want_connected or (self.simulated and self.cap is not None) or self.connecting
        if connected_ui:
            self._set(self.conn_btn, text="Disconnect", fg_color=theme.SURFACE_2, hover_color=theme.SURFACE_3,
                      text_color=theme.TEXT, border_width=1, border_color=theme.BORDER)
        else:
            self._set(self.conn_btn, text="Connect", fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                      text_color=theme.ACCENT_TEXT, border_width=0)
        status = self.status_text if now < self.status_until else ""
        self._set(self.status_label, text=status)

        badge = {"live": theme.GOOD, "demo": theme.INFO, "calibrating": theme.INFO,
                 "connecting": theme.WARN, "retry": theme.WARN}.get(self.conn_state)
        if self.mic_muted and self.conn_state == "live":
            badge = theme.BAD
        self.tray.update(badge, "Triki Audio - " + self.tray_status_text())
        want_badge = bool(self.mic_badge.get() and self.mic_muted)
        if want_badge != self.badge.visible:
            self.badge.show() if want_badge else self.badge.hide()

        # volume + mic widgets (cheap, only redrawn on change)
        if self.vol_level is not None:
            pct = round(self.vol_level * 100)
            self._set(self.vol_big, text=f"{pct}%")
            if getattr(self, "_bar_pct", None) != pct:
                self._bar_pct = pct
                self.vol_bar.set(pct / 100)
        if self.mic_muted is None:
            self._set(self.mic_btn, text="No microphone", fg_color=theme.SURFACE_2, hover_color=theme.SURFACE_3,
                      text_color=theme.MUTED)
        elif self.mic_muted:
            self._set(self.mic_btn, text="Muted  -  click to unmute", fg_color=theme.BAD, hover_color="#ff6a62",
                      text_color="#ffffff")
        else:
            self._set(self.mic_btn, text="Microphone on", fg_color=theme.GOOD, hover_color="#4cc95d",
                      text_color="#ffffff")

    def _badge_moved(self, x, y):
        self.badge_pos = (x, y)
        self.save_config()

    def _set(self, widget, **kw):
        """configure() only when something changed - CTk redraws are not free."""
        cache = widget.__dict__.setdefault("_triki_cache", {})
        changed = {k: v for k, v in kw.items() if cache.get(k) != v}
        if changed:
            cache.update(changed)
            widget.configure(**changed)

    def _render(self, st):
        mic_glow = None
        if self.conn_state == "live" and self.mic_muted is not None:
            mic_glow = theme.BAD if self.mic_muted else None
        self.view.render(st.quaternion, st.button, glow=mic_glow)

        face = {"up": "Face up", "down": "Face down", None: "On edge"}[st.face] if self.cap else "-"
        self.chip_face.set(face)
        self.chip_tilt.set(f"{st.tilt:.0f}°" if self.cap else "-")
        self.chip_twist.set(f"{st.twist_dps:+.0f}°/s" if self.cap else "-",
                            theme.ACCENT if abs(st.twist_dps) >= self.knob_deadzone.get() and self.cap else theme.TEXT)
        self.chip_button.set("Pressed" if st.button else "Released" if self.cap else "-",
                             theme.GOOD if st.button else theme.TEXT)
        for f, (row, _menu) in self.face_rows.items():
            active = self.cap is not None and st.face == f
            self._set(row, border_color=theme.ACCENT if active else theme.SURFACE_2)

        if self.tabs.get() == "Actions":
            hold, self._impact_hold = self._impact_hold, 0.0
            self.impact_peak = max(hold, self.impact_peak * 0.92)
            thr = max(0.01, float(self.tap_threshold.get()))
            frac = min(1.0, self.impact_peak / (2 * thr))   # threshold sits in the middle of the bar
            if abs(frac - getattr(self, "_meter_frac", -1)) > 0.01:
                self._meter_frac = frac
                self.tap_meter.set(frac)
            self._set(self.tap_meter, progress_color=theme.GOOD if self.impact_peak >= thr else theme.FAINT)
            self._set(self.tap_value, text=f"{self.impact_peak:.2f} g")
            flash = self.flash_text if time.time() < self.flash_until else ""
            self._set(self.tap_flash, text=flash or "Knock twice next to the cap to try it")
            self._set(self.tap_flash, text_color=theme.GOOD if flash else theme.MUTED)
        if self.tabs.get() == "Sensor":
            a = st.accel_g
            self.stat["Roll"].set(f"{st.roll:+.1f}°")
            self.stat["Pitch"].set(f"{st.pitch:+.1f}°")
            self.stat["Yaw"].set(f"{st.yaw:+.1f}°")
            self.stat["|a|"].set(f"{math.sqrt(dot(a, a)):.2f} g")
            self.stat["Rate"].set(f"{self.rate:.0f} Hz")
            self.stat["Frames"].set(f"{st.frame}")
            hist = list(self.history)
            self.gyro_scope.render([h[0] for h in hist], st.gyro_dps)
            self.acc_scope.render([h[1] for h in hist], st.accel_g)

    # ------------------------------------------------------------- window / tray
    def show_window(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def on_close(self):
        if self.close_to_tray.get() and self.tray.available:
            self.withdraw()
            self.save_config()
            if not self._hidden_hint_shown and self.notify.get():
                self._hidden_hint_shown = True
                self.toast.show(Icon.POWER, "Still running", "Triki Audio is in the tray", seconds=2.5)
        else:
            self.quit_app()

    def quit_app(self):
        self.save_config()
        self.want_connected = False
        cap, self.cap = self.cap, None
        if cap:
            cap.stop()
        self.tray.stop()
        self.destroy()

    def toggle_startup(self):
        self.startup_var.set(not self.startup_var.get())
        self._startup_switched()

    def _startup_switched(self):
        want = bool(self.startup_var.get())
        try:
            system.set_startup(want)
        except OSError as ex:
            self.set_status(f"Couldn't change startup setting: {ex}")
        self.startup_var.set(system.is_startup_enabled())
        self.set_status("Triki Audio will start with Windows (in the tray)" if self.startup_var.get()
                        else "Triki Audio won't start with Windows")

    @property
    def startup_enabled(self):
        return bool(self.startup_var.get())

    # ------------------------------------------------------------- config
    CONFIG_KEYS = ("layout", "gyro_scale", "odr", "kp", "invert_gyro", "vol_enabled", "vol_deg_per_100",
                   "knob_deadzone", "vol_invert", "vol_osd", "switch_enabled", "switch_hold",
                   "tap_threshold", "double_tap_window",
                   "auto_connect", "close_to_tray", "notify", "mic_badge", "upside_down")

    def save_config(self):
        try:
            data = {k: getattr(self, k).get() for k in self.CONFIG_KEYS}
            data["actions"] = {g: {"action": self._action_id(g), "arg": self.action_args[g].get()}
                               for g in GESTURES}
            data.update(dev_ids=self.dev_ids, ble_address=self.ble_address, ble_name=self.ble_name,
                        badge_pos=list(self.badge.pos) if getattr(self, "badge", None) and self.badge.pos
                        else self.badge_pos)
            CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as ex:
            self.set_status(f"Couldn't save settings: {ex}")

    def load_config(self):
        path = CONFIG_FILE if CONFIG_FILE.exists() else OLD_CONFIG_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        for k in self.CONFIG_KEYS:
            if k in data:
                try:
                    getattr(self, k).set(data[k])
                except tk.TclError:
                    pass
        if self.layout.get() not in LAYOUTS:
            self.layout.set("Auto")
        self.dev_ids.update({k: v for k, v in data.get("dev_ids", {}).items() if k in self.dev_ids})
        self.ble_address = data.get("ble_address", "")
        self.ble_name = data.get("ble_name", "")
        saved = data.get("actions")
        if saved is None and data.get("mic_enabled") is False:   # settings from before Actions existed
            saved = {"click": {"action": "none"}}
        for g, spec in (saved or {}).items():
            if g in GESTURES and spec.get("action") in ACTIONS:
                self.action_vars[g].set(ACTIONS[spec["action"]][0])
                self.action_args[g].set(spec.get("arg", ""))
        pos = data.get("badge_pos")
        self.badge_pos = tuple(pos) if isinstance(pos, list) and len(pos) == 2 else None


def main():
    parser = argparse.ArgumentParser(description="Triki Audio")
    parser.add_argument("--tray", action="store_true", help="start hidden in the system tray")
    args = parser.parse_args()

    holder = {}
    instance = system.SingleInstance(lambda: holder["app"].call_soon(holder["app"].show_window)
                                     if "app" in holder else None)
    if not instance.acquire():
        return  # already running - it has been asked to show itself
    try:
        holder["app"] = App(start_hidden=args.tray)
        holder["app"].mainloop()
    finally:
        instance.release()


if __name__ == "__main__":
    main()
