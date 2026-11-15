"""Small rounded on-screen notification (like the Windows volume flyout)."""

import tkinter as tk

import theme

_KEY = "#010203"  # colour made transparent, gives us rounded corners


def _rounded(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Toast:
    WIDTH, HEIGHT = 300, 64

    def __init__(self, root):
        self.root = root
        self.win = None
        self._hide_job = None
        self._fade_job = None

    def _ensure(self):
        if self.win is not None:
            return
        w = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(background=_KEY)
        try:
            w.attributes("-transparentcolor", _KEY)
            w.attributes("-toolwindow", True)  # keep it out of alt-tab
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(w, width=self.WIDTH, height=self.HEIGHT, background=_KEY, highlightthickness=0)
        self.canvas.pack()
        self.win = w

    def show(self, icon, text, sub="", colour=theme.ACCENT, seconds=1.6):
        self._ensure()
        c = self.canvas
        c.delete("all")
        _rounded(c, 1, 1, self.WIDTH - 1, self.HEIGHT - 1, 18, fill=theme.SURFACE, outline=theme.BORDER)
        c.create_oval(14, 12, 54, 52, fill=theme.SURFACE_2, outline="")
        c.create_text(34, 32, text=icon, fill=colour, font=(theme.ICON_FONT, 16))
        if sub:
            c.create_text(68, 23, anchor="w", text=text, fill=theme.TEXT, font=(theme.FONT, 12, "bold"))
            c.create_text(68, 43, anchor="w", text=sub, fill=theme.MUTED, font=(theme.FONT, 9),
                          width=self.WIDTH - 80)
        else:
            c.create_text(68, 32, anchor="w", text=text, fill=theme.TEXT, font=(theme.FONT, 12, "bold"))

        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x, y = (sw - self.WIDTH) // 2, sh - self.HEIGHT - 110
        self.win.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        for job in (self._hide_job, self._fade_job):
            if job:
                self.root.after_cancel(job)
        self.win.attributes("-alpha", 0.97)
        self.win.deiconify()
        self.win.lift()
        self._hide_job = self.root.after(int(seconds * 1000), lambda: self._fade(0.97))

    def _fade(self, alpha):
        alpha -= 0.12
        if alpha <= 0:
            self.win.withdraw()
            self._fade_job = None
            return
        self.win.attributes("-alpha", alpha)
        self._fade_job = self.root.after(30, lambda: self._fade(alpha))


class MutedBadge:
    """Always-on-top 'mic muted' pill. Drag it anywhere; on_moved(x, y) reports the new spot."""

    WIDTH, HEIGHT = 128, 42

    def __init__(self, root, on_moved=None):
        self.root = root
        self.on_moved = on_moved
        self.win = None
        self.visible = False
        self.pos = None  # (x, y) or None for the default corner
        self._drag = None

    def _ensure(self):
        if self.win is not None:
            return
        w = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(background=_KEY)
        try:
            w.attributes("-transparentcolor", _KEY)
            w.attributes("-toolwindow", True)
            w.attributes("-alpha", 0.94)
        except tk.TclError:
            pass
        c = tk.Canvas(w, width=self.WIDTH, height=self.HEIGHT, background=_KEY, highlightthickness=0,
                      cursor="fleur")
        c.pack()
        _rounded(c, 1, 1, self.WIDTH - 1, self.HEIGHT - 1, 20, fill="#2b1517", outline=theme.BAD, width=2)
        c.create_text(26, self.HEIGHT / 2, text=theme.Icon.MIC_OFF, fill=theme.BAD, font=(theme.ICON_FONT, 15))
        c.create_text(46, self.HEIGHT / 2, anchor="w", text="Muted", fill=theme.TEXT,
                      font=(theme.FONT, 12, "bold"))
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._move)
        c.bind("<ButtonRelease-1>", self._release)
        self.win = w

    def _default_pos(self):
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        return sw - self.WIDTH - 24, sh - self.HEIGHT - 72  # bottom right, above the taskbar

    def _place(self):
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x, y = self.pos or self._default_pos()
        x = max(0, min(sw - self.WIDTH, int(x)))
        y = max(0, min(sh - self.HEIGHT, int(y)))
        self.win.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def show(self):
        self._ensure()
        self._place()
        self.win.deiconify()
        self.win.lift()
        self.visible = True

    def hide(self):
        if self.win is not None:
            self.win.withdraw()
        self.visible = False

    def reset_position(self):
        self.pos = None
        if self.visible:
            self._place()

    def _press(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _move(self, e):
        if self._drag:
            self.win.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _release(self, _e):
        self._drag = None
        self.pos = (self.win.winfo_x(), self.win.winfo_y())
        if self.on_moved:
            self.on_moved(*self.pos)
