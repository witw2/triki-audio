"""Marble maze: tilt the Triki to roll a marble to the gold cap. Arrow keys work too."""

import math
import time
import tkinter as tk

import customtkinter as ctk

import theme

CELL = 40
# '#' wall  'O' hole (back to start)  'S' start  'G' goal  '.' floor
LEVELS = [
    ("Warm-up", [
        "################",
        "#S.............#",
        "#..............#",
        "#######.########",
        "#..............#",
        "#..............#",
        "#.########.#####",
        "#..............#",
        "#.............G#",
        "################",
    ]),
    ("Holes", [
        "################",
        "#S....#........#",
        "#.....#..O.....#",
        "#..O..#.....O..#",
        "#.....#..#######",
        "#.....O........#",
        "####.......O...#",
        "#......O...#...#",
        "#..O.......#..G#",
        "################",
    ]),
    ("Snake", [
        "################",
        "#S..........O..#",
        "##########.....#",
        "#...........O..#",
        "#....###########",
        "#..............#",
        "##########..O..#",
        "#G...O.........#",
        "#.......O......#",
        "################",
    ]),
]

ACCEL = 900.0        # px/s^2 at a 45 degree tilt
FRICTION = 1.6       # per second
BOUNCE = 0.35
RADIUS = 13


class MarbleGame(ctk.CTkToplevel):
    def __init__(self, app, on_close=None):
        super().__init__(app, fg_color=theme.BG)
        self.app = app
        self.on_close = on_close
        self.title("Triki Marble")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.after(250, lambda: self.attributes("-topmost", False))
        self.protocol("WM_DELETE_WINDOW", self.close)
        icon = getattr(app, "icon_path", None)
        if icon:   # CTkToplevel sets its own icon after ~200 ms, so set ours after that
            self.after(300, lambda: self.iconbitmap(icon))

        F = app.fonts
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 6))
        self.title_lbl = ctk.CTkLabel(head, text="", font=F["title"], text_color=theme.TEXT)
        self.title_lbl.pack(side="left")
        self.time_lbl = ctk.CTkLabel(head, text="", font=F["h2"], text_color=theme.ACCENT)
        self.time_lbl.pack(side="right")
        self.canvas = tk.Canvas(self, width=16 * CELL, height=10 * CELL, background=theme.SURFACE,
                                highlightthickness=0)
        self.canvas.pack(padx=16)
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=16, pady=(8, 14))
        self.hint = ctk.CTkLabel(foot, text="", font=F["small"], text_color=theme.MUTED)
        self.hint.pack(side="left")
        app._button(foot, "Level the board", self._level, width=130).pack(side="right")
        app._button(foot, "Restart", lambda: self.load(self.level), width=90).pack(side="right", padx=8)

        self.keys = set()
        self.bind("<KeyPress>", lambda e: self.keys.add(e.keysym))
        self.bind("<KeyRelease>", lambda e: self.keys.discard(e.keysym))
        self.level = 0
        self.load(0)
        self.after(50, self.focus_force)
        self._last = time.perf_counter()
        self._loop()

    # ------------------------------------------------------------ level
    def load(self, index):
        self.level = index % len(LEVELS)
        name, rows = LEVELS[self.level]
        c = self.canvas
        c.delete("all")
        self.walls, self.holes = [], []
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                x0, y0 = x * CELL, y * CELL
                if ch == "#":
                    self.walls.append((x0, y0, x0 + CELL, y0 + CELL))
                    c.create_rectangle(x0, y0, x0 + CELL, y0 + CELL, fill=theme.SURFACE_3, outline=theme.BORDER)
                elif ch == "O":
                    self.holes.append((x0 + CELL / 2, y0 + CELL / 2))
                    c.create_oval(x0 + 6, y0 + 6, x0 + CELL - 6, y0 + CELL - 6, fill="#05060a", outline=theme.BORDER)
                elif ch == "S":
                    self.start = (x0 + CELL / 2, y0 + CELL / 2)
                elif ch == "G":
                    self.goal = (x0 + CELL / 2, y0 + CELL / 2)
                    gx, gy = self.goal
                    for r, col in ((17, "#b8862a"), (14, theme.ACCENT), (9, "#efc860")):
                        c.create_oval(gx - r, gy - r, gx + r, gy + r, fill=col, outline="")
        self.marble = c.create_oval(0, 0, 0, 0, fill="#d9dde6", outline="#ffffff", width=1)
        self.shine = c.create_oval(0, 0, 0, 0, fill="#ffffff", outline="")
        self.title_lbl.configure(text=f"Level {self.level + 1}: {name}")
        best = self.app.game_best.get(str(self.level))
        self.best_txt = f"best {best:.1f} s" if best else "no best time yet"
        self._reset_marble()
        self.started = time.perf_counter()
        self.finished = None

    def _reset_marble(self):
        self.x, self.y = self.start
        self.vx = self.vy = 0.0

    def _level(self):
        if self.app.cap:
            self.app.zero()

    # ------------------------------------------------------------ physics
    def _input(self):
        """Tilt in degrees along screen x / y from the cap (or the arrow keys)."""
        tx = ty = 0.0
        cap = self.app.cap
        if cap is not None and not cap.state.calibrating:
            st = cap.state
            tx, ty = st.lean_x, -st.lean_y      # +X edge down -> right; +Y edge (forward) down -> up
            self.hint.configure(text=f"Tilt the cap to roll the marble  ·  {self.best_txt}")
        else:
            self.hint.configure(text=f"No Triki connected - use the arrow keys  ·  {self.best_txt}")
        k = 30.0
        tx += k * (("Right" in self.keys) - ("Left" in self.keys))
        ty += k * (("Down" in self.keys) - ("Up" in self.keys))
        return tx, ty

    def _loop(self):
        if not self.winfo_exists():
            return
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        if self.finished is None:
            tx, ty = self._input()
            self.vx += ACCEL * math.sin(math.radians(max(-60, min(60, tx)))) / math.sin(math.radians(45)) * dt
            self.vy += ACCEL * math.sin(math.radians(max(-60, min(60, ty)))) / math.sin(math.radians(45)) * dt
            damp = max(0.0, 1.0 - FRICTION * dt)
            self.vx *= damp
            self.vy *= damp
            self._move(self.vx * dt, 0)
            self._move(0, self.vy * dt)
            self._check()
        self._draw(now)
        self.after(16, self._loop)

    def _move(self, dx, dy):
        self.x += dx
        self.y += dy
        for x0, y0, x1, y1 in self.walls:
            cx, cy = max(x0, min(self.x, x1)), max(y0, min(self.y, y1))
            ddx, ddy = self.x - cx, self.y - cy
            if ddx * ddx + ddy * ddy < RADIUS * RADIUS:
                if dx:
                    self.x = (x0 - RADIUS) if dx > 0 else (x1 + RADIUS)
                    self.vx = -self.vx * BOUNCE
                if dy:
                    self.y = (y0 - RADIUS) if dy > 0 else (y1 + RADIUS)
                    self.vy = -self.vy * BOUNCE

    def _check(self):
        for hx, hy in self.holes:
            if math.hypot(self.x - hx, self.y - hy) < CELL / 2 - 6:
                self._reset_marble()
                self.app.toast.show("", "Oops - back to the start", seconds=0.8) if self.app.notify.get() else None
                return
        gx, gy = self.goal
        if math.hypot(self.x - gx, self.y - gy) < 14:
            self.finished = time.perf_counter() - self.started
            key = str(self.level)
            best = self.app.game_best.get(key)
            record = best is None or self.finished < best
            if record:
                self.app.game_best[key] = round(self.finished, 2)
                self.app.save_config()
            self.app.toast.show("", f"Level {self.level + 1} done: {self.finished:.1f} s",
                                "New best time!" if record else f"Best: {best:.1f} s", theme.GOOD, 2.0)
            self.after(1800, lambda: self.load(self.level + 1))

    def _draw(self, now):
        r = RADIUS
        self.canvas.coords(self.marble, self.x - r, self.y - r, self.x + r, self.y + r)
        self.canvas.coords(self.shine, self.x - r * 0.55, self.y - r * 0.6, self.x - r * 0.1, self.y - r * 0.15)
        elapsed = self.finished if self.finished is not None else now - self.started
        self.time_lbl.configure(text=f"{elapsed:5.1f} s")

    def close(self):
        if self.on_close:
            self.on_close()
        self.destroy()
