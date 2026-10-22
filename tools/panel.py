"""In-match panel strips: the trainer and Pokémon pictures beside the playfield.

panel.py ROM --dump OUT_DIR
    Each character's strip as PNG (in character id order).

panel.py ROM TEMPLATE_ID TRAINER_IMAGE OUT.BIN [--trainer-crop X,Y,W,H] [--head-crop X,Y,W,H]
         [--pokemon IMG1,IMG2,IMG3] [--theme RRGGBB,RRGGBB] [--key auto|RRGGBB] [--preview PNG]
    A new strip with the size of character TEMPLATE_ID's (files.ini: @ROMADDR = OUT.BIN,
    the address printed). Images on a flat background (--key) or with transparency.
    --trainer-crop: the part of TRAINER_IMAGE for the 108×192 trainer picture (default: the
    whole height, centered). --head-crop: the part shown in the corner of each Pokémon panel
    (default: around the top of the character). --theme: the dark and light colors of the
    speed-line background.

Strips (docs/johto.md): ROM addresses in the table at 0x800B6340 (16 entries, one per
character id, 15 = the final rival), raw RGBA5551, 112 pixels wide, columns 108-111 pure
green (0x07C1) padding. Rows 0-191 are the trainer; the rest are three Pokémon panels of
equal height ((height - 192) / 3), the trainer's head bottom-left and the Pokémon on the right.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from portrait import remove_key

W, VIS, TRAINER_H = 112, 108, 192
GREEN = 0x07C1
TABLE = 0x800B6340
OFF = 0x80000400 - 0x1000


def strips(rom):
    base = [int.from_bytes(rom[TABLE - OFF + 4 * i:TABLE - OFF + 4 * i + 4], "big") for i in range(16)]
    # the last strip ends where the next data starts: same size as the one before it
    ends = base[1:] + [base[15] + (base[15] - base[14])]
    return [(b, (e - b) // (2 * W)) for b, e in zip(base, ends)]


def decode(rom, addr, h):
    px = np.frombuffer(rom[addr:addr + h * W * 2], ">u2").reshape(h, W).astype(int)
    return np.dstack([(px >> 11 & 31) * 255 // 31, (px >> 6 & 31) * 255 // 31,
                      (px >> 1 & 31) * 255 // 31, (px & 1) * 255]).astype(np.uint8)


def encode(rgba):
    a = rgba.astype(int)
    px = (a[..., 0] * 31 // 255) << 11 | (a[..., 1] * 31 // 255) << 6 | (a[..., 2] * 31 // 255) << 1 | 1
    px[:, VIS:] = GREEN
    return px.astype(">u2").tobytes()


def speed_lines(w, h, dark, light, focus, seed):
    """Light streaks radiating from `focus`, brighter near it, as behind the originals."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(float)
    ang = np.arctan2(yy - focus[1], xx - focus[0])
    r = np.hypot(xx - focus[0], yy - focus[1])
    streak = np.zeros_like(ang)
    for k, amp in ((23, 1.0), (57, 0.6), (131, 0.4)):
        phases = rng.uniform(0, 2 * np.pi, 3)
        streak += amp * sum(np.sin(k * (j + 1) * ang / 2 + p) for j, p in enumerate(phases)) / 3
    streak = (streak - streak.min()) / (streak.max() - streak.min())
    glow = np.clip(1.2 - r / (1.4 * max(w, h)), 0, 1)
    t = np.clip(0.25 + 0.75 * glow * streak ** 1.5 + 0.35 * glow ** 3, 0, 1)[..., None]
    rgb = np.array(dark) * (1 - t) + np.array(light) * t
    return np.dstack([rgb, np.full((h, w), 255)]).clip(0, 255).astype(np.uint8)


def fit(im, box_w, box_h, crop):
    """crop (X,Y,W,H) of im, or the whole height centered, resized to box_w × box_h."""
    if crop:
        x, y, w, h = crop
    else:   # the whole image plus some room above, bottom edges aligned
        h = round(im.height * 1.2); w = round(h * box_w / box_h)
        x = (im.width - w) // 2; y = im.height - h
    return im.crop((x, y, x + w, y + h)).resize((box_w, box_h), Image.LANCZOS)


def head_box(im):
    """The top of the character (alpha), edge to edge, for the panels' corner."""
    a = np.asarray(im)[..., 3] > 128
    rows = np.nonzero(a.any(1))[0]
    top, height = rows.min(), rows.max() + 1 - rows.min()
    band = round(height * 0.6)   # hair and face of a bust
    cols = np.nonzero(a[top:top + band].any(0))[0]
    return int(cols.min()), int(top), int(cols.max() + 1 - cols.min()), band


def make(rom, template, trainer_path, out, trainer_crop=None, head_crop=None, pokemon=(),
         theme=("1C3C8C", "A8DCFF"), key="auto", preview=None):
    addr, h = strips(rom)[template]
    panel_h = (h - TRAINER_H) // 3
    assert TRAINER_H + 3 * panel_h == h, "template strip isn't 192 + 3 equal panels"
    dark, light = ([int(c[i:i + 2], 16) for i in (0, 2, 4)] for c in theme)
    trainer = remove_key(Image.open(trainer_path).convert("RGBA"), key)

    strip = Image.new("RGBA", (W, h))
    section = Image.fromarray(speed_lines(W, TRAINER_H, dark, light, (W, TRAINER_H * 0.3), 1))
    section.alpha_composite(fit(trainer, VIS, TRAINER_H, trainer_crop))
    strip.paste(section, (0, 0))

    hx, hy, hw, hh = head_crop or head_box(trainer)
    head_h = round(panel_h * 0.8)
    head = trainer.crop((hx, hy, hx + hw, hy + hh))
    head = head.resize((min(round(VIS * 0.55), round(head_h * hw / hh)), head_h), Image.LANCZOS)
    for i in range(3):
        panel = Image.fromarray(speed_lines(W, panel_h, dark, light, (W, panel_h * 0.4), 2 + i))
        if i < len(pokemon):
            p = remove_key(Image.open(pokemon[i]).convert("RGBA"), key)
            bbox = p.getchannel("A").getbbox()
            p = p.crop(bbox)
            scale = min((VIS * 0.7) / p.width, (panel_h * 1.0) / p.height)
            p = p.resize((max(1, round(p.width * scale)), max(1, round(p.height * scale))), Image.LANCZOS)
            panel.alpha_composite(p, (VIS - p.width, (panel_h - p.height) // 2))
        panel.alpha_composite(head, (0, panel_h - head.height))
        strip.paste(panel, (0, TRAINER_H + i * panel_h))

    rgba = np.asarray(strip)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(encode(rgba))
    if preview:
        Image.fromarray(decode(encode(rgba), 0, h)).save(preview)
    print(f"{out}: {W}x{h} (panels {panel_h}); files.ini line: @{addr:X} = {Path(out).name}")


def main():
    args = sys.argv[1:]
    opts = {}
    for o in ("--trainer-crop", "--head-crop", "--pokemon", "--theme", "--key", "--preview", "--dump"):
        if o in args:
            i = args.index(o); opts[o[2:]] = args[i + 1]; del args[i:i + 2]
    if not args:
        print(__doc__); return
    rom = open(args[0], "rb").read()
    if "dump" in opts:
        out = Path(opts["dump"]); out.mkdir(parents=True, exist_ok=True)
        for i, (addr, h) in enumerate(strips(rom)):
            Image.fromarray(decode(rom, addr, h)).save(out / f"strip_{i:02}.png")
            print(f"strip_{i:02}.png  ROM {addr:07X}  112x{h}")
        return
    box = lambda s: tuple(int(v) for v in s.split(",")) if s else None
    make(rom, int(args[1]), args[2], args[3], trainer_crop=box(opts.get("trainer-crop")),
         head_crop=box(opts.get("head-crop")),
         pokemon=opts["pokemon"].split(",") if "pokemon" in opts else (),
         theme=tuple(opts["theme"].split(",")) if "theme" in opts else ("1C3C8C", "A8DCFF"),
         key=opts.get("key", "auto"), preview=opts.get("preview"))


if __name__ == "__main__":
    main()
