// Diagnostic counters, printed and reset once per second when PPL_TRACE is set (main.cpp),
// and the display list dumps (ppl_dump.cpp): F9, and PPL_DUMP_OFFSCREEN=1.
#ifndef PPL_TRACE_H
#define PPL_TRACE_H

#include <atomic>
#include <cstdint>
#include <string>

#include "ultramodern/ultra64.h"

namespace ppl::trace {
    inline std::atomic<uint32_t> gfx_tasks{0};      // display lists sent to RT64
    inline std::atomic<uint32_t> audio_tasks{0};
    inline std::atomic<uint32_t> video_tasks{0};    // HVQM2 (skipped)
    inline std::atomic<uint32_t> retrace_waits{0};  // osViGetCurrentLine pairs

    // Set by F9 to the number of graphics tasks to dump (_0.._3), each with the RDRAM.
    inline std::atomic_int dump_remaining{0};
    constexpr int dump_tasks = 4;
    // PPL_DUMP_OFFSCREEN=1: the first tasks that draw into a buffer other than the screen's
    // (_offscreen_0..).
    constexpr int offscreen_dumps = 4;
    void dump_task(const uint8_t* rdram, const OSTask* task, const std::string& name);
    bool draws_offscreen(const uint8_t* rdram, const OSTask* task);
}

#endif
