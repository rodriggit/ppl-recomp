"""ROM survey: signatures, microcode/library strings and an entropy map.

survey.py ROM [block_kb]
"""
import math
import re
import sys
from collections import Counter

SIGNATURES = [b"Yay0", b"Yaz0", b"MIO0", b"PERS-SZP", b"LZ77", b"Vpk0", b"VPK0", b"Yay1"]
STRINGS = [
    rb"RSP [^\x00]{0,60}",
    rb"F3D[^\x00]{0,60}",
    rb"S2D[^\x00]{0,60}",
    rb"L3D[^\x00]{0,60}",
    rb"N64 [A-Za-z]+Tables[^\x00]{0,8}",
    rb"SRAM[^\x00]{0,20}",
    rb"EEPROM[^\x00]{0,20}",
    rb"FLASH[^\x00]{0,20}",
    rb"Copyright[^\x00]{0,60}",
    rb"libultra[^\x00]{0,40}",
    rb"NuSystem[^\x00]{0,40}",
    rb"[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}[^\x00]{0,30}",
]


def entropy(chunk):
    counts = Counter(chunk)
    n = len(chunk)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def main():
    rom = open(sys.argv[1], "rb").read()
    block = int(sys.argv[2]) * 1024 if len(sys.argv) > 2 else 0x10000

    print("== signatures")
    for sig in SIGNATURES:
        hits = [m.start() for m in re.finditer(re.escape(sig), rom)]
        if hits:
            shown = " ".join(f"{h:07X}" for h in hits[:12])
            print(f"{sig.decode():9} {len(hits):5}  {shown}{' ...' if len(hits) > 12 else ''}")

    print("== strings")
    seen = set()
    for pat in STRINGS:
        for m in re.finditer(pat, rom):
            text = m.group().decode("latin-1")
            if (m.start(), text) not in seen and text.isprintable():
                seen.add((m.start(), text))
                print(f"{m.start():07X}  {text}")

    print(f"== entropy per 0x{block:X} (bits/byte; ~8 = compressed or random, 0 = padding)")
    line = []
    for off in range(0, len(rom), block):
        chunk = rom[off:off + block]
        e = entropy(chunk)
        line.append(f"{off:07X}:{e:4.1f}")
        if len(line) == 8:
            print("  ".join(line))
            line = []
    if line:
        print("  ".join(line))


if __name__ == "__main__":
    main()
