"""Stadium portraits (CNAME.BIF): build a kit from the ROM, then make new portraits.

portrait.py kit ROM KIT_DIR
    Extracts what new portraits need from the 15 Kanto portraits into KIT_DIR (keep it
    under mods/: it holds Nintendo pixels): the purple background of each frame (a radial
    gradient fitted to the pixels the portraits share) and the name lettering, cut into
    glyphs, plus F, J, P and W built from E, U, R and M.

portrait.py make ROM KIT_DIR IMAGE "NAME" OUT.BIF [--crop X,Y,W,H] [--key auto|RRGGBB]
                 [--template CBROCK.BIF] [--preview DIR]
    IMAGE: the character, with transparency (PNG) or on a flat background removed with
    --key ("auto" takes the top-left pixel's color). --crop is the part of IMAGE that
    fills the 99×129 picture (default: the whole image, cropped to that aspect from the
    top). NAME: the lettering on top; "|" splits two lines ("TEAM|ROCKET").
    Writes frame 0 (character facing right, as given) and frame 1 (character mirrored,
    name not), each quantized to 255 colors plus the transparent index, into a copy of
    the template's BIF structure.

Portrait layout (all 15 originals): 104×129 CI8, columns 99-103 transparent padding,
radial background centered near (54, 100), name centered at x ≈ 53.5 with its
baseline at y ≈ 21 (two lines: 17 and 32), 1 pixel between letters.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from bif import read_bif, to_rgba, write_bif
from filetable import read_archive

W, H, VIS = 104, 129, 99
NAMES = {"CASH": "ASH", "CGARY": "GARY", "CBROCK": "BROCK", "CMISTY": "MISTY",
         "CLTSURGE": "LT.SURGE", "CERIKA": "ERIKA", "CKOGA": "KOGA", "CSABRINA": "SABRINA",
         "CBLAINE": "BLAINE", "CTRACEY": "TRACEY", "CTR": "TEAMROCKET",
         "CGIOVANNI": "GIOVANNI", "CRITCHIE": "RITCHIE", "CLORELEI": "LORELEI", "CBRUNO": "BRUNO"}
# glyph sources in order of preference (cleanest lettering first)
PREF = ["CSABRINA", "CGIOVANNI", "CTRACEY", "CRITCHIE", "CTR", "CLORELEI", "CBRUNO",
        "CKOGA", "CERIKA", "CGARY", "CASH"]


def rom_files(rom_path):
    rom = open(rom_path, "rb").read()
    return {name: rom[s:s + z] for name, s, z in read_archive(rom)}


def frames_rgba(data):
    out = []
    for f in read_bif(data)["frames"]:
        img = f["image"]
        out.append(np.frombuffer(b"".join(to_rgba(img)), np.uint8).reshape(img["height"], img["width"], 4))
    return out


# --- kit ---------------------------------------------------------------------------

def fit_background(stack):
    """stack: n × H × W × 4 portraits of one frame. Returns H × W × 3 floats."""
    rgb = stack[..., :3].astype(np.int32)
    bg = np.zeros((H, W, 3)); known = np.zeros((H, W), bool)
    for y in range(H):
        for x in range(VIS):
            v = rgb[:, y, x]
            d = np.abs(v[:, None, :] - v[None, :, :]).max(-1)
            close = (d <= 6).sum(1)
            i = close.argmax()
            if close[i] >= 5:  # most portraits show background here
                bg[y, x] = v[d[i] <= 6].mean(0); known[y, x] = True
    purple = known & (bg[..., 2] > bg[..., 0] + 15) & (bg[..., 2] > bg[..., 1] + 30)
    ys, xs = np.nonzero(purple)
    best = None
    for cx in range(30, 76, 2):
        for cy in range(40, 120, 2):
            r = np.hypot(xs - cx, ys - cy).round().astype(int)
            cnt = np.bincount(r)
            prof = np.stack([np.bincount(r, bg[ys, xs, c]) for c in range(3)], 1) / np.maximum(cnt, 1)[:, None]
            err = np.abs(prof[r] - bg[ys, xs]).max(-1).mean()
            if best is None or err < best[0]:
                best = (err, cx, cy, prof, cnt)
    err, cx, cy, prof, cnt = best
    yy, xx = np.mgrid[0:H, 0:W]
    R = np.hypot(xx - cx, yy - cy).round().astype(int)
    full = np.zeros((R.max() + 1, 3)); last = prof[np.nonzero(cnt)[0][0]]
    for i in range(len(full)):
        if i < len(cnt) and cnt[i]:
            last = prof[i]
        full[i] = last
    print(f"  background: center ({cx},{cy}), mean error {err:.2f}")
    return full[R]


def components(mask):
    lab = np.zeros(mask.shape, int); n = 0
    for y, x in zip(*np.nonzero(mask)):
        if lab[y, x]:
            continue
        n += 1; st = [(y, x)]; lab[y, x] = n
        while st:
            cy, cx = st.pop()
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = n; st.append((ny, nx))
    return lab, n


def is_fill(a):
    a = a.astype(int)
    return (a[..., 0] > 120) & (a[..., 0] - a[..., 2] > 70)


def cut_glyphs(im, model, name):
    """Glyphs of one portrait's name, or None when the letters can't be separated."""
    band = im[:36, :, :3].astype(float)
    fill = is_fill(band); fill[:, VIS:] = False
    for thr in (160, 175, 190, 205, 220):  # strong yellow first separates touching letters
        lab, n = components(fill & (band[..., 0] > thr))
        big = [i for i in range(1, n + 1) if (lab == i).sum() >= 6]
        if len(big) >= len(name):
            break
    cy, cx = np.nonzero(np.isin(lab, big)); cl = lab[cy, cx]
    fy, fx = np.nonzero(fill)
    d = (fy[:, None] - cy[None]) ** 2 + (fx[:, None] - cx[None]) ** 2
    lab = np.zeros_like(lab); near = d.min(1) <= 4
    lab[fy[near], fx[near]] = cl[d.argmin(1)][near]
    comps = []
    for i in big:
        ys, xs = np.nonzero(lab == i)
        if len(ys) >= 6:
            comps.append((ys.min(), ys.max() + 1, xs.min(), xs.max() + 1, i))
    two_lines = name == "TEAMROCKET"
    comps.sort(key=lambda c: (two_lines and c[0] > 18, c[2]))
    if len(comps) != len(name) or not all(c[1] - c[0] >= 9 and c[3] - c[2] <= 13 for c in comps):
        return None
    diff = np.abs(band - model[:36]).max(-1)
    out = []
    for ch, (y0, y1, x0, x1, i) in zip(name, comps):
        own = lab == i
        ring = own.copy()
        for _ in range(2):  # the outline around the fill
            g = ring.copy()
            g[1:] |= ring[:-1]; g[:-1] |= ring[1:]; g[:, 1:] |= ring[:, :-1]; g[:, :-1] |= ring[:, 1:]
            ring = g
        m = ring & (diff > 10) & ~(fill & ~own)
        ys, xs = np.nonzero(m)
        Y0, X0 = ys.min(), xs.min()
        g = np.zeros((ys.max() + 1 - Y0, xs.max() + 1 - X0, 4), np.uint8)
        g[..., :3] = band[Y0:ys.max() + 1, X0:xs.max() + 1]
        g[..., 3] = m[Y0:ys.max() + 1, X0:xs.max() + 1] * 255
        fys = np.nonzero(own.any(1))[0]
        out.append((ch, g, int(fys.max() + 1 - Y0)))  # baseline = bottom of the fill
    return out


def derived_letters(font):
    """F, J, P, W from E, U, R, M (none of them appears in the Kanto names)."""
    E, I, R, M, U = (font[c][0] for c in "EIRMU")
    # F: E's rows down to the lower arm, then I's stem (baseline-aligned) at E's stem
    fe = is_fill(E); widths = fe.sum(1)
    wide = np.nonzero(widths > widths.min() + 2)[0]
    cut = wide[wide > E.shape[0] // 2].min() - 1
    stem_x = np.nonzero(fe[cut - 1])[0].min()
    ix = np.nonzero(is_fill(I).any(0))[0].min()
    F = np.zeros_like(E); F[:cut] = E[:cut]
    off = font["I"][1] - font["E"][1]
    for r in range(cut, E.shape[0]):
        if 0 <= r + off < I.shape[0]:
            for xi in range(I.shape[1]):
                xo = stem_x - ix + xi
                if 0 <= xo < F.shape[1] and I[r + off, xi, 3]:
                    F[r, xo] = I[r + off, xi]
    font["F"] = (F, font["E"][1])
    # P: R without its leg. The bowl closes where the right side's fill stops being one
    # run joined to the stem; below it keep only the stem and close it with outline.
    fr = is_fill(R)
    stem_r = np.nonzero(fr[fr.shape[0] // 2])[0]
    stem_right = stem_r[np.nonzero(np.diff(stem_r) > 1)[0][0]] if (np.diff(stem_r) > 1).any() else stem_r.max()
    leg_rows = [r for r in range(R.shape[0] // 2, R.shape[0])
                if fr[r, stem_right + 2:].any() and not fr[r, stem_right + 1]]
    P = R.copy()
    if leg_rows:
        outline = R[R.shape[0] // 2, np.nonzero(R[R.shape[0] // 2, :, 3])[0].min()].copy()
        for r in range(min(leg_rows), R.shape[0]):
            P[r, stem_right + 3:] = 0
            if P[r, stem_right + 2, 3]:
                P[r, stem_right + 2] = outline
    font["P"] = (P, font["R"][1])
    # W: M upside down
    top = np.nonzero(is_fill(M).any(1))[0].min()
    font["W"] = (M[::-1].copy(), M.shape[0] - top)
    # J: U without the upper part of its left stroke
    J = U.copy(); cols = np.nonzero(is_fill(U).any(0))[0]
    J[:font["U"][1] - 5, :(cols.min() + cols.max()) // 2] = 0
    font["J"] = (J, font["U"][1])


def build_kit(rom_path, kit):
    files = rom_files(rom_path)
    portraits = {n: frames_rgba(files[n + ".BIF"]) for n in NAMES}
    kit = Path(kit); kit.mkdir(parents=True, exist_ok=True)
    models = []
    for fi in range(2):
        print(f"frame {fi}:")
        models.append(fit_background(np.stack([p[fi] for p in portraits.values()])))
    candidates = {}
    for n, name in NAMES.items():
        cut = cut_glyphs(portraits[n][0], models[0], name)
        print(f"  {n:10} {'ok' if cut else 'letters not separable, skipped'}")
        for ch, g, base in cut or []:
            candidates.setdefault(ch, []).append((PREF.index(n) if n in PREF else 99, g, base))
    font = {ch: min(v, key=lambda t: t[0])[1:] for ch, v in candidates.items()}
    derived_letters(font)
    np.savez_compressed(kit / "portrait_kit.npz", bg0=models[0], bg1=models[1],
                        **{f"glyph_{c}": g for c, (g, b) in font.items()},
                        bases=np.array([[ord(c), b] for c, (g, b) in font.items()]))
    sheet(font).save(kit / "font.png")
    print(f"letters: {''.join(sorted(font))} -> {kit / 'portrait_kit.npz'}")


def load_kit(kit):
    z = np.load(Path(kit) / "portrait_kit.npz")
    bases = {chr(c): int(b) for c, b in z["bases"]}
    font = {c: (z[f"glyph_{c}"], bases[c]) for c in bases}
    return [z["bg0"], z["bg1"]], font


def sheet(font):
    order = sorted(font)
    base = max(b for g, b in font.values()); below = max(g.shape[0] - b for g, b in font.values())
    w = sum(font[c][0].shape[1] + 3 for c in order)
    im = Image.new("RGBA", (w, base + below), (60, 60, 60, 255)); x = 0
    for c in order:
        g, b = font[c]; im.alpha_composite(Image.fromarray(g), (x, base - b)); x += g.shape[1] + 3
    return im.resize((im.width * 6, im.height * 6), Image.NEAREST)


# --- make --------------------------------------------------------------------------

def draw_name(canvas, font, text):
    lines = text.upper().split("|")
    baselines = [21] if len(lines) == 1 else [17, 32]
    for line, base in zip(lines, baselines):
        glyphs = []
        for ch in line:
            if ch == " ":
                glyphs.append(None)
            elif ch in font:
                glyphs.append(font[ch])
            else:
                raise SystemExit(f"no glyph for {ch!r} (have {''.join(sorted(font))})")
        # letters overlap by their outline: advance = glyph width - 3 + 1 px gap
        adv = [5 if g is None else g[0].shape[1] - 2 for g in glyphs]
        x = round(53.5 - (sum(adv) + 2) / 2)
        for g, a in zip(glyphs, adv):
            if g is not None:
                img, b = g
                canvas.alpha_composite(Image.fromarray(img), (x, base - b))
            x += a


def load_character(path, key, crop):
    im = Image.open(path).convert("RGBA")
    if key:
        a = np.asarray(im).copy()
        col = a[0, 0, :3].astype(int) if key == "auto" else np.array([int(key[i:i + 2], 16) for i in (0, 2, 4)])
        dist = np.abs(a[..., :3].astype(int) - col).max(-1)
        a[..., 3] = np.where(dist < 24, 0, np.where(dist < 48, (dist - 24) * 255 // 24, a[..., 3]))
        im = Image.fromarray(a)
    if crop:
        x, y, w, h = crop
    else:  # whole width, picture aspect, from the top
        w = im.width; h = min(im.height, round(w * H / VIS)); x = y = 0
        if h == im.height:
            w = round(h * VIS / H); x = (im.width - w) // 2
    return im.crop((x, y, x + w, y + h)).resize((VIS, H), Image.LANCZOS)


def quantize(rgba):
    """255 colors for the visible pixels + transparent index 0 (pure green, as the game's)."""
    rgb = Image.fromarray(rgba[..., :3])
    q = rgb.quantize(255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    idx = np.asarray(q, np.uint8).astype(np.int32) + 1
    pal = q.getpalette()[:255 * 3]
    idx[rgba[..., 3] < 128] = 0
    palette = [b"\x00\xff\x00\xff"] + [bytes(pal[i * 3:i * 3 + 3]) + b"\xff" for i in range(len(pal) // 3)]
    palette += [b"\0\0\0\xff"] * (256 - len(palette))
    return idx.astype(np.uint8), palette


def make(rom_path, kit, image, name, out, crop=None, key=None, template="CBROCK.BIF", preview=None):
    bgs, font = load_kit(kit)
    char = load_character(image, key, crop)
    files = rom_files(rom_path)
    bif = read_bif(files[template])
    for fi, frame in enumerate(bif["frames"]):
        layer = char if fi == 0 else char.transpose(Image.FLIP_LEFT_RIGHT)
        canvas = Image.fromarray(np.dstack([bgs[fi].clip(0, 255).astype(np.uint8),
                                            np.full((H, W), 255, np.uint8)]))
        canvas.alpha_composite(layer, (0, 0))
        draw_name(canvas, font, name)
        rgba = np.asarray(canvas).copy(); rgba[:, VIS:, 3] = 0
        idx, palette = quantize(rgba)
        img = frame["image"]
        assert (img["width"], img["height"], img["row_bytes"]) == (W, H, W), "template is not a portrait"
        img["pixels"] = idx.tobytes(); img["palette"] = palette
        img["flags"] = (img["flags"] & ~0xFF000) | 0x400  # transparent index 0
        if preview:
            Path(preview).mkdir(parents=True, exist_ok=True)
            p = np.frombuffer(b"".join(to_rgba(img)), np.uint8).reshape(H, W, 4)
            Image.fromarray(p).save(Path(preview) / f"{Path(out).stem}_{fi:02}.png")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(write_bif(bif))
    print(f"{out}: {len(bif['frames'])} frames")


def main():
    args = sys.argv[1:]
    opts = {}
    for o in ("--crop", "--key", "--template", "--preview"):
        if o in args:
            i = args.index(o); opts[o[2:]] = args[i + 1]; del args[i:i + 2]
    if args[:1] == ["kit"]:
        build_kit(args[1], args[2])
    elif args[:1] == ["make"]:
        crop = tuple(int(v) for v in opts["crop"].split(",")) if "crop" in opts else None
        make(args[1], args[2], args[3], args[4], args[5], crop=crop, key=opts.get("key"),
             template=opts.get("template", "CBROCK.BIF"), preview=opts.get("preview"))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
