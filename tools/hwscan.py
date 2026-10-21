"""List functions that build uncached or hardware addresses (lui 0xA000-0xBFFF, 0xC000).

hwscan.py ROM vram_lo vram_hi [names.txt]
Floats loaded with lui (0xBF80 = -1.0f, ...) also match; a following mtc1 marks them.
"""
import sys

from funcinfo import MAIN_ROM, MAIN_VRAM, CODE_END, load_names, word


def main():
    rom = open(sys.argv[1], "rb").read()
    lo, hi = int(sys.argv[2], 16), int(sys.argv[3], 16)
    names = load_names(sys.argv[4] if len(sys.argv) > 4 else None)
    starts = set(names)
    for v in range(MAIN_VRAM, MAIN_VRAM + CODE_END - MAIN_ROM, 4):
        w = word(rom, v)
        if w >> 26 == 3:
            starts.add(0x80000000 | (w & 0x3FFFFFF) << 2)
    starts = sorted(s for s in starts if lo <= s < hi)
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else hi
        found = []
        for v in range(start, end, 4):
            w = word(rom, v)
            imm = w & 0xFFFF
            if w >> 26 == 0x0F and (0xA000 <= imm < 0xC000 or imm == 0xC000):
                rt = (w >> 16) & 0x1F
                is_float = any(word(rom, x) >> 21 == 0x204 and (word(rom, x) >> 16) & 0x1F == rt
                               for x in range(v + 4, min(v + 16, end), 4))
                if not is_float:
                    found.append(f"{imm:04X}@{v:08X}")
        if found:
            print(f"{start:08X} {names.get(start, ''):24} " + " ".join(found[:8]))


if __name__ == "__main__":
    main()
