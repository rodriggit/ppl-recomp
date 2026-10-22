// Mod files in the game's file archive (docs/johto.md). Every archive read goes through
// func_80001CAC(rom, ram, size), which copies cartridge bytes to RAM; the port hooks it twice
// (tools/gen_config.py MOD_HOOKS):
// - on entry, reads from the virtual area at 0x02000000 (past the 32 MiB cartridge) are
//   served from the mod files loaded here;
// - on exit, after a read of the archive directory (ROM 0x9BDB40 into 0x8018ABB0), the
//   entries listed in files.ini are pointed at those files: an existing name is replaced,
//   a new one is appended (the directory buffer holds 597 entries, the game uses 465).
// func_8001D278 takes the file count from the directory it just read, so appended entries
// are found by name like the others.
#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "recomp.h"
#include "ppl_config.h"
#include "ppl_mods.h"

namespace {
    constexpr uint32_t archive_rom = 0x9BDB40;      // tools/filetable.py
    constexpr uint32_t directory_ram = 0x8018ABB0;  // func_8001C8B0's copy of the directory
    constexpr uint32_t directory_bytes = 0x3800;
    constexpr uint32_t entry_bytes = 0x18;          // u32 size, u32 offset from the archive, char name[16]
    constexpr uint32_t max_entries = (directory_bytes - 4) / entry_bytes;
    constexpr uint32_t virtual_rom = 0x02000000;

    struct ModFile {
        std::string name;    // archive name, uppercase
        uint32_t rom;        // in the virtual area
        uint32_t size;
    };
    std::vector<ModFile> files;
    std::vector<uint8_t> data;   // the virtual area's bytes

    uint8_t& byte(uint8_t* rdram, uint32_t addr) {
        return rdram[(addr & 0x1FFFFFFF) ^ 3];   // RDRAM holds 32-bit words in host order
    }

    uint32_t& word(uint8_t* rdram, uint32_t addr) {
        return *reinterpret_cast<uint32_t*>(rdram + (addr & 0x1FFFFFFC));
    }

    std::string trim(const std::string& s) {
        size_t start = s.find_first_not_of(" \t\r\n");
        size_t end = s.find_last_not_of(" \t\r\n");
        return start == std::string::npos ? std::string() : s.substr(start, end - start + 1);
    }

    std::string upper(std::string s) {
        std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return char(std::toupper(c)); });
        return s;
    }

    void patch_directory(uint8_t* rdram) {
        uint32_t count = word(rdram, directory_ram);
        if (count > max_entries) {
            return;
        }
        for (const ModFile& f : files) {
            uint32_t i = 0;
            for (; i < count; i++) {
                const uint32_t name = directory_ram + 4 + i * entry_bytes + 8;
                size_t k = 0;
                while (k < 16 && k < f.name.size() && byte(rdram, name + k) == uint8_t(f.name[k])) {
                    k++;
                }
                if (k == f.name.size() && (k == 16 || byte(rdram, name + k) == 0)) {
                    break;
                }
            }
            if (i == count) {
                if (count == max_entries) {
                    static bool reported = false;
                    if (!reported) {
                        fprintf(stderr, "[ppl] mods: the archive directory is full (%u files), %s and later new names left out\n",
                                max_entries, f.name.c_str());
                        reported = true;
                    }
                    continue;
                }
                const uint32_t name = directory_ram + 4 + i * entry_bytes + 8;
                for (size_t k = 0; k < 16; k++) {
                    byte(rdram, name + k) = k < f.name.size() ? uint8_t(f.name[k]) : 0;
                }
                count++;
            }
            const uint32_t entry = directory_ram + 4 + i * entry_bytes;
            word(rdram, entry) = f.size;
            word(rdram, entry + 4) = f.rom - archive_rom;
        }
        word(rdram, directory_ram) = count;
    }
}

// Entry of func_80001CAC: serve reads from the virtual area. Returns 1 when done.
extern "C" int ppl_rom_read(uint8_t* rdram, recomp_context* ctx) {
    const uint32_t rom = (uint32_t)ctx->r4, ram = (uint32_t)ctx->r5, size = (uint32_t)ctx->r6;
    if (rom < virtual_rom) {
        return 0;
    }
    for (uint32_t i = 0; i < size; i++) {
        const uint32_t at = rom - virtual_rom + i;
        byte(rdram, ram + i) = at < data.size() ? data[at] : 0;   // windows may read past a file
    }
    return 1;
}

// Before func_80001CAC returns (its registers still hold the end of the copy: s2 the RAM
// address, s3 the ROM address): after a directory read, point the entries at the mod files.
extern "C" void ppl_rom_read_done(uint8_t* rdram, recomp_context* ctx) {
    if (files.empty()) {
        return;
    }
    const uint32_t ram_end = (uint32_t)ctx->r18, rom_end = (uint32_t)ctx->r19;
    const uint32_t n = ram_end - directory_ram;
    if (ram_end > directory_ram && n <= directory_bytes && rom_end - n == archive_rom) {
        patch_directory(rdram);
    }
}

void ppl::mods::load(const std::filesystem::path& executable_folder) {
    const std::string campaign = ppl::config::get("mods", "campaign");
    if (campaign.empty()) {
        return;
    }
    std::filesystem::path folder = std::filesystem::u8path(ppl::config::get("mods", "folder"));
    if (folder.empty()) {
        folder = "mods";
    }
    if (folder.is_relative()) {
        folder = executable_folder / folder;
    }
    folder /= std::filesystem::u8path(campaign);
    const std::filesystem::path list = folder / "files.ini";
    std::ifstream in(list);
    if (!in) {
        ppl::config::warn("[mods] campaign = " + campaign + ": could not read " + ppl::config::path_text(list));
        return;
    }
    // files.ini: "ARCHIVE.NAME = file in the campaign folder" per line; ; and # start comments,
    // [sections] are ignored.
    std::string line;
    int number = 0;
    uint32_t added = 0;
    while (std::getline(in, line)) {
        number++;
        line = trim(line);
        if (line.empty() || line[0] == ';' || line[0] == '#' || line[0] == '[') {
            continue;
        }
        const size_t eq = line.find('=');
        const std::string name = upper(trim(line.substr(0, eq)));
        const std::string source = eq == std::string::npos ? std::string() : trim(line.substr(eq + 1));
        const std::string where = ppl::config::path_text(list) + " line " + std::to_string(number);
        if (name.empty() || source.empty() || name.size() > 15) {
            ppl::config::warn(where + ": expected NAME = file (names up to 15 characters)");
            continue;
        }
        std::ifstream file(folder / std::filesystem::u8path(source), std::ios::binary);
        if (!file) {
            ppl::config::warn(where + ": could not read " + source);
            continue;
        }
        std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
        data.resize((data.size() + 15) & ~size_t(15));
        files.push_back({ name, uint32_t(virtual_rom + data.size()), uint32_t(bytes.size()) });
        data.insert(data.end(), bytes.begin(), bytes.end());
        added++;
    }
    fprintf(stderr, "[ppl] mods: campaign %s, %u file(s) from %s\n", campaign.c_str(), added,
            ppl::config::path_text(folder).c_str());
}
