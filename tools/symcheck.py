"""Check the function split and names against what N64Recomp and the runtime provide.

symcheck.py ROM names.txt SYMBOL_LISTS_CPP RUNTIME_DIR [PORT_DIR] [--why ADDR]

Functions come from funcsplit.py. A function is not recompiled when its name is in
N64Recomp's reimplemented or ignored list (src/symbol_lists.cpp at the pinned commit)
or in gen_config.py's IGNORED; every other function is (gen_config.py's STUBS as empty
bodies). Reports, for the recompiled ones only:
  - split problems (branches out of the function that are not tail calls);
  - instructions N64Recomp rejects: cop0 moves other than Status, eret, cache, tlb*, sync;
  - loads/stores through a KSEG1 or RDB-port address (they would fault in the port);
  - calls, tail calls and address references to functions that are not recompiled and
    that neither the runtime nor the port defines as NAME_recomp; from unreachable code,
    only the calls (the port must still define them to link).
Then the names of N64Recomp's reimplemented list that our list does not have.
--why ADDR prints how the function at ADDR is reached from the entry point instead.
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

import rabbitizer

from funcinfo import load_names
from funcsplit import CODE_END_VRAM, DATA_END_VRAM, MAIN_VRAM, Code, branch_target, check, find_starts
from gen_config import IGNORED, STUBS

LOADS_STORES = {0x20, 0x21, 0x23, 0x24, 0x25, 0x27, 0x28, 0x29, 0x2B, 0x31, 0x35, 0x37, 0x39, 0x3D, 0x3F}
COP0_STATUS = 12


def n64recomp_lists(path):
    text = Path(path).read_text()
    lists = {}
    for block in ("reimplemented_funcs", "ignored_funcs", "renamed_funcs"):
        body = text.split(f"N64Recomp::{block}", 1)[1].split("};", 1)[0]
        lists[block] = set(re.findall(r'"([^"]+)"', body))
    return lists


def defined_recomp_funcs(dirs):
    found = set()
    for d in dirs:
        for f in Path(d).rglob("*"):
            if f.suffix in (".c", ".cpp") and "build" not in f.parts:
                found |= set(re.findall(r"\bvoid\s+(\w+)_recomp\s*\(", f.read_text(errors="ignore")))
    return found


def scan(code, start, end):
    """Rejected instructions, hardware accesses and references of one function."""
    rejected, hw, refs = [], [], []
    known = {}      # register -> (value built by lui [+ addiu/ori], vram of the lui)
    for v in range(start, end, 4):
        w = code.w(v)
        op, rs, rt, imm = w >> 26, (w >> 21) & 0x1F, (w >> 16) & 0x1F, w & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        dest = (w >> 11) & 0x1F if op == 0 else rt if 0x08 <= op <= 0x0F or 0x20 <= op <= 0x27 else None
        value = None
        if op == 0x0F:
            value = (imm << 16, v)
        elif op in (0x09, 0x0D) and rs in known and v - known[rs][1] <= 32:
            a = known[rs][0] | imm if op == 0x0D else (known[rs][0] + simm) & 0xFFFFFFFF
            value = (a, known[rs][1])
            if MAIN_VRAM <= a < CODE_END_VRAM:
                refs.append(("address", v, a))
            elif CODE_END_VRAM <= a < DATA_END_VRAM:
                refs.append(("data", v, a))
        elif op == 0 and w & 0x3F == 0x21 and (rs in known) != (rt in known):
            value = known[rs if rs in known else rt]          # addu of a table base and an index
        elif op == 0x10:
            ins = rabbitizer.Instruction(w, v)
            if ins.getOpcodeName() not in ("mfc0", "mtc0") or (w >> 11) & 0x1F != COP0_STATUS:
                rejected.append(f"{v:08X} {ins.disassemble()}")
        elif op == 0x2F or (op == 0 and w & 0x3F == 0x0F):
            rejected.append(f"{v:08X} {rabbitizer.Instruction(w, v).disassemble()}")
        elif op in LOADS_STORES and rs in known and v - known[rs][1] <= 32:
            a = (known[rs][0] + simm) & 0xFFFFFFFF
            if 0xA0000000 <= a < 0xC0000000 or a >> 16 == 0xC000:
                hw.append(f"{v:08X} {a:08X}")
            elif CODE_END_VRAM <= a < DATA_END_VRAM:
                refs.append(("data", v, a))
        if dest:
            if value:
                known[dest] = value
            else:
                known.pop(dest, None)
        if op == 3:
            refs.append(("call", v, 0x80000000 | (w & 0x3FFFFFF) << 2))
        elif op == 2:
            t = 0x80000000 | (w & 0x3FFFFFF) << 2
            if not start <= t < end:
                refs.append(("tail call", v, t))
        else:
            t = branch_target(w, v)
            if t is not None and not start <= t < end:
                refs.append(("tail call", v, t))
    return rejected, hw, refs


def main():
    args = sys.argv[1:sys.argv.index("--why")] if "--why" in sys.argv else sys.argv[1:]
    rom_path, names_path, lists_path, *dirs = args
    code = Code(open(rom_path, "rb").read())
    names = load_names(names_path)
    lists = n64recomp_lists(lists_path)
    provided = defined_recomp_funcs(dirs)
    not_recompiled = lists["reimplemented_funcs"] | lists["ignored_funcs"] | set(IGNORED)

    sure, accepted, _ = find_starts(code)
    funcs, problems = check(code, sure | set(accepted))
    name_of = lambda a: names.get(a, f"func_{a:08X}")
    recompiled = {s for s, _ in funcs if name_of(s) not in not_recompiled}
    scans = {s: scan(code, s, s + size) for s, size in funcs if s in recompiled}
    for s in recompiled:
        if name_of(s) in STUBS:
            scans[s] = ([], [], [])

    # Reachable in the port: from the entry point, following references out of recompiled
    # functions only (the runtime replaces the rest). Code pointers stored as data are
    # grouped into tables (gaps under 0x10 bytes). A table that some code addresses (one
    # of its words) counts once that code is reachable; any other table counts from the
    # start, since it may be reached from a base address outside it (arrays of structs).
    starts = {s for s, _ in funcs}
    tables = []
    for v in range(CODE_END_VRAM, DATA_END_VRAM, 4):
        if code.w(v) in starts:
            if tables and v - tables[-1][-1] < 0x10:
                tables[-1].append(v)
            else:
                tables.append([v])
    addressed = set()
    for s, size in funcs:
        for kind, _, t in scans[s][2] if s in scans else scan(code, s, s + size)[2]:
            addressed |= {n for n, table in enumerate(tables) if kind == "data" and table[0] <= t <= table[-1]}
    live, used, parent = {MAIN_VRAM}, set(range(len(tables))) - addressed, {}
    todo = [MAIN_VRAM]
    for n in used:
        for p in tables[n]:
            if code.w(p) not in live:
                live.add(code.w(p))
                parent[code.w(p)] = (p, "pointer in data")
                todo.append(code.w(p))
    while todo:
        f = todo.pop()
        targets = []
        for kind, v, t in scans[f][2] if f in scans else ():
            if kind != "data":
                targets.append((t, f"{kind} {v:08X}"))
                continue
            for n, table in enumerate(tables):
                if n not in used and table[0] <= t <= table[-1]:
                    used.add(n)
                    targets += [(code.w(p), f"table {t:08X} via {v:08X}") for p in table]
        for t, how in targets:
            if t in starts and t not in live:
                live.add(t)
                parent[t] = (f, how)
                todo.append(t)
    if "--why" in sys.argv:
        t = int(sys.argv[sys.argv.index("--why") + 1], 16)
        while t in parent:
            f, how = parent[t]
            print(f"{t:08X} {name_of(t):24} <- {how} at {f:08X} {name_of(f) if f in starts else ''}")
            t = f
        return
    unused = sum(len(t) for n, t in enumerate(tables) if n not in used)
    dead = sorted(recompiled - live)
    print(f"{len(funcs)} functions: {len(recompiled)} recompiled ({len(dead)} of them unreachable in the port), "
          f"{len(funcs) - len(recompiled)} replaced or ignored by N64Recomp or the config; "
          f"{len(provided)} NAME_recomp defined by the runtime/port")
    print(f"{len(tables) - len(used)} of {len(tables)} code pointer tables in data ({unused} pointers) "
          f"are addressed only by unreachable code")

    bad = defaultdict(list)
    for start, kind, detail in problems:
        if start in recompiled and name_of(start) not in STUBS:
            bad[start].append(f"{kind}: {detail}")
    for start, (rejected, hw, _) in scans.items():
        bad[start] += [f"rejected  {x}" for x in rejected] + [f"hardware  {x}" for x in hw]

    print("\nReachable recompiled functions that N64Recomp rejects or that touch hardware:")
    for start in sorted(s for s in bad if s in live and bad[s]):
        print(f"  {start:08X} {name_of(start)}")
        for line in bad[start]:
            print(f"      {line}")

    print("\nUnreachable recompiled functions that N64Recomp rejects or that touch hardware "
          "(to ignore or stub in the config):")
    print("  " + " ".join(name_of(s) for s in dead if bad[s]))

    # Reachable code needs a real NAME_recomp; unreachable code only needs its calls to link.
    missing, link_only = defaultdict(list), defaultdict(list)
    for start in sorted(recompiled):
        for kind, v, t in scans[start][2]:
            if t in starts and name_of(t) in not_recompiled and name_of(t) not in provided:
                if start in live:
                    missing[name_of(t)].append(f"{kind} {v:08X} in {name_of(start)}")
                elif kind != "address":
                    link_only[name_of(t)].append(f"{kind} {v:08X} in {name_of(start)}")

    print("\nNot recompiled, reached from reachable recompiled code, and not defined by the runtime/port:")
    for name, where in sorted(missing.items()):
        print(f"  {name:28} {len(where):3}  e.g. {'; '.join(where[:3])}")
    print("\nCalled only from unreachable recompiled code (the port needs a definition to link):")
    for name, where in sorted(link_only.items()):
        if name not in missing:
            print(f"  {name:28} {len(where):3}  e.g. {'; '.join(where[:3])}")

    ours = set(names.values())
    print("\nIn N64Recomp's reimplemented list but not in our names:")
    print("  " + " ".join(sorted(lists["reimplemented_funcs"] - ours)))


if __name__ == "__main__":
    main()
