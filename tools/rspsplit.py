"""Show the structure of an RSP code area: zero runs and jumps into IMEM.

rspsplit.py ROM rom_start rom_end vram
Lists runs of >= 2 zero words and every `j`/`jal` whose target is an IMEM
address (0x1000-0x1FFF, as RSP code is linked at 0x04001000), with the first
word after each zero run, which is where a new microcode is likely to start.
"""
import sys


def main():
    rom = open(sys.argv[1], "rb").read()
    start, end, vram = (int(a, 16) for a in sys.argv[2:5])
    zeros = 0
    for i in range(start, end, 4):
        w = int.from_bytes(rom[i:i + 4], "big")
        if w == 0:
            zeros += 1
            continue
        if zeros >= 2:
            print(f"-- {zeros} zero words, next code at rom {i:07X} vram {vram + i - start:08X}: {w:08X}")
        zeros = 0
        if w >> 26 in (2, 3):
            target = (w & 0x3FFFFFF) << 2
            if 0x1000 <= target < 0x2000 or 0x04001000 <= target < 0x04002000:
                kind = "j  " if w >> 26 == 2 else "jal"
                print(f"   rom {i:07X} vram {vram + i - start:08X}  {kind} 0x{target & 0x1FFF:04X}")


if __name__ == "__main__":
    main()
