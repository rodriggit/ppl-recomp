"""Split the main segment's CPU code into functions and check the result.

funcsplit.py ROM [names.txt] [--out syms.toml] [--verbose]

Function starts:
  - the entry point and every jal target;
  - code addresses built with lui + addiu/ori (thread entries, callbacks);
  - code addresses stored as data words, unless they look like a jump table
    (runs of consecutive words pointing into the same small span);
  - `addiu sp, sp, -N` right after the end of a function (unreferenced code).
Pointer-based and prologue starts are accepted only right after a function end
(jr / j / eret + delay slot, possibly followed by nop padding).

Checks: every function ends in a jump; every branch and j stays inside its
function or targets a function start. Problems are listed; with --out the
function list is written as an N64Recomp symbol file section.
"""
import sys

from funcinfo import load_names

MAIN_ROM, MAIN_VRAM = 0x1000, 0x80000400
CODE_END_VRAM = 0x800AF9C0          # RSP microcode starts here (phase 0)
DATA_END_VRAM = 0x800C98A0          # bss starts here
JR_RA = 0x03E00008
ERET = 0x42000018


def is_terminator(w):
    op = w >> 26
    if op == 2:                                   # j
        return True
    if op == 0 and w & 0x3F == 0x08:              # jr
        return True
    if w == ERET:
        return True
    if op == 4 and (w >> 16) & 0x3FF == 0:        # b (beq zero, zero)
        return True
    return False


def branch_target(w, v):
    op = w >> 26
    rt = (w >> 16) & 0x1F
    if op in (4, 5, 6, 7, 0x14, 0x15, 0x16, 0x17) or (op == 1 and rt in (0, 1, 2, 3, 0x10, 0x11)):
        off = w & 0xFFFF
        off = off - 0x10000 if off & 0x8000 else off
        return v + 4 + off * 4
    return None


class Code:
    def __init__(self, rom):
        self.rom = rom

    def w(self, v):
        i = v - MAIN_VRAM + MAIN_ROM
        return int.from_bytes(self.rom[i:i + 4], "big")

    def after_function_end(self, v):
        """True if v follows `jr ra` + delay slot or `eret` (no delay slot), maybe with nop padding.

        Plain `j` and `b` don't count: inside a switch every case ends with one,
        so the next case label would look like a function start.
        """
        p = v - 4
        while p > MAIN_VRAM and self.w(p) == 0:
            if self.w(p - 4) == JR_RA:
                return True
            p -= 4
        return self.w(p) == ERET or (p - 4 >= MAIN_VRAM and self.w(p - 4) == JR_RA)


def find_starts(code):
    in_code = lambda a: MAIN_VRAM <= a < CODE_END_VRAM and a % 4 == 0
    sure, maybe = {MAIN_VRAM}, {}

    luis = {}
    for v in range(MAIN_VRAM, CODE_END_VRAM, 4):
        w = code.w(v)
        op, rs, rt, imm = w >> 26, (w >> 21) & 0x1F, (w >> 16) & 0x1F, w & 0xFFFF
        if op == 3:
            sure.add(0x80000000 | (w & 0x3FFFFFF) << 2)
        if op == 0x0F:
            luis[rt] = (imm << 16, v)
        elif op in (0x09, 0x0D) and rs in luis and v - luis[rs][1] <= 32:
            a = luis[rs][0] | imm if op == 0x0D else (luis[rs][0] + (imm - 0x10000 if imm & 0x8000 else imm)) & 0xFFFFFFFF
            if in_code(a):
                maybe.setdefault(a, f"ptr@{v:08X}")

    data = [(v, code.w(v)) for v in range(CODE_END_VRAM, DATA_END_VRAM, 4)]
    n = 0
    while n < len(data):
        run = n
        while run < len(data) and in_code(data[run][1]):
            run += 1
        group = [x for _, x in data[n:run]]
        # Jump tables: 3+ consecutive targets within 0x1000 bytes of each other.
        is_table = len(group) >= 3 and max(group) - min(group) < 0x1000
        if not is_table:
            for (v, a) in data[n:run]:
                maybe.setdefault(a, f"data@{v:08X}")
        n = max(run, n + 1)

    for v in range(MAIN_VRAM + 8, CODE_END_VRAM, 4):
        w = code.w(v)
        if w >> 16 == 0x27BD and w & 0x8000 and code.after_function_end(v):
            maybe.setdefault(v, "prologue")

    accepted = {a: why for a, why in maybe.items() if a not in sure and code.after_function_end(a)}
    rejected = {a: why for a, why in maybe.items() if a not in sure and a not in accepted}
    return sure, accepted, rejected


def check(code, starts):
    starts = sorted(starts)
    bounds = list(zip(starts, starts[1:] + [CODE_END_VRAM]))
    start_set = set(starts)
    problems = []
    funcs = []
    for start, end in bounds:
        last = end - 4
        while last > start and code.w(last) == 0 and not is_terminator(code.w(last - 4)):
            last -= 4
        # trailing nop padding belongs to the function; find its last real jump
        real_end = end
        p = end - 4
        while p > start and code.w(p) == 0:
            p -= 4
        if not (is_terminator(code.w(p - 4)) or is_terminator(code.w(p))):
            problems.append((start, "no terminator", f"last word at {p:08X}"))
        for v in range(start, end, 4):
            w = code.w(v)
            t = branch_target(w, v)
            if t is None and w >> 26 == 2:
                t = (v & 0xF0000000) | (w & 0x3FFFFFF) << 2
            if t is not None and not (start <= t < end) and t not in start_set:
                problems.append((start, "jump out of function", f"{v:08X} -> {t:08X}"))
        funcs.append((start, real_end - start))
    return funcs, problems


def main():
    rom = open(sys.argv[1], "rb").read()
    args = [a for a in sys.argv[2:] if not a.startswith("--")]
    names = load_names(args[0] if args and not args[0].endswith(".toml") else None)
    code = Code(rom)
    sure, accepted, rejected = find_starts(code)
    starts = sure | set(accepted)
    funcs, problems = check(code, starts)

    print(f"{len(sure)} jal/entry starts, {len(accepted)} pointer/prologue starts accepted, "
          f"{len(rejected)} candidates rejected; {len(funcs)} functions")
    print(f"{len(problems)} problems")
    for start, kind, detail in problems:
        print(f"  {start:08X} {names.get(start, ''):24} {kind}: {detail}")
    if "--verbose" in sys.argv:
        for a, why in sorted(accepted.items()):
            print(f"  accepted {a:08X} {why}")
        for a, why in sorted(rejected.items()):
            print(f"  rejected {a:08X} {why}")

    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
        with open(out, "w", newline="\n") as f:
            f.write("[[section]]\nname = \"main\"\n")
            f.write(f"rom = 0x{MAIN_ROM:X}\nvram = 0x{MAIN_VRAM:X}\nsize = 0x{CODE_END_VRAM - MAIN_VRAM:X}\n\n")
            f.write("functions = [\n")
            for start, size in funcs:
                name = names.get(start, f"func_{start:08X}")
                f.write(f"    {{ name = \"{name}\", vram = 0x{start:08X}, size = 0x{size:X} }},\n")
            f.write("]\n")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
