"""List jal targets (function starts) in the main segment with their call counts.

jaltargets.py ROM [vram_lo vram_hi]
"""
import sys
from collections import Counter

MAIN_ROM, MAIN_VRAM, CODE_END = 0x1000, 0x80000400, 0xB05C0


def main():
    rom = open(sys.argv[1], "rb").read()
    lo, hi = (int(a, 16) for a in sys.argv[2:4]) if len(sys.argv) > 3 else (0, 0xFFFFFFFF)
    calls = Counter()
    for i in range(MAIN_ROM, CODE_END, 4):
        w = int.from_bytes(rom[i:i + 4], "big")
        if w >> 26 == 3:
            calls[0x80000000 | (w & 0x3FFFFFF) << 2] += 1
    targets = sorted(t for t in calls if lo <= t < hi)
    print(f"{len(calls)} jal targets in total, {len(targets)} in range")
    for t in targets:
        print(f"{t:08X} {calls[t]:4}")


if __name__ == "__main__":
    main()
