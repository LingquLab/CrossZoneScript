/* Copyright (c) 2026 TileXR Project.
 * Measure a known timer interval so SST simulation-time differences identify its scale.
 * Each rank runs one AIV and owns its result and shared profiling lane. */
#include "kernel_operator.h"
#include <tilexr/profiling/device/direct_writer.h>
#include "profile_flush.h"
extern "C" __global__ __aicore__ void tilexr_ht_clock_probe(GM_ADDR result, GM_ADDR profiling, uint64_t waitTicks)
{
    TileXR::Profiling::DirectWriter capture;
    capture.Initialize(profiling, 0);
    const uint64_t begin = AscendC::GetSystemCycle();
    uint64_t end;
    do { end = AscendC::GetSystemCycle(); } while (end - begin < waitTicks);
    capture.RecordAt(1, begin);
    capture.RecordAt(2, end);
    capture.Publish();
    auto *words = reinterpret_cast<__gm__ uint64_t *>(result);
    words[0] = begin;
    words[1] = end;
    words[2] = end - begin;
    words[3] = 0;
    FlushProfileRange(reinterpret_cast<uint64_t>(result), 32);
    FlushProfile(profiling);
}
