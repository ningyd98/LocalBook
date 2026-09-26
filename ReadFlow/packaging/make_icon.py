#!/usr/bin/env python3
"""
make_icon.py — draws the ReadFlow app icon and writes AppIcon.icns.

No Xcode required: the artwork is drawn with Pillow, the .iconset is produced
with sips, and .icns is packed with iconutil (both ship with macOS).

Design (v2 — v1 was rejected on sight):
  · macOS squircle on a transparent canvas, art inset so the system can draw its
    own shadow in the gutter.
  · The mark: an amber bookmark tab (reading) beside three white ribbons whose
    right ends flow into a wave (flow). Three shapes only, so it survives 16px.
  · Ribbons are filled polygons offset from a centreline, NOT a thick
    ImageDraw.line: PIL renders thick wavy polylines with hairline cracks.
  · Nothing overlaps; the bookmark keeps its own column clear of the text.

Usage:  python3 make_icon.py [outdir]
"""
import math, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFilter

S = 1024
MARGIN = 92
SIDE = S - MARGIN * 2
RADIUS = int(SIDE * 0.2237)

TOP = (64, 98, 240)
BOTTOM = (32, 178, 210)
ACCENT = (255, 176, 32)
INK = (255, 255, 255)


def squircle(size, n=5.0):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    a, pts = size / 2.0, []
    for i in range(1201):
        t = 2 * math.pi * i / 1200
        ct, st = math.cos(t), math.sin(t)
        pts.append((a + a * math.copysign(abs(ct) ** (2.0 / n), ct),
                    a + a * math.copysign(abs(st) ** (2.0 / n), st)))
    d.polygon(pts, fill=255)
    return m


def gradient(size):
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = (x * 0.35 + y * 0.65) / 63.0
            px[x, y] = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
    return small.resize((size, size), Image.BICUBIC)


def ribbon(d, pts, width, fill, taper=0.0):
    """Stroke with an optional taper: the far end narrows, so the line reads as a
    brush stroke that is still moving rather than a sausage that stopped."""
    up, dn = [], []
    radii = []
    n = len(pts) - 1
    for i, (x, y) in enumerate(pts):
        if i == 0:
            tx, ty = pts[1][0] - x, pts[1][1] - y
        elif i == len(pts) - 1:
            tx, ty = x - pts[-2][0], y - pts[-2][1]
        else:
            tx, ty = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        L = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / L, tx / L
        r = (width / 2.0) * (1.0 - taper * (i / n) ** 2.2)
        radii.append(r)
        up.append((x + nx * r, y + ny * r))
        dn.append((x - nx * r, y - ny * r))
    d.polygon(up + dn[::-1], fill=fill)
    for p, r in ((pts[0], radii[0]), (pts[-1], radii[-1])):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=fill)


def build_master():
    k = SIDE / 1000.0
    X = lambda v: MARGIN + v * k
    Y = lambda v: MARGIN + v * k
    mask_full = Image.new("L", (S, S), 0)
    mask_full.paste(squircle(SIDE), (MARGIN, MARGIN))

    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(gradient(S).crop((MARGIN, MARGIN, MARGIN + SIDE, MARGIN + SIDE)),
               (MARGIN, MARGIN), squircle(SIDE))

    gloss = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse(
        [MARGIN - SIDE * 0.35, MARGIN - SIDE * 0.80,
         MARGIN + SIDE * 1.35, MARGIN + SIDE * 0.60], fill=(255, 255, 255, 30))
    gloss = gloss.filter(ImageFilter.GaussianBlur(70))
    gloss.putalpha(Image.composite(gloss.getchannel("A"), Image.new("L", (S, S), 0), mask_full))
    icon = Image.alpha_composite(icon, gloss)

    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [MARGIN, MARGIN + 16, MARGIN + SIDE, MARGIN + SIDE + 16],
        radius=RADIUS, fill=(8, 20, 60, 80))
    icon = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(26)), icon)

    # --- amber bookmark (its own column), with a V notch cut at the foot
    art = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bx, by, bw, bh = 176, 316, 118, 336
    notch_h = 92
    ImageDraw.Draw(art).rounded_rectangle([X(bx), Y(by), X(bx + bw), Y(by + bh)],
                                          radius=int(34 * k), fill=ACCENT)
    ImageDraw.Draw(art).polygon(
        [(X(bx - 6), Y(by + bh - notch_h)), (X(bx + bw / 2), Y(by + bh - 10)),
         (X(bx + bw + 6), Y(by + bh - notch_h)),
         (X(bx + bw + 6), Y(by + bh + 60)), (X(bx - 6), Y(by + bh + 60))],
        fill=(0, 0, 0, 0))
    art.putalpha(Image.composite(art.getchannel("A"), Image.new("L", (S, S), 0), mask_full))
    icon = Image.alpha_composite(icon, art)

    # --- three flowing ribbons
    art2 = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(art2)
    for y, x0, length, stroke, amp, phase, drift in [
            (360, 372, 470, 76, 44, 0.00, -18),
            (512, 372, 424, 76, 52, 0.55, -26),
            (664, 372, 356, 76, 60, 1.10, -34)]:
        pts, steps = [], 120
        for i in range(steps + 1):
            t = i / steps
            wave = math.sin(t * math.pi * 1.05 + phase) * amp * (t ** 1.30)
            pts.append((X(x0 + length * t), Y(y + wave + drift * t)))
        ribbon(d2, pts, stroke * k, INK, taper=0.34)
    return Image.alpha_composite(icon, art2)


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "build")
    os.makedirs(outdir, exist_ok=True)
    master = build_master()
    master.save(os.path.join(outdir, "AppIcon-1024.png"))
    print("master:", os.path.join(outdir, "AppIcon-1024.png"))

    iconset = os.path.join(outdir, "AppIcon.iconset")
    subprocess.run(["rm", "-rf", iconset], check=True)
    os.makedirs(iconset, exist_ok=True)
    for size, scale in [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
                        (256, 1), (256, 2), (512, 1), (512, 2)]:
        master.resize((size * scale, size * scale), Image.LANCZOS).save(
            os.path.join(iconset, "icon_%dx%d%s.png" % (size, size, "@2x" if scale == 2 else "")))
    icns = os.path.join(outdir, "AppIcon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    print("icns  :", icns, os.path.getsize(icns), "bytes")

    strip = Image.new("RGBA", (700, 200), (242, 242, 245, 255))
    x = 28
    for px in (160, 96, 48, 32, 16):
        small = master.resize((px, px), Image.LANCZOS)
        strip.paste(small, (x, 100 - px // 2), small)
        x += px + 30
    strip.save(os.path.join(outdir, "preview-sizes.png"))
    print("sizes :", os.path.join(outdir, "preview-sizes.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
