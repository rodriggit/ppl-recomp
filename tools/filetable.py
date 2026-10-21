"""The ROM's file archive: u32 count, then count entries of
{u32 size, u32 offset from the archive start, char name[16]}.

filetable.py ROM [archive_rom_offset] [--list]
Prints a summary per extension and, with --list, every file with its first bytes.
"""
import sys
from collections import defaultdict

ARCHIVE = 0x9BDB40
ENTRY = 0x18


def read_archive(rom, base=ARCHIVE):
    count = int.from_bytes(rom[base:base + 4], "big")
    files = []
    for n in range(count):
        e = base + 4 + n * ENTRY
        size = int.from_bytes(rom[e:e + 4], "big")
        offset = int.from_bytes(rom[e + 4:e + 8], "big")
        name = rom[e + 8:e + 24].split(b"\0")[0].decode("latin-1")
        files.append((name, base + offset, size))
    return files


def main():
    rom = open(sys.argv[1], "rb").read()
    args = [a for a in sys.argv[2:] if not a.startswith("--")]
    base = int(args[0], 16) if args else ARCHIVE
    files = read_archive(rom, base)
    end = max(start + size for _, start, size in files)
    print(f"archive at {base:07X}: {len(files)} files, data up to {end:07X}")

    by_ext = defaultdict(list)
    for name, start, size in files:
        by_ext[name.rsplit(".", 1)[-1]].append(size)
    for ext, sizes in sorted(by_ext.items()):
        print(f"  .{ext:5} {len(sizes):4} files, {sum(sizes):9} bytes")

    if "--list" in sys.argv:
        for name, start, size in files:
            head = rom[start:start + 12].hex(" ", 4)
            print(f"{start:07X} {size:7X}  {name:16} {head}")


if __name__ == "__main__":
    main()
