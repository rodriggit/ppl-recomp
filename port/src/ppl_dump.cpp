// Display list dumps: for a graphics task, its display list walked into the sub-lists to
// ppl_dl_log<name>.txt, and the first 8 MiB of RDRAM to rdram_dump<name>.bin (as the runtime
// stores it: 32-bit words in host order), in the working directory.
#include <array>
#include <cstdio>
#include <fstream>
#include <functional>
#include <string>
#include <vector>

#include "ppl_trace.h"

namespace {
    constexpr uint32_t dump_size = 0x800000;
    constexpr int max_commands = 50000;
    constexpr size_t max_depth = 18;            // the RSP's display list stack is 10 deep

    // F3DEX 1.x and S2DEX 1.x, the game's microcodes (docs/phase0.md), use the old GBI.
    constexpr uint8_t G_DL = 0x06, G_ENDDL = 0xB8, G_MOVEWORD = 0xBC, G_SETCIMG = 0xFF;
    constexpr uint8_t G_MW_SEGMENT = 0x06;
    constexpr uint32_t screen_framebuffers[] = { 0x3B5000, 0x3DA800 };   // docs/phase0.md

    uint32_t word(const uint8_t* rdram, uint32_t addr) {
        return *reinterpret_cast<const uint32_t*>(rdram + (addr & 0x7FFFFC));
    }

    // Calls visit(pc, w0, w1, depth, note) for each command, following sub-lists, as the
    // microcode would; returns the number of commands.
    using Visitor = std::function<void(uint32_t, uint32_t, uint32_t, size_t, const std::string&)>;
    int walk(const uint8_t* rdram, const OSTask* task, const Visitor& visit) {
        std::array<uint32_t, 16> segments{};
        auto physical = [&](uint32_t addr) { return (segments[(addr >> 24) & 0xF] + (addr & 0xFFFFFF)) & 0x7FFFFF; };
        std::vector<uint32_t> stack{ (uint32_t)task->t.data_ptr & 0x7FFFFF };
        int count = 0;
        char note[64];
        while (!stack.empty() && count < max_commands) {
            const uint32_t pc = stack.back();
            const size_t depth = stack.size() - 1;
            stack.back() += 8;
            const uint32_t w0 = word(rdram, pc), w1 = word(rdram, pc + 4);
            const uint8_t op = w0 >> 24;
            note[0] = '\0';
            count++;
            if (op == G_MOVEWORD && (w0 & 0xFF) == G_MW_SEGMENT) {
                const uint32_t seg = ((w0 >> 8) & 0xFFFF) / 4;
                segments[seg & 0xF] = w1 & 0x7FFFFF;
                snprintf(note, sizeof(note), "segment %u = %06X", seg, w1 & 0x7FFFFF);
            }
            else if (op == G_DL) {
                const uint32_t target = physical(w1);
                const bool branch = ((w0 >> 16) & 0xFF) != 0;
                snprintf(note, sizeof(note), "%s %06X", branch ? "branch" : "call", target);
                if (branch) {
                    stack.back() = target;
                }
                else if (stack.size() < max_depth) {
                    stack.push_back(target);
                }
            }
            else if (op == G_ENDDL) {
                stack.pop_back();
            }
            else if (op == G_SETCIMG) {
                snprintf(note, sizeof(note), "color image %06X", physical(w1));
            }
            visit(pc, w0, w1, depth, note);
        }
        return count;
    }
}

void ppl::trace::dump_task(const uint8_t* rdram, const OSTask* task, const std::string& name) {
    std::ofstream("rdram_dump" + name + ".bin", std::ios::binary).write(reinterpret_cast<const char*>(rdram), dump_size);

    FILE* out = fopen(("ppl_dl_log" + name + ".txt").c_str(), "w");
    if (out == nullptr) {
        return;
    }
    const OSTask_s& t = task->t;
    fprintf(out, "task type %u flags %X ucode %08X ucode_data %08X data %08X size %X\n",
        t.type, t.flags, (uint32_t)t.ucode, (uint32_t)t.ucode_data, (uint32_t)t.data_ptr, t.data_size);
    const int count = walk(rdram, task, [&](uint32_t pc, uint32_t w0, uint32_t w1, size_t depth, const std::string& note) {
        fprintf(out, "%*s%06X: %08X %08X%s%s\n", int(depth) * 2, "", pc, w0, w1, note.empty() ? "" : "  ; ", note.c_str());
    });
    fprintf(out, "%d commands%s\n", count, count >= max_commands ? " (limit reached)" : "");
    fclose(out);
    fprintf(stderr, "[ppl] graphics task dumped to ppl_dl_log%s.txt and rdram_dump%s.bin\n", name.c_str(), name.c_str());
}

bool ppl::trace::draws_offscreen(const uint8_t* rdram, const OSTask* task) {
    bool offscreen = false;
    walk(rdram, task, [&](uint32_t, uint32_t w0, uint32_t, size_t, const std::string& note) {
        uint32_t address;
        if ((w0 >> 24) == G_SETCIMG && sscanf(note.c_str(), "color image %X", &address) == 1 &&
            address != screen_framebuffers[0] && address != screen_framebuffers[1]) {
            offscreen = true;
        }
    });
    return offscreen;
}
