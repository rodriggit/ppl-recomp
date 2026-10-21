"""Disassemble MIPS words from a ROM offset: romdis.py ROM rom_offset vram count [--rsp]"""
import sys

import rabbitizer


def main():
    rom_path, rom, vram, count = sys.argv[1], int(sys.argv[2], 16), int(sys.argv[3], 16), int(sys.argv[4], 0)
    category = rabbitizer.InstrCategory.RSP if "--rsp" in sys.argv[5:] else rabbitizer.InstrCategory.CPU
    with open(rom_path, "rb") as f:
        f.seek(rom)
        data = f.read(count * 4)
    for i in range(0, len(data), 4):
        word = int.from_bytes(data[i:i + 4], "big")
        instr = rabbitizer.Instruction(word, vram + i, category=category)
        print(f"{rom + i:08X} {vram + i:08X} {word:08X}  {instr.disassemble()}")


if __name__ == "__main__":
    main()
