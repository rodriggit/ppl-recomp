"""Find addresses built with lui + addiu/ori in CPU code, and data words, inside a range.

ptrscan.py ROM code_rom_start code_rom_end vram lo hi
Reports each referenced address in [lo, hi) with the places that build it
(lui/addiu pairs within 8 instructions) or hold it as a 32-bit data word.
"""
import sys
from collections import defaultdict


def main():
    rom = open(sys.argv[1], "rb").read()
    start, end, vram, lo, hi = (int(a, 16) for a in sys.argv[2:7])
    words = [int.from_bytes(rom[i:i + 4], "big") for i in range(start, end, 4)]
    refs = defaultdict(list)
    for n, w in enumerate(words):
        if lo <= w < hi:
            refs[w].append(f"data@{vram + n * 4:08X}")
        if w >> 26 != 0x0F:
            continue
        reg, upper = (w >> 16) & 0x1F, (w & 0xFFFF) << 16
        for m in range(n + 1, min(n + 9, len(words))):
            x = words[m]
            op, rs = x >> 26, (x >> 21) & 0x1F
            if rs == reg and op in (0x09, 0x0D, 0x23, 0x2B):  # addiu, ori, lw, sw
                imm = x & 0xFFFF
                addr = (upper | imm) if op == 0x0D else (upper + (imm - 0x10000 if imm & 0x8000 else imm)) & 0xFFFFFFFF
                if lo <= addr < hi:
                    refs[addr].append(f"{vram + n * 4:08X}")
                break
    for addr in sorted(refs):
        print(f"{addr:08X}  (rom {addr - vram + start:07X})  <- " + " ".join(refs[addr][:6]))


if __name__ == "__main__":
    main()
