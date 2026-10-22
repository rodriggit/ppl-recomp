# Pinned upstream versions

The same commits as [mt64-recomp](https://github.com/rodriggit/mt64-recomp), an earlier
N64Recomp port by the same author, which pins the ones Zelda64Recomp v1.2.2 uses. Source is read and built only at these commits.

| Project | Commit | Date |
|---|---|---|
| N64Recomp/N64Recomp | a13e5cff96686776b0e03baf23923e3c1927b770 | 2025-08-11 |
| N64Recomp/N64ModernRuntime | df7e820d8c55e4fcb4616c210cbb2c01b25cd48c | 2025-08-12 |
| rt64/rt64 | 23cab603c4f9f4a8b369b38e036f1aa484603878 | 2025-08-25 |

RT64 is built with mt64-recomp's RT64 patches (`patches/rt64/*.patch` in that
repository, described in its `docs/versions.md`). Its CMake applies them to the RT64
checkout when it configures; this port uses the same checkout and doesn't apply them
itself, so without that repository apply them by hand (`git apply` in the RT64 checkout,
in file order). Most are fixes that apply to any game (VI X scale, framebuffer tile
copies, buffer reservation); the 2D linear filtering and the shader list are options this
port doesn't turn on yet.

SDL2 2.30.3 (official release package, fetched by CMake on Windows).

Python tools (PyPI): rabbitizer 1.16.2, spimdisasm 1.42.4.
