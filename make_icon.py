#!/usr/bin/env python3
"""Generate icon.ico from the radio design (Pillow redraw of icon.svg)."""
from PIL import Image, ImageDraw

S = 512  # master size, downscaled for the .ico


def rounded(d, box, r, **kw):
    d.rounded_rectangle(box, radius=r, **kw)


def build(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = size / 128  # scale factor from the 128px svg viewBox

    def xy(x, y):
        return (x * k, y * k)

    # radio body
    rounded(d, [xy(14, 38), xy(114, 108)], 12 * k, fill="#1d2129",
            outline="#39415a", width=max(1, round(3 * k)))
    # antenna + tip
    d.line([xy(98, 40), xy(118, 10)], fill="#e5484d", width=max(1, round(6 * k)))
    r = 5 * k
    ax, ay = xy(118, 10)
    d.ellipse([ax - r, ay - r, ax + r, ay + r], fill="#e5484d")
    # speaker grill
    gr = 3.5 * k
    for gx in (82, 96):
        for gy in (58, 72, 86):
            cx, cy = xy(gx, gy)
            d.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill="#39415a")
    # tuning dial
    rounded(d, [xy(26, 52), xy(70, 92)], 6 * k, fill="#14171c",
            outline="#4cc38a", width=max(1, round(2 * k)))
    # dial ticks
    for tx in (34, 48, 62):
        d.line([xy(tx, 86), xy(tx, 94)], fill="#8b93a1",
               width=max(1, round(2 * k)))
    # tuner needle
    d.line([xy(52, 54), xy(52, 90)], fill="#e5484d",
           width=max(1, round(3 * k)))
    # knobs
    for kx, ky in ((34, 98), (94, 98)):
        r = 6 * k
        cx, cy = xy(kx, ky)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#8b93a1")
    return img


if __name__ == "__main__":
    master = build(S)
    master.save("icon.ico",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                       (128, 128), (256, 256)])
    print("wrote icon.ico")
