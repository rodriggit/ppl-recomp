"""Find RSP microcode: runs of vector instructions (COP2, LWC2, SWC2).

rspscan.py ROM rom_start rom_end vram [block]
Prints blocks (default 0x400 bytes) where at least 1/8 of the words are vector ops.
"""
import sys

VECTOR_OPS = {0x12, 0x32, 0x3A}  # COP2, LWC2, SWC2


def main():
    rom = open(sys.argv[1], "rb").read()
    start, end, vram = (int(a, 16) for a in sys.argv[2:5])
    block = int(sys.argv[5], 16) if len(sys.argv) > 5 else 0x400
    for off in range(start, end, block):
        words = [int.from_bytes(rom[i:i + 4], "big") for i in range(off, min(off + block, end), 4)]
        vec = sum(1 for w in words if w >> 26 in VECTOR_OPS)
        if vec * 8 >= len(words):
            print(f"rom {off:07X}  vram {vram + off - start:08X}  vector ops {vec:3}/{len(words)}")


if __name__ == "__main__":
    main()
