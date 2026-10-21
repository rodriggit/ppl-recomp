"""Find code-looking regions: count `jr ra` and `addiu sp, sp, -N` per block.

codescan.py ROM [block_kb]
Only blocks with at least a few hits are printed, with the most common jal
target segment (upper 12 bits of the target) as a VRAM hint.
"""
import sys
from collections import Counter

JR_RA = 0x03E00008


def main():
    rom = open(sys.argv[1], "rb").read()
    block = int(sys.argv[2]) * 1024 if len(sys.argv) > 2 else 0x4000
    for off in range(0, len(rom), block):
        jr = prologues = 0
        targets = Counter()
        for i in range(off, min(off + block, len(rom)), 4):
            w = int.from_bytes(rom[i:i + 4], "big")
            if w == JR_RA:
                jr += 1
            elif w >> 16 == 0x27BD and w & 0x8000:
                prologues += 1
            elif w >> 26 == 3:
                targets[0x80000000 | ((w & 0x3FFFFFF) << 2) & 0xFFF00000] += 1
        if jr >= 4 and prologues >= 4:
            hint = ", ".join(f"{t:08X}x{n}" for t, n in targets.most_common(2))
            print(f"{off:07X}  jr_ra={jr:4}  prologues={prologues:4}  jal_seg: {hint}")


if __name__ == "__main__":
    main()
