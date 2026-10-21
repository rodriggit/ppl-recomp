"""List `lui rX, imm` in a code range: luiscan.py ROM rom_start rom_end vram imm [imm ...] (hex)"""
import sys


def main():
    rom = open(sys.argv[1], "rb").read()
    start, end, vram = (int(a, 16) for a in sys.argv[2:5])
    wanted = {int(a, 16) for a in sys.argv[5:]}
    hits = {}
    for i in range(start, end, 4):
        w = int.from_bytes(rom[i:i + 4], "big")
        if w >> 26 == 0x0F and (w & 0xFFFF) in wanted:
            hits.setdefault(w & 0xFFFF, []).append(vram + i - start)
    for imm, addrs in sorted(hits.items()):
        print(f"lui 0x{imm:04X}: {len(addrs):3}  " + " ".join(f"{a:08X}" for a in addrs[:12]))


if __name__ == "__main__":
    main()
