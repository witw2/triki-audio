"""3D bottle-cap renderer and a small strip chart, drawn on plain tkinter canvases."""

import math
import tkinter as tk

from triki.orientation import cross, dot, mat_vec, q_to_matrix

import theme


def v_norm(v):
    n = math.sqrt(dot(v, v)) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)


def _rgb(hex_colour):
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def build_cap_mesh(flutes=21, sub=4):
    """Crown cork: domed top, fluted skirt flaring outward. z = up, top at z~0."""
    n = flutes * sub
    faces = []  # (points, kind)
    rings = [(1.00, 0.000, 0.00), (1.04, 0.025, -0.10), (1.13, 0.070, -0.27)]  # radius, flute amp, z
    ring_pts = []
    for r0, amp, z in rings:
        pts = []
        for i in range(n):
            th = 2 * math.pi * i / n
            r = r0 + amp * math.cos(flutes * th)
            pts.append((r * math.cos(th), r * math.sin(th), z))
        ring_pts.append(pts)
    centre = (0.0, 0.0, 0.06)
    inner = [(0.80 * math.cos(2 * math.pi * i / n), 0.80 * math.sin(2 * math.pi * i / n), 0.045)
             for i in range(n)]
    for i in range(n):
        j = (i + 1) % n
        faces.append(([centre, inner[i], inner[j]], "top"))
        faces.append(([inner[i], ring_pts[0][i], ring_pts[0][j], inner[j]], "top"))
        for k in range(len(rings) - 1):
            a, b = ring_pts[k], ring_pts[k + 1]
            faces.append(([a[i], b[i], b[j], a[j]], "skirt"))
    out = [(pts, kind, _poly_normal(pts)) for pts, kind in faces]
    # embossed speaker logo on the top face (also shows when the cap is twisted)
    out += [(pts, "icon", (0.0, 0.0, 1.0)) for pts in _speaker_icon(z=0.068)]
    # the same logo printed on the inside (seen when the cap lies on its back)
    out += [(pts, "icon_in", (0.0, 0.0, -1.0)) for pts in _speaker_icon(z=0.03)]
    return out


def _speaker_icon(z, dx=0.06, scale=1.0):
    """Flat speaker-with-sound-waves glyph in the cap's top plane."""
    def p(x, y):
        return ((x + dx) * scale, y * scale, z)
    polys = [
        [p(-0.44, -0.11), p(-0.30, -0.11), p(-0.30, 0.11), p(-0.44, 0.11)],          # magnet
        [p(-0.30, -0.11), p(-0.12, -0.27), p(-0.12, 0.27), p(-0.30, 0.11)],          # cone
    ]
    for r in (0.20, 0.34):                                                          # waves
        half = 0.035
        angles = [math.radians(-48 + 96 * i / 10) for i in range(11)]
        outer = [p(-0.08 + (r + half) * math.cos(a), (r + half) * math.sin(a)) for a in angles]
        inner = [p(-0.08 + (r - half) * math.cos(a), (r - half) * math.sin(a)) for a in reversed(angles)]
        polys.append(outer + inner)
    return polys


def _poly_normal(pts):
    nx = ny = nz = 0.0
    for i in range(len(pts)):
        x1, y1, z1 = pts[i]
        x2, y2, z2 = pts[(i + 1) % len(pts)]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return v_norm((nx, ny, nz))


class CapView(tk.Canvas):
    COLORS = {
        "top": (222, 178, 62),
        "skirt": (204, 160, 50),
        "icon": (146, 104, 26),   # darker gold, looks pressed into the metal
        "icon_in": (196, 154, 58),  # gold print on the grey liner
        "inside": (96, 98, 108),
    }

    def __init__(self, master, bg=theme.SURFACE, **kw):
        super().__init__(master, background=bg, highlightthickness=0, **kw)
        self.bg = _rgb(bg)
        self.faces = build_cap_mesh()
        self.az = math.radians(-60)
        self.el = math.radians(26)
        self.zoom = 1.0
        self.show_axes = False
        self._drag = None
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Double-Button-1>", lambda e: self.reset_camera())

    def reset_camera(self):
        self.az, self.el, self.zoom = math.radians(-60), math.radians(26), 1.0

    def _press(self, e):
        self._drag = (e.x, e.y)

    def _motion(self, e):
        if self._drag:
            dx, dy = e.x - self._drag[0], e.y - self._drag[1]
            self.az -= dx * 0.01
            self.el = max(-1.4, min(1.4, self.el + dy * 0.01))
            self._drag = (e.x, e.y)

    def _wheel(self, e):
        self.zoom = max(0.4, min(3.0, self.zoom * (1.1 if e.delta > 0 else 1 / 1.1)))

    def _camera(self):
        ca, sa, ce, se = math.cos(self.az), math.sin(self.az), math.cos(self.el), math.sin(self.el)
        back = (ce * ca, ce * sa, se)
        right = (-sa, ca, 0.0)
        return back, right, cross(back, right)

    def render(self, q, pressed=False, glow=None):
        """glow: optional hex colour for a soft halo (e.g. mic state)."""
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 20:
            return
        cx, cy = w / 2, h / 2 + h * 0.04
        s = min(w, h) * 0.27 * self.zoom
        back, right, up = self._camera()
        dist = 6.0

        def proj(p):
            d = dot(p, back)
            k = dist / (dist - d)
            return cx + s * dot(p, right) * k, cy - s * dot(p, up) * k, d

        # soft radial halo
        halo = _rgb(glow) if glow else (60, 66, 80)
        for i in range(10, 0, -1):
            r = s * (0.9 + i * 0.16)
            col = _mix(self.bg, halo, 0.13 * (1 - i / 10) ** 1.6)
            self.create_oval(cx - r, cy - r * 0.92, cx + r, cy + r * 0.92, fill=_hex(col), outline="")

        # floor: dotted grid + contact shadow
        fz = -0.95
        dot_col = _hex(_mix(self.bg, (255, 255, 255), 0.09))
        for i in range(-4, 5):
            for j in range(-4, 5):
                if i * i + j * j > 18:
                    continue
                x, y, _ = proj((i * 0.45, j * 0.45, fz))
                self.create_oval(x - 1.2, y - 1.2, x + 1.2, y + 1.2, fill=dot_col, outline="")
        for k, a in ((1.05, 0.12), (0.85, 0.2), (0.62, 0.28)):
            pts = []
            for i in range(32):
                th = 2 * math.pi * i / 32
                x, y, _ = proj((k * math.cos(th), k * math.sin(th), fz))
                pts += [x, y]
            self.create_polygon(pts, fill=_hex(_mix(self.bg, (0, 0, 0), a)), outline="", smooth=True)

        rot = q_to_matrix(q)
        light = v_norm(tuple(1.0 * b + 0.7 * u - 0.45 * r for b, r, u in zip(back, right, up)))
        polys = []
        for pts, kind, normal in self.faces:
            n = mat_vec(rot, normal)
            facing_amount = dot(n, back)
            facing = facing_amount > 0
            if kind in ("icon", "icon_in") and facing_amount < 0.12:
                continue  # hidden, or so edge-on that it would flicker
            pp = [proj(mat_vec(rot, p)) for p in pts]
            depth = sum(p[2] for p in pp) / len(pp)
            if facing:
                base = self.COLORS[kind]
            else:
                base, n = self.COLORS["inside"], (-n[0], -n[1], -n[2])
            if pressed and kind in ("top", "skirt", "icon", "icon_in") and facing:
                base = _mix(base, (255, 240, 200), 0.35)
            diff = max(0.0, dot(n, light))
            refl = v_norm(tuple(2 * dot(n, light) * nc - lc for nc, lc in zip(n, light)))
            spec = max(0.0, dot(refl, back)) ** 22 * 0.75
            shade = 0.24 + 0.76 * diff
            col = _hex(tuple(c * shade + 255 * spec for c in base))
            polys.append((depth, [c for p in pp for c in p[:2]], col, kind))
        # The logo is printed on the top face, so draw it right after the nearest top polygon
        # (per-polygon depth sorting would let top triangles cover parts of it).
        top_depths = [d for d, _, _, k in polys if k == "top"]
        if top_depths:
            logo_depth = max(top_depths) + 1e-6
            polys = [(logo_depth, c, col, k) if k in ("icon", "icon_in") else (d, c, col, k)
                     for d, c, col, k in polys]
        polys.sort(key=lambda t: t[0])
        for _, coords, col, _kind in polys:
            self.create_polygon(coords, fill=col, outline=col)

        if self.show_axes:
            c0 = proj((0, 0, 0))
            for vec, col in zip(((1.5, 0, 0), (0, 1.5, 0), (0, 0, 1.0)), theme.AXIS_COLORS):
                p = proj(mat_vec(rot, vec))
                self.create_line(c0[0], c0[1], p[0], p[1], fill=col, width=1, dash=(3, 3))

        self.create_text(14, h - 12, anchor="sw", fill=theme.FAINT, font=(theme.FONT, 9),
                         text="drag to orbit  ·  scroll to zoom  ·  double-click to reset")


class Scope(tk.Canvas):
    """Three-channel strip chart."""

    def __init__(self, master, title, rng, unit, bg=theme.SURFACE_2, height=110, **kw):
        super().__init__(master, background=bg, highlightthickness=0, height=height, **kw)
        self.title, self.rng, self.unit = title, rng, unit

    def render(self, series, latest):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20:
            return
        top = 24
        mid = top + (h - top) / 2
        amp = (h - top) / 2 - 6
        for frac in (-1, 0, 1):
            y = mid - frac * amp
            self.create_line(10, y, w - 10, y, fill=theme.BORDER, dash=() if frac == 0 else (2, 4))
        n = len(series)
        if n > 1:
            step = max(1, n // 150)  # cap the point count for speed
            pts = series[::step]
            m = len(pts)
            for ch in range(3):
                coords = []
                for i, v in enumerate(pts):
                    coords += [10 + (w - 20) * i / (m - 1), mid - max(-1.0, min(1.0, v[ch] / self.rng)) * amp]
                self.create_line(coords, fill=theme.AXIS_COLORS[ch], width=1.5)
        self.create_text(12, 6, anchor="nw", fill=theme.TEXT, font=(theme.FONT, 10, "bold"), text=self.title)
        self.create_text(12 + 7 * len(self.title) + 14, 8, anchor="nw", fill=theme.FAINT, font=(theme.FONT, 9),
                         text=f"±{self.rng:g} {self.unit}")
        for ch, name in enumerate("XYZ"):
            self.create_text(w - 12 - (2 - ch) * 72, 7, anchor="ne", fill=theme.AXIS_COLORS[ch],
                             font=("Consolas", 9), text=f"{name}{latest[ch]:+7.2f}")
