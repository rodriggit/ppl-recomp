// libultra functions the game calls that N64Recomp ignores and the runtime doesn't define,
// and the port's versions of the ones it defines unusably (tools/gen_config.py PORT_REPLACED).
#include <atomic>
#include <vector>

#include <ultramodern/ultra64.h>
#include <ultramodern/ultramodern.hpp>
#include "recomp.h"
#include "ppl_trace.h"

extern "C" void yield_self_1ms(uint8_t* rdram);   // ultramodern/src/scheduling.cpp

// VIs since start, counted by the runtime's VI thread through the port's vi_callback.
std::atomic<uint64_t> ppl_vi_count{0};

// osViGetCurrentLine: every call site is a retrace wait, `while (line != 0)` then
// `while (line == 0)` (docs/phase2.md). The first call of a pair waits for the next VI and
// returns line 0; the second returns a non-zero line. Waiting yields every millisecond so
// the other game threads keep running, as they would during the original busy wait.
// Game threads run one at a time, so one pair state for all of them is enough.
extern "C" void osViGetCurrentLine_recomp(uint8_t* rdram, recomp_context* ctx) {
    static bool in_retrace = false;
    if (!in_retrace) {
        const uint64_t start = ppl_vi_count.load();
        while (ppl_vi_count.load() == start) {
            yield_self_1ms(rdram);
        }
        in_retrace = true;
        ppl::trace::retrace_waits++;
        ctx->r2 = 0;
    } else {
        in_retrace = false;
        ctx->r2 = 1;
    }
}

// osYieldThread: the HVQM2 player calls it in a loop until the timekeeper thread, which has a
// higher priority, frees a frame buffer (func_8003E6B8). On the console the timekeeper runs
// when its interrupt arrives; here, wait up to 1 ms for an event and let a woken thread run.
extern "C" void ppl_osYieldThread(uint8_t* rdram, recomp_context* ctx) {
    yield_self_1ms(rdram);
}

// osStopThread: the runtime only lets a thread stop itself. The HVQM2 player stops the
// scheduler thread, blocked in osRecvMesg, for the length of a video and restarts it after
// (func_8003F810). The runtime's osStartThread then queued it as runnable while it was still
// on the message queue's list, and both lists share the thread's `next`: the scheduler never
// woke up again. Take the thread out of the queue it is in; once restarted, it resumes in
// osRecvMesg's wait loop and blocks again while there is no message, as on the console.
extern "C" void ppl_osStopThread(uint8_t* rdram, recomp_context* ctx) {
    const PTR(OSThread) t_ = (int32_t)ctx->r4;
    if (t_ == NULLPTR || t_ == ultramodern::this_thread()) {
        osStopThread(rdram, t_);
        return;
    }
    OSThread* t = TO_PTR(OSThread, t_);
    const PTR(PTR(OSThread)) queue = t->queue;
    if (queue != NULLPTR) {
        // The runtime's thread_queue_remove only finds the first thread of a queue, so
        // rebuild the queue without t. Reinserting in reverse keeps equal priorities in order.
        std::vector<PTR(OSThread)> others;
        while (!ultramodern::thread_queue_empty(rdram, queue)) {
            const PTR(OSThread) cur = ultramodern::thread_queue_pop(rdram, queue);
            if (cur != t_) {
                others.push_back(cur);
            }
        }
        for (auto it = others.rbegin(); it != others.rend(); ++it) {
            ultramodern::thread_queue_insert(rdram, queue, *it);
        }
    }
    t->state = STOPPED;
}
