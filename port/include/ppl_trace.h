// Diagnostic counters, printed and reset once per second when PPL_TRACE is set (main.cpp).
#ifndef PPL_TRACE_H
#define PPL_TRACE_H

#include <atomic>
#include <cstdint>

namespace ppl::trace {
    inline std::atomic<uint32_t> gfx_tasks{0};      // display lists sent to RT64
    inline std::atomic<uint32_t> audio_tasks{0};
    inline std::atomic<uint32_t> video_tasks{0};    // HVQM2 (skipped)
    inline std::atomic<uint32_t> retrace_waits{0};  // osViGetCurrentLine pairs
}

#endif
