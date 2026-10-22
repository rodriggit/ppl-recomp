"""BIF images: decode, survey and export.

bif.py ROM --survey              one line per BIF: frames and BFF2 header fields
bif.py ROM NAME.BIF [OUT_DIR]    write each frame of NAME.BIF as PNG

Container ("3FIB", read by func_8001F6B8; little-endian when the magic reads
"3FIB" byte by byte, big-endian when it reads "BIF3"):
  u32 magic, u32 flags, u8 rgba[4] (-> +0x94), u32 frame count
  flags & 0x10:  u8 rgba[4] (-> +0x98)
  flags & 0x08:  u32 length, char name[length]
  per frame:     u32 a, u32 b, u32 c, then one BFF2 image

Image ("BFF2", func_8001D524; big-endian when the magic reads "BFF2", swapped
when it reads "2FFB"):
  u32 magic, u32 flags, u32 dims, u32 height, u32 row_bytes
  flags & 0x200000: dims = width << 16 | width2, else dims = width
  flags & 0x000200: u32 length, char name[length]
  flags & 0x400000: u32 count, u32 size (then count words, size bytes)
  (flags & 0xF0) == 0x30: u32 colors, colors × u32 RGBA8888 palette
                          (flags & 0x400: index (flags >> 12) & 0xFF is clear)
  pixels: row_bytes × height bytes, run-length coded when flags & 0x800
  (func_8001D310).
"""
import struct
import sys
import zlib
from pathlib import Path

from filetable import read_archive


class Reader:
    def __init__(self, data, pos=0):
        self.data, self.pos = data, pos

    def take(self, n):
        b = self.data[self.pos:self.pos + n]
        if len(b) < n:
            raise ValueError(f"read past the end at {self.pos:#x}")
        self.pos += n
        return b

    def u32(self, little):
        return int.from_bytes(self.take(4), "little" if little else "big")


def decompress(r, size):
    """func_8001D310. Control byte c:
    0xxxxxxx: c + 1 literal bytes
    10xxxxxx: next byte repeated (c & 0x3F) + 2 times
    11nnnrrr: (n + 2) literal bytes, written (r + 2) times in a row
    Output stops at `size` bytes (a pattern repeat can be clipped)."""
    out = bytearray()
    while len(out) < size:
        c = r.take(1)[0]
        if c & 0x80 == 0:
            out += r.take(c + 1)
        elif c & 0x40 == 0:
            out += r.take(1) * ((c & 0x3F) + 2)
        else:
            pattern = r.take(((c >> 3) & 7) + 2)
            out += pattern * ((c & 7) + 2)
    return bytes(out[:size])


def compress(data):
    """Inverse of decompress(): runs of 2..65 equal bytes, patterns of 2..9
    bytes repeated 2..9 times when that saves more, literals otherwise."""
    out = bytearray()
    lit = bytearray()

    def flush():
        for i in range(0, len(lit), 128):
            part = lit[i:i + 128]
            out.append(len(part) - 1)
            out.extend(part)
        lit.clear()

    i, n = 0, len(data)
    while i < n:
        run = 1
        while i + run < n and run < 65 and data[i + run] == data[i]:
            run += 1
        best = (run - 2, None, None) if run >= 3 else (0, None, None)
        for plen in range(2, 10):
            reps = 1
            while (reps < 9 and i + (reps + 1) * plen <= n
                   and data[i + reps * plen:i + (reps + 1) * plen] == data[i:i + plen]):
                reps += 1
            if reps >= 2 and plen * reps - plen - 1 > best[0]:
                best = (plen * reps - plen - 1, plen, reps)
        if best[0] <= 0:
            lit.append(data[i])
            i += 1
            continue
        flush()
        if best[1] is None:
            out += bytes((0x80 | (run - 2), data[i]))
            i += run
        else:
            plen, reps = best[1], best[2]
            out.append(0xC0 | (plen - 2) << 3 | (reps - 2))
            out += data[i:i + plen]
            i += plen * reps
    flush()
    return bytes(out)


def write_image(img):
    """BFF2, big-endian, the layout read_image() reads."""
    flags = img["flags"]
    be = lambda v: v.to_bytes(4, "big")
    dims = img["width"] << 16 | img["width2"] if flags & 0x200000 else img["width"]
    out = bytearray(b"BFF2" + be(flags) + be(dims) + be(img["height"]) + be(img["row_bytes"]))
    if flags & 0x200:
        name = img["name"]
        out += be(len(name)) + name
    if flags & 0x400000:
        out += be(img["words"]) + be(img["size"])
    if flags & 0xF0 == 0x30:
        out += be(len(img["palette"])) + b"".join(img["palette"])
    if flags & 0x400000:
        out += img["word_data"] + img["pixels"]
    elif flags & 0x800:
        out += compress(img["pixels"])
    else:
        out += img["pixels"]
    return bytes(out)


def write_bif(bif):
    """3FIB, little-endian, the layout read_bif() reads."""
    le = lambda v: v.to_bytes(4, "little")
    flags = bif["flags"]
    out = bytearray(b"3FIB" + le(flags) + bif["rgba"] + le(len(bif["frames"])))
    if flags & 0x10:
        out += bif["rgba2"]
    if flags & 0x08:
        name = bif["name"]
        out += le(len(name)) + name
    for f in bif["frames"]:
        out += le(f["a"]) + le(f["b"]) + le(f["c"]) + write_image(f["image"])
    return bytes(out)


def read_image(r):
    magic = r.take(4)
    if magic == b"BFF2":
        little = False
    elif magic == b"2FFB":
        little = True
    else:
        raise ValueError(f"no BFF2 at {r.pos - 4:#x}: {magic!r}")
    img = {"flags": r.u32(little)}
    dims, img["height"], img["row_bytes"] = r.u32(little), r.u32(little), r.u32(little)
    flags = img["flags"]
    if flags & 0x200000:
        img["width"], img["width2"] = dims >> 16, dims & 0xFFFF
    else:
        img["width"] = img["width2"] = dims
    if flags & 0x200:
        img["name"] = r.take(r.u32(little))
    if flags & 0x400000:
        img["words"], img["size"] = r.u32(little), r.u32(little)
    img["palette"] = []
    if flags & 0xF0 == 0x30:
        n = r.u32(little)
        img["palette"] = [r.take(4) for _ in range(n)]
    if flags & 0x400000:
        img["word_data"] = r.take(img["words"] * 4)
        img["pixels"] = r.take(img["size"])
    elif flags & 0x800:
        img["pixels"] = decompress(r, img["row_bytes"] * img["height"])
    else:
        img["pixels"] = r.take(img["row_bytes"] * img["height"])
    return img


def read_bif(data):
    r = Reader(data)
    magic = r.take(4)
    if magic == b"3FIB":
        little = True
    elif magic == b"BIF3":
        little = False
    else:
        raise ValueError(f"not a BIF: {magic!r}")
    bif = {"flags": r.u32(little), "rgba": r.take(4)}
    count = r.u32(little)
    if bif["flags"] & 0x10:
        bif["rgba2"] = r.take(4)
    if bif["flags"] & 0x08:
        bif["name"] = r.take(r.u32(little))
    bif["frames"] = []
    for _ in range(count):
        a, b, c = r.u32(little), r.u32(little), r.u32(little)
        bif["frames"].append({"a": a, "b": b, "c": c, "image": read_image(r)})
    bif["rest"] = len(data) - r.pos
    return bif


def to_rgba(img):
    """RGBA8888 rows. flags & 0xF is the depth (2: 4 bits, 3: 8, 4: 16, 5: 32),
    (flags >> 4) & 0xF the kind (2: intensity, or IA when 16-bit; 3: palette;
    5: RGBA, 5551 when 16-bit). Rows are row_bytes apart (width2 pixels)."""
    depth, kind = img["flags"] & 0xF, (img["flags"] >> 4) & 0xF
    w, h, stride, px = img["width"], img["height"], img["row_bytes"], img["pixels"]
    clear = (img["flags"] >> 12) & 0xFF if img["flags"] & 0x400 else None
    pal = img["palette"]
    rows = []
    for y in range(h):
        row = bytearray()
        line = px[y * stride:(y + 1) * stride]
        for x in range(w):
            if depth == 2:
                v = line[x >> 1] >> (0 if x & 1 else 4) & 0xF
            elif depth == 3:
                v = line[x]
            elif depth == 4:
                v = int.from_bytes(line[2 * x:2 * x + 2], "big")
            elif depth == 5:
                v = int.from_bytes(line[4 * x:4 * x + 4], "big")
            else:
                raise ValueError(f"unknown depth {depth}")
            if kind == 3:
                c = pal[v] if v < len(pal) else b"\xff\0\xff\xff"
                row += c[:3] + (b"\0" if v == clear else c[3:4])
            elif depth == 5:
                row += v.to_bytes(4, "big")
            elif depth == 4 and kind == 5:
                row += bytes(((v >> 11 & 31) * 255 // 31, (v >> 6 & 31) * 255 // 31,
                              (v >> 1 & 31) * 255 // 31, 255 if v & 1 else 0))
            elif depth == 4:
                row += bytes((v >> 8,) * 3 + (v & 0xFF,))
            else:
                i = v * 17 if depth == 2 else v
                row += bytes((i, i, i, i))
        rows.append(bytes(row))
    return rows


def write_png(path, rows, w, h):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\0" + row for row in rows)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def main():
    rom = open(sys.argv[1], "rb").read()
    files = {name: rom[start:start + size] for name, start, size in read_archive(rom)}
    if "--survey" in sys.argv:
        for name, data in files.items():
            if not name.endswith(".BIF"):
                continue
            try:
                bif = read_bif(data)
            except ValueError as e:
                print(f"{name:16} ERROR {e}")
                continue
            for i, f in enumerate(bif["frames"]):
                img = f["image"]
                print(f"{name:16} {bif['flags']:04X} {i:2} ({f['a']},{f['b']},{f['c']}) "
                      f"flags={img['flags']:08X} {img['width']}x{img['height']} "
                      f"w2={img['width2']} row={img['row_bytes']} pal={len(img['palette'])}"
                      + (f" rest={bif['rest']}" if i == len(bif["frames"]) - 1 and bif["rest"] else ""))
        return
    name = sys.argv[2]
    out = Path(sys.argv[3] if len(sys.argv) > 3 else ".")
    out.mkdir(parents=True, exist_ok=True)
    bif = read_bif(files[name])
    for i, f in enumerate(bif["frames"]):
        img = f["image"]
        path = out / f"{name.rsplit('.', 1)[0]}_{i:02}.png"
        write_png(path, to_rgba(img), img["width"], img["height"])
        print(f"{path}  {img['width']}x{img['height']} flags={img['flags']:08X}")


if __name__ == "__main__":
    main()
