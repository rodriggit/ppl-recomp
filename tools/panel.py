"""In-match panel strips: the trainer and Pokémon pictures beside the playfield.

panel.py ROM --dump OUT_DIR
    Every character's two strips as PNG (left_ID.png, right_ID.png).

panel.py ROM ID TRAINER_IMAGE OUT_PREFIX [--crop X,Y,W,H] [--pokemon IMG1,IMG2,IMG3]
         [--theme RRGGBB,RRGGBB] [--key auto|RRGGBB] [--preview PNG]
    New strips for both sides with the sizes of character ID's: OUT_PREFIX_L.BIN and
    OUT_PREFIX_R.BIN, and the files.ini lines that replace the originals. Images on a flat
    background (--key, default auto) or with transparency. --crop: the part of
    TRAINER_IMAGE for the 108×192 picture (default: the whole height and some room above,
    the figure shifted toward its left). --theme: the dark and light colors of the
    speed-line background. --preview: both strips and the match boxes they make.

Strips (docs/custom-stadium.md): raw RGBA5551, 112 pixels wide, columns 108-111 pure green (0x07C1),
one per character id in two tables (16 entries, 15 = the final rival):
- 0x800B6340, left side: the whole 192-row trainer picture, then three Pokémon panels;
- 0x800B6950, right side (mirrored): the picture's last 192 - P rows, then three panels.
A panel (P rows) is the top of the trainer picture with that Pokémon; in a match the box
shows the current Pokémon's panel over the rest of the picture.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from portrait import remove_key

W, VIS, PICTURE_H = 112, 108, 192
GREEN = 0x07C1
LEFT, RIGHT = 0x800B6340, 0x800B6950
OFF = 0x80000400 - 0x1000


def strips(rom, table):
    """[(ROM address, height, panel height)] for the 16 characters."""
    base = [int.from_bytes(rom[table - OFF + 4 * i:table - OFF + 4 * i + 4], "big") for i in range(16)]
    ends = base[1:] + [base[15] + (base[15] - base[14])]   # the last one: as big as the one before
    out = []
    for b, e in zip(base, ends):
        h = (e - b) // (2 * W)
        p = (h - PICTURE_H) // 3 if table == LEFT else (h - PICTURE_H) // 2
        out.append((b, h, p))
    return out


def decode(data, addr, h):
    px = np.frombuffer(data[addr:addr + h * W * 2], ">u2").reshape(h, W).astype(int)
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


def picture(trainer, crop, dark, light):
    """The 108×192 trainer picture (left-side orientation)."""
    if crop:
        x, y, w, h = crop
    else:   # whole image plus room above, bottom edges aligned, figure shifted left
        h = round(trainer.height * 1.2); w = round(h * VIS / PICTURE_H)
        x = (trainer.width - w) // 2 + round(w * 0.1); y = trainer.height - h
    pic = Image.fromarray(speed_lines(VIS, PICTURE_H, dark, light, (VIS, PICTURE_H * 0.3), 1))
    pic.alpha_composite(trainer.crop((x, y, x + w, y + h)).resize((VIS, PICTURE_H), Image.LANCZOS))
    return pic


def panel(pic, p_h, pokemon, key):
    """Top of the picture with the Pokémon on the right (left-side orientation)."""
    out = pic.crop((0, 0, VIS, p_h))
    if pokemon:
        p = remove_key(Image.open(pokemon).convert("RGBA"), key)
        p = p.crop(p.getchannel("A").getbbox())
        scale = min(VIS * 0.62 / p.width, p_h / p.height)
        p = p.resize((max(1, round(p.width * scale)), max(1, round(p.height * scale))), Image.LANCZOS)
        out.alpha_composite(p, (VIS - p.width, (p_h - p.height) // 2))
    return out


def build(pic, layout, h, p_h, pokemon, key):
    strip = Image.new("RGBA", (W, h))
    if layout == LEFT:
        strip.paste(pic, (0, 0)); first = PICTURE_H
    else:
        strip.paste(pic.crop((0, p_h, VIS, PICTURE_H)), (0, 0)); first = PICTURE_H - p_h
    for i in range(3):
        strip.paste(panel(pic, p_h, pokemon[i] if i < len(pokemon) else None, key), (0, first + i * p_h))
    if layout == RIGHT:   # mirrored: the trainer on the right, the Pokémon on the left
        strip = Image.fromarray(np.concatenate([np.asarray(strip)[:, VIS - 1::-1], np.asarray(strip)[:, VIS:]], 1))
    return np.asarray(strip)


def box(strip, layout, h, p_h, which):
    """What a match box shows with Pokémon `which`: its panel over the rest of the picture."""
    first = PICTURE_H if layout == LEFT else PICTURE_H - p_h
    rest = strip[p_h:PICTURE_H] if layout == LEFT else strip[0:PICTURE_H - p_h]
    return np.concatenate([strip[first + which * p_h:first + (which + 1) * p_h], rest], 0)


def make(rom, cid, trainer_path, prefix, crop=None, pokemon=(), theme=("1C3C8C", "A8DCFF"),
         key="auto", preview=None):
    dark, light = ([int(c[i:i + 2], 16) for i in (0, 2, 4)] for c in theme)
    trainer = remove_key(Image.open(trainer_path).convert("RGBA"), key)
    pic = picture(trainer, crop, dark, light)
    shots = []
    for layout, side in ((LEFT, "L"), (RIGHT, "R")):
        addr, h, p_h = strips(rom, layout)[cid]
        strip = build(pic, layout, h, p_h, pokemon, key)
        out = Path(f"{prefix}_{side}.BIN")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(encode(strip))
        print(f"@{addr:X} = {out.name}   ; {'left' if layout == LEFT else 'right'} strip, 112x{h}, panels {p_h}")
        back = decode(encode(strip), 0, h)
        shots += [back, np.zeros((h, 4, 4), np.uint8)]
        for k in range(3):
            shots += [box(back, layout, h, p_h, k), np.zeros((PICTURE_H, 4, 4), np.uint8)]
    if preview:
        H = max(s.shape[0] for s in shots)
        sheet = np.concatenate([np.pad(s, ((0, H - s.shape[0]), (0, 0), (0, 0))) for s in shots], 1)
        Image.fromarray(sheet).save(preview)


def main():
    args = sys.argv[1:]
    opts = {}
    for o in ("--crop", "--pokemon", "--theme", "--key", "--preview", "--dump"):
        if o in args:
            i = args.index(o); opts[o[2:]] = args[i + 1]; del args[i:i + 2]
    if not args:
        print(__doc__); return
    rom = open(args[0], "rb").read()
    if "dump" in opts:
        out = Path(opts["dump"]); out.mkdir(parents=True, exist_ok=True)
        for layout, side in ((LEFT, "left"), (RIGHT, "right")):
            for i, (addr, h, p_h) in enumerate(strips(rom, layout)):
                Image.fromarray(decode(rom, addr, h)).save(out / f"{side}_{i:02}.png")
                print(f"{side}_{i:02}.png  ROM {addr:07X}  112x{h}  panels {p_h}")
        return
    make(rom, int(args[1]), args[2], args[3],
         crop=tuple(int(v) for v in opts["crop"].split(",")) if "crop" in opts else None,
         pokemon=opts["pokemon"].split(",") if "pokemon" in opts else (),
         theme=tuple(opts["theme"].split(",")) if "theme" in opts else ("1C3C8C", "A8DCFF"),
         key=opts.get("key", "auto"), preview=opts.get("preview"))


if __name__ == "__main__":
    main()
