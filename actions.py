"""Things a gesture can do: media keys, keyboard shortcuts, launching apps / websites."""

import ctypes
import os

from theme import Icon

# ---------------------------------------------------------------- catalogue
# id -> (label shown in the UI, toast icon, needs a text argument)
ACTIONS = {
    "none": ("Do nothing", "", False),
    "mic": ("Mute / unmute microphone", Icon.MIC, False),
    "play_pause": ("Play / pause", "\uE768", False),
    "next": ("Next track", "\uE893", False),
    "prev": ("Previous track", "\uE892", False),
    "mute": ("Mute / unmute speakers", "\uE74F", False),
    "output_cycle": ("Switch output device", Icon.SPEAKERS, False),
    "shuffle": ("Toggle shuffle", "", False),
    "next_profile": ("Next profile", "", False),
    "shortcut": ("Keyboard shortcut...", "\uE765", True),
    "open": ("Open app or website...", "\uE8A7", True),
}
LABEL_TO_ID = {label: key for key, (label, _icon, _arg) in ACTIONS.items()}

# gesture id (triki event) -> label
GESTURES = {
    "click": "Click the button",
    "double_click": "Double-click the button",
    "long_press": "Hold the button",
    "double_tap": "Double-tap the table",
    "shake": "Shake",
}
BUTTON_GESTURES = ("click", "double_click", "long_press")
DEFAULTS = {"click": "play_pause", "double_click": "next", "long_press": "mic", "double_tap": "prev",
            "shake": "open"}
DEFAULT_ARGS = {"shake": "https://open.spotify.com/"}

ARG_HINTS = {
    "shortcut": "e.g. ctrl+shift+m  or  win+d",
    "open": r"e.g. https://open.spotify.com  or  C:\path\app.exe",
}


# ---------------------------------------------------------------- keyboard
_user32 = ctypes.windll.user32
KEYEVENTF_EXTENDEDKEY = 0x1
KEYEVENTF_KEYUP = 0x2

VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B,
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "printscreen": 0x2C, "capslock": 0x14,
    "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1, "stop": 0xB2,
    "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD,
}
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B,
             0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}


def _key(vk, up=False):
    flags = (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
    _user32.keybd_event(vk, 0, flags, 0)


def tap_key(vk):
    _key(vk)
    _key(vk, up=True)


def parse_shortcut(text):
    """'ctrl+shift+m' -> [0x11, 0x10, 0x4D]. Raises ValueError for unknown keys."""
    keys = []
    for part in text.lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in VK:
            keys.append(VK[part])
        elif len(part) == 1:
            res = _user32.VkKeyScanW(ord(part))
            if res == -1:
                raise ValueError(f"unknown key {part!r}")
            keys.append(res & 0xFF)
        else:
            raise ValueError(f"unknown key {part!r}")
    if not keys:
        raise ValueError("empty shortcut")
    return keys


def send_shortcut(text):
    keys = parse_shortcut(text)
    for vk in keys:
        _key(vk)
    for vk in reversed(keys):
        _key(vk, up=True)


def open_target(target):
    target = target.strip().strip('"')
    if not target:
        raise ValueError("nothing to open")
    os.startfile(target)
