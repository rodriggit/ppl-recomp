"""Write the recompiler inputs: BUILD_DIR/syms.toml and BUILD_DIR/recomp.toml for
N64Recomp, BUILD_DIR/aspMain.toml for RSPRecomp.

gen_config.py ROM BUILD_DIR

The code is stored uncompressed in the ROM (phase 0), so the ROM itself is the
recompiler's input. Functions come from funcsplit.py, names from libultra_names.txt.
"""
import os
import sys
from pathlib import Path

from funcinfo import load_names
from funcsplit import Code, check, find_starts, write_syms

ENTRY = 0x80000400                 # clears bss, sets sp, `jr $t2` to the game's startup
ENTRY_JR = 0x80000434
STARTUP = 0x80000520

# aspMain (docs/phase0.md): text loaded at IMEM 0x1080; its data starts with 8 constants
# and then the table of the 16 command handlers, reached with `jr` (indirect branch targets).
ASPMAIN_TEXT_ROM, ASPMAIN_TEXT_SIZE = 0xB0690, 0xE20
ASPMAIN_DATA_ROM = 0xC1FF0
ASPMAIN_TABLE = ASPMAIN_DATA_ROM + 0x10

STUBS = [
    # The debug libultra's error path: the default handler waits in osRecvMesg for an RDB
    # event that never comes in the port (docs/phase1.md).
    "__osError",
    # SP debugger, unreachable but called by the rest of it. The first three are the only
    # callers of __osSpRawReadIo / __osSpRawWriteIo, which nothing defines; the others
    # touch SP registers (0x800AAA18 also jumps into 0x800AA9D4). N64Recomp doesn't
    # analyze stubbed functions.
    "func_800A7BE0",
    "func_800A7C30",
    "func_800AA258",
    "func_800AA9B0",
    "func_800AA9D4",
    "func_800AAA18",
    "func_800AAA2C",
]

# Unreachable in the port (tools/symcheck.py), and N64Recomp would reject them or they
# touch hardware: N64Recomp compiles every function it is given.
IGNORED = [
    "func_800914F0",   # PI raw DMA, referenced only by osCreatePiManager
    "func_800A33E0",   # same
    "func_800A1EB0",   # PI domain registers, only from osInitialize
    "func_800A3F24",   # RDB receive, only from __osException
    "func_800A3E34",   # only from func_800A3F24
    "func_800A3CF8",   # RDB port, only from func_800A3E34
    "func_800A3DBC",   # same
]

# Functions the runtime reimplements in a way this game can't use. They are renamed and
# ignored, and an ignored function keeps its name, so the generated code calls the port's
# version (port/src/ppl_hooks.cpp).
PORT_REPLACED = {
    # The runtime's osYieldThread does nothing (an assert in debug builds), and the HVQM2
    # player spins on it in func_8003E6B8 until the timekeeper thread frees a frame buffer.
    "osYieldThread": "ppl_osYieldThread",
    # The runtime's osStopThread only stops the calling thread. The HVQM2 player stops the
    # scheduler thread during a video (func_8003F810) and restarts it after.
    "osStopThread": "ppl_osStopThread",
}

# Busy waits in the game's sound code: they poll state that the audio thread updates, and on
# the console that thread preempts them at the next retrace. The runtime never preempts a
# game thread, so yield inside the loop (before the per-frame sound update it calls) to let
# a woken thread of higher priority run. (function, vram of the call to func_800039C0)
YIELD_HOOKS = [
    ("func_800039C0", 0x80003D5C),   # waits for a sound to stop
    ("func_800039C0", 0x80003DD4),   # same, second sound slot
    ("func_80003E00", 0x80003E58),   # retries a sound until its channel is free
    ("func_800048C0", 0x800048F8),   # waits while func_8008B80C(1) is non-zero
]
YIELD_TEXT = "{ void yield_self_1ms(uint8_t* rdram); yield_self_1ms(rdram); }"

# Switches N64Recomp doesn't recognize (its log calls them indirect tail calls): they dispatch
# with `lw $rt, 0($rp)` + `jr $rt`, where $rp walks the jump table in a loop, and N64Recomp
# only follows a table indexed through an addu. The runtime then finds no function at the
# case. Label every case and dispatch to it before the lw; the cases themselves stay as
# recompiled. (function, vram of the lw, table, entries: the sltiu bound before the lw)
JUMP_TABLES = [
    ("func_80007E68", 0x80007EA8, 0x800C31E8, 6),
    ("func_80008EA4", 0x80008F04, 0x800C3250, 6),
    ("func_8004DCC0", 0x8004DE0C, 0x800C5FF0, 6),
]
OP_LW, FUNCT_JR = 0x23, 0x08


def jump_table_hooks(code, funcs, names):
    bounds = {names.get(s, f"func_{s:08X}"): (s, s + size) for s, size in funcs}
    hooks = []
    for func, lw_vram, table, count in JUMP_TABLES:
        start, end = bounds[func]
        lw, jr, delay = code.w(lw_vram), code.w(lw_vram + 4), code.w(lw_vram + 8)
        base, target = lw >> 21 & 31, lw >> 16 & 31
        assert lw >> 26 == OP_LW and lw & 0xFFFF == 0, f"no lw $rt, 0($rp) at 0x{lw_vram:08X}"
        assert jr == target << 21 | FUNCT_JR and delay == 0, f"no jr $rt + nop at 0x{lw_vram + 4:08X}"
        cases = sorted({code.w(table + 4 * i) for i in range(count)})
        assert all(start <= c < end for c in cases), f"case outside {func}"
        lines = [f"ctx->r{target} = MEM_W(0x0, ctx->r{base});",
                 f"switch ((uint32_t)ctx->r{target}) {{"]
        lines += [f"    case 0x{c:08X}: goto ppl_jt_{c:08X};" for c in cases]
        lines += [f"    default: switch_error(__func__, 0x{lw_vram + 4:08X}, 0x{table:08X});", "}"]
        hooks.append((func, lw_vram, "{\n        " + "\n        ".join(lines) + "\n    }"))
        hooks += [(func, c, f"ppl_jt_{c:08X}:;") for c in cases]
    return hooks


def all_hooks(code, funcs, names):
    """Text inserted by N64Recomp before an instruction, as {(function, vram): C text}.
    N64Recomp takes one hook per instruction, so texts for the same one are joined."""
    hooks = {}
    for func, vram, text in [(f, v, YIELD_TEXT) for f, v in YIELD_HOOKS] + jump_table_hooks(code, funcs, names):
        hooks[func, vram] = hooks[func, vram] + "\n    " + text if (func, vram) in hooks else text
    return hooks


def main():
    rom_path, build = Path(sys.argv[1]), Path(sys.argv[2])
    build.mkdir(parents=True, exist_ok=True)
    code = Code(rom_path.read_bytes())
    names = load_names(Path(__file__).with_name("libultra_names.txt"))
    names = {a: PORT_REPLACED.get(n, n) for a, n in names.items()}
    ignored = IGNORED + list(PORT_REPLACED.values())
    sure, accepted, _ = find_starts(code)
    funcs, _ = check(code, sure | set(accepted))
    write_syms(build / "syms.toml", funcs, names)

    present = {names.get(s, f"func_{s:08X}") for s, _ in funcs}
    missing = [n for n in STUBS + ignored + [h[0] for h in YIELD_HOOKS + JUMP_TABLES] if n not in present]
    assert not missing, f"not a function: {missing}"
    assert ENTRY in {s for s, _ in funcs} and code.w(ENTRY_JR) == 0x01400008  # jr $t2

    quote = lambda xs: ", ".join(f'"{x}"' for x in xs)
    rom_rel = os.path.relpath(rom_path.resolve(), build.resolve()).replace("\\", "/")
    out = [
        "# Generated by tools/gen_config.py - do not edit",
        "[input]",
        'symbols_file_path = "syms.toml"',
        f'rom_file_path = "{rom_rel}"',
        'output_func_path = "RecompiledFuncs"',
        f"entrypoint = 0x{ENTRY:08X}",
        'recomp_include = "#include \\"recomp.h\\""',
        "",
        "[patches]",
        f"stubs = [ {quote(STUBS)} ]",
        f"ignored = [ {quote(ignored)} ]",
        "",
        "# boot: `jr $t2` always goes to the game's startup; make it a direct tail call.",
        "# N64Recomp renames the entrypoint function before applying patches.",
        "[[patches.instruction]]",
        'func = "recomp_entrypoint"',
        f"vram = 0x{ENTRY_JR:08X}",
        f"value = 0x{0x08000000 | (STARTUP >> 2) & 0x03FFFFFF:08X}",
        "",
    ]
    for (func, vram), text in sorted(all_hooks(code, funcs, names).items(), key=lambda h: h[0][1]):
        out += [
            "[[patches.hook]]",
            f'func = "{func}"',
            f"before_vram = 0x{vram:08X}",
            f"text = '''{text}'''",
            "",
        ]
    (build / "recomp.toml").write_text("\n".join(out), newline="\n")
    print(f"{len(funcs)} functions, {len(STUBS)} stubbed, {len(ignored)} ignored "
          f"({len(PORT_REPLACED)} replaced by the port); wrote {build}")
    write_aspmain_config(build, code.rom, rom_rel)


def write_aspmain_config(build, rom, rom_rel):
    imem_start, imem_end = 0x1080, 0x1080 + ASPMAIN_TEXT_SIZE
    table = [int.from_bytes(rom[ASPMAIN_TABLE + 2 * i:ASPMAIN_TABLE + 2 * i + 2], "big") for i in range(16)]
    assert all(imem_start <= t < imem_end for t in table), [hex(t) for t in table]
    out = [
        "# Generated by tools/gen_config.py - do not edit",
        f"text_offset = 0x{ASPMAIN_TEXT_ROM:08X}",
        f"text_size = 0x{ASPMAIN_TEXT_SIZE:X}",
        "text_address = 0x04001080",
        f'rom_file_path = "{rom_rel}"',
        'output_file_path = "rsp/aspMain.cpp"',
        'output_function_name = "aspMain"',
        f"extra_indirect_branch_targets = [ {', '.join(f'0x{t:04X}' for t in sorted(set(table)))} ]",
        "",
    ]
    (build / "aspMain.toml").write_text("\n".join(out), newline="\n")
    (build / "rsp").mkdir(exist_ok=True)
    print(f"aspMain: {len(set(table))} command handlers")


if __name__ == "__main__":
    main()
