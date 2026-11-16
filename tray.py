"""System-tray icon (notification area) with a status badge and a menu."""

import math

from PIL import Image, ImageDraw

import theme

try:
    import pystray
except ImportError:  # tray is optional
    pystray = None


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def cap_image(size=64, badge=None):
    """A gold crown cap, optionally with a coloured status dot (hex colour)."""
    s = 4  # supersample for smooth edges
    n = size * s
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = n / 2
    teeth = 21
    pts = []
    for i in range(teeth * 2):
        th = math.pi * i / teeth
        r = n * (0.48 if i % 2 == 0 else 0.43)
        pts.append((c + r * math.cos(th), c + r * math.sin(th)))
    d.polygon(pts, fill=_rgb("#b8862a"))
    d.ellipse([c - n * 0.40, c - n * 0.40, c + n * 0.40, c + n * 0.40], fill=_rgb(theme.ACCENT))
    d.ellipse([c - n * 0.30, c - n * 0.30, c + n * 0.30, c + n * 0.30], fill=_rgb("#efc860"))
    d.ellipse([c - n * 0.22, c - n * 0.30, c + n * 0.06, c - n * 0.12], fill=(255, 236, 170, 140))  # shine
    if badge:
        r = n * 0.2
        bx, by = n - r - n * 0.02, n - r - n * 0.02
        d.ellipse([bx - r - n * 0.04, by - r - n * 0.04, bx + r + n * 0.04, by + r + n * 0.04], fill=_rgb(theme.BG))
        d.ellipse([bx - r, by - r, bx + r, by + r], fill=_rgb(badge))
    return img.resize((size, size), Image.LANCZOS)


class Tray:
    """Thin wrapper over pystray. All callbacks are passed through `call_soon`
    so they run on the Tk thread."""

    def __init__(self, app):
        self.app = app
        self.icon = None
        self._badge = object()
        self._title = None

    @property
    def available(self):
        return pystray is not None

    def start(self):
        if not self.available:
            return
        a = self.app
        on_gui = lambda fn: (lambda icon, item: a.call_soon(fn))
        menu = pystray.Menu(
            pystray.MenuItem("Show Triki Audio", on_gui(a.show_window), default=True),
            pystray.MenuItem(lambda item: a.tray_status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Microphone muted", on_gui(a.toggle_mic_manual),
                             checked=lambda item: bool(a.mic_muted)),
            pystray.MenuItem(lambda item: "Disconnect" if a.want_connected else "Connect",
                             on_gui(a.toggle_connection)),
            pystray.MenuItem("Profile", pystray.Menu(lambda: (
                pystray.MenuItem(name, (lambda n: (lambda icon, item: a.call_soon(lambda: a.switch_profile(n))))(name),
                                 checked=(lambda n: (lambda item: a._current_profile == n))(name), radio=True)
                for name in a.profile_names()))),
            pystray.MenuItem("Start with Windows", on_gui(a.toggle_startup),
                             checked=lambda item: a.startup_enabled),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_gui(a.quit_app)),
        )
        self.icon = pystray.Icon("TrikiAudio", cap_image(64), "Triki Audio", menu)
        self.icon.run_detached()

    def update(self, badge, title):
        if not self.icon:
            return
        if badge != self._badge:
            self._badge = badge
            self.icon.icon = cap_image(64, badge)
        if title != self._title:
            self._title = title
            self.icon.title = title[:127]
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def stop(self):
        if self.icon:
            self.icon.stop()
            self.icon = None
