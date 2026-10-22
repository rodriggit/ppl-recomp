# Johto campaign — research

Goal: a "Johto" campaign for the 1P Stadium (8 Johto leaders, Elite Four, Lance), as a
port-only mod (C++ hooks over recompiled functions, assets from a `mods/` folder that is
not under version control).

## BIF images
`tools/bif.py ROM --survey` lists every frame; `tools/bif.py ROM NAME.BIF [OUT_DIR]`
writes each frame as PNG. All 440 BIFs (646 frames) parse with no bytes left over, and
`write_bif()` re-encodes them so that every frame decodes back to the same pixels and
palette (output 2% smaller than the originals; 21 files byte-identical).

### Container "3FIB" (func_8001F6B8)
Little-endian when the magic reads "3FIB" byte by byte (every file in the ROM),
big-endian when it reads "BIF3".

| Field | Notes |
|---|---|
| u32 magic, u32 flags | flags 0x108 (stills) or 0x12E (animations) |
| u8 rgba[4] | stored at image set +0x94 (always FF FF FF FF) |
| u32 frame count | |
| u8 rgba[4] | only when flags & 0x10 (+0x98) |
| u32 length, char name[length] | only when flags & 0x08, skipped |
| per frame: u32 a, u32 b, u32 c + one BFF2 | a, b are 0 everywhere; c is 1 for stills, 6 in BBACK (frame time?) |

### Image "BFF2" (func_8001D524)
Big-endian when the magic reads "BFF2" (every image in the ROM), swapped when "2FFB".

| Field | Notes |
|---|---|
| u32 magic, u32 flags | |
| u32 dims | flags & 0x200000: width << 16 \| padded width, else width |
| u32 height, u32 row_bytes | row_bytes = padded width × bytes per pixel |
| u32 length, char name[length] | only when flags & 0x200 |
| u32 count, u32 size | only when flags & 0x400000 (3 fonts, FPPL*.BIF) |
| u32 colors, colors × RGBA8888 | only when (flags & 0xF0) == 0x30; converted to RGBA5551 on load |
| pixels | row_bytes × height bytes, run-length coded when flags & 0x800 |

- flags & 0xF: depth (2: 4 bits, 3: 8, 4: 16, 5: 32).
- (flags >> 4) & 0xF: kind (2: intensity, IA when 16-bit; 3: palette; 5: RGBA).
- flags & 0x400: palette index (flags >> 12) & 0xFF is transparent.

Formats in use: 0x55 RGBA32 (backgrounds, 291 frames), 0x33 CI8 (portraits, labels),
0x54 RGBA16, 0x24 IA16 (silhouettes), 0x22/0x23 I4/I8, 0x32 CI4.

### Compression (func_8001D310)
Control byte c, output stops at row_bytes × height:
- `0xxxxxxx`: c + 1 literal bytes;
- `10xxxxxx`: the next byte repeated (c & 0x3F) + 2 times;
- `11nnnrrr`: n + 2 literal bytes, written r + 2 times in a row (may be clipped).

func_8001CC7C is not a decompressor: it is a buffered read from an open file
(handle {index, size, ROM address, position}).

## Characters
Character ids follow the portrait order of OPPONENT.SBF / POKEMON.SBF / RESULT.SBF:

| Id | Character | Portrait | Pokémon (table 0x800B5B54, index id × 3 + slot) |
|---|---|---|---|
| 0 | Ash | cash.bif | pikachu, squirtle, bulbasr |
| 1 | Gary | cgary.bif | nidoran, growlith, krabby |
| 2 | Brock | cbrock.bif | geodude, vulpix, zubat |
| 3 | Misty | cmisty.bif | horsea, psyduck, staryu |
| 4 | Lt. Surge | cltsurge.bif | raichu, jolteon, magneton |
| 5 | Erika | cerika.bif | tangela, weepinbell, gloom |
| 6 | Koga | ckoga.bif | venomoth, voltorb, golbat |
| 7 | Sabrina | csabrina.bif | abra, hypno, alakazam |
| 8 | Blaine | cblaine.bif | arcanineb, charmeleon, magmar |
| 9 | Tracey | ctracey.bif | marrill, venonat, scyther |
| 10 | Team Rocket | ctr.bif | weezing, arbok, golbatr |
| 11 | Giovanni | cgiovanni.bif | persian, sandslash, nidoking |
| 12 | Ritchie | critchie.bif | sparky, charmander, butterfree |
| 13 | Lorelei | clorelei.bif | cloyster, polywirl, dewgong |
| 14 | Bruno | cbruno.bif | onix, hitmonchan, primape |

- Per character: `CNAME.BIF`, the portrait: 104×129 CI8 (up to 256 colors), name drawn
  on top over a purple background, 2 frames: frame 0 faces right (left side), frame 1
  is the right-side version, art mirrored but the name not (Team Rocket's is a different
  drawing), and has flag 0x100 set. `NAME.BIF`: IA16 silhouette laid over the group
  picture TRSELECT.BIF (320×240) in the 2P character select, so a new character also
  needs its place in that picture. `RECHEADS.BIF`: 16 RGBA32 heads (~32×32), one per
  1P stage in stage order (0 Gary, 1–13 Brock…Bruno, 14 Gary, 15 Mewtwo). Some `PBACK*.BIF` 128×96 backgrounds.
- Per Pokémon: `NAME.BIF` and `XXXXTEXT.BIF` (96×13 CI8 name label). The label names
  are in a second table copied from 0x800C3930 (0x180 bytes: 48 entries of 8 bytes,
  index (id × 3 + slot) × 8).
- The file names live in the main segment's data (ROM 0xC3FF8–0xC4530), not in the
  archive; the code never names `CBROCK.BIF` etc. directly: the SBF scenes do.
- func_80013FB4 (Pokémon pick, scene 1PPK-PICK) reads the player characters from
  0x800B5A10 (s16 each) and loads the three Pokémon of each.
- Scyther (scyther.bif / scyttext.bif) already exists.

## Stadium flow
- 0x801A6D9C: current stage; 0x801A6D98 == 5 seems to be the 1P Stadium mode
  (the music code in 0x800041A4–0x800044C0 special-cases stages 3 and 6 there).
- func_80042C24 builds `sprintf("TRANSITION%d", stage)` and plays that scene of
  OPPONENT.SBF before the match; its argument 2–5 picks GARYWIN / GARYLOSE / TRACEYLOSE /
  TEAMROCKETLOSE instead.
- Background per stage: s16 table at 0x800C51A0 (stages 1–14: 0, 5, 6, 7, 1, 10, 2, 9,
  0, 3, 4, 2, 8, 11; stage 15 → 0) into the ROM offsets at 0x800B6808: 12 raw RGBA16
  320×232 images in the unexplored ROM area (0x35C3F0, 0x3807F0, ... step 0x24400).
- No stage → opponent table exists (searched the whole main segment): from the
  dialogue, the opponent of stages 1–14 is the character with the same id, and
  stage 15 is Gary again:

| Stage | Opponent | |
|---|---|---|
| 1 | Gary | opening match |
| 2–8 | Brock, Misty, Lt. Surge, Erika, Koga, Sabrina, Blaine | badges |
| 9 | Tracey | before the last badge |
| 10, 11 | Team Rocket, Giovanni | last badge |
| 12–14 | Ritchie, Lorelei, Bruno | "Elite Four" |
| 15 | Gary | champion |
| — | Mewtwo | Puzzle Master |

  (To confirm at runtime: where the battle loads the opponent's id.)

### Scene text
SBF text runs are u32 length + length × (class, code) byte pairs:
class 0x18 = uppercase (code 1–26 = A–Z), 0x1C = lowercase, 0x00 = space (1) or line
break (2), 0x10 = punctuation (0x0D `'`, 0x0E `,`, 0x0F `.`, 0x01 `!`; 0x10 is `?`,
0x06 `-`, 0x0A `:`, 0x0B `—` by context). The parser is not located yet (its error
message is "screenLoad: TEXT has internal NULL-character", ROM 0xC4DC8).

## Consequences for the Johto campaign
- Kanto has exactly 13 opponents besides Ash and Gary (ids 2–14), the same count as
  Johto (8 leaders + Will, Koga, Bruno, Karen, Lance). Stages 2–9 can hold the 8 Johto
  leaders and stages 10–14 the Elite Four and Lance, reusing the 15 character slots:
  a Johto run swaps the per-character data (portrait/silhouette file names, the three
  Pokémon and labels, the TRANSITION scenes' text) instead of adding characters.
- Johto Koga and Bruno can point at the existing ckoga/cbruno/koga/bruno files.

## Found on the way
`tools/libultra_names.txt` had two names swapped, now fixed: 0x8009D620 is
**sprintf** (writes the terminating NUL at buf + n; output routine 0x8009D6A0 copies
into the buffer) and 0x8009DA60 is **osSyncPrintf** (first argument is the format;
used for "imageLoad: Could not load IMAGE '%s'"). Harmless for the port: neither
name is reimplemented by N64Recomp (a13e5cf). sprintf is in its `renamed_funcs`,
which only appends `_recomp` and still recompiles the function, and osSyncPrintf is
in no list, so both were always recompiled from the game's code. The next
`gen_config.py` run gives them the right names.

## Still unknown
- Where the battle takes the opponent's id, AI level and music per stage.
- The rest of the SBF scene format (objects, positions, which BIF index a scene shows).
- How 1P progress is stored in the FlashRAM save.
- ROM 0xCA4A0–0x9BDB40 beyond the 12 stage backgrounds.
