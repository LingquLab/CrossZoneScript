/* Copyright (c) 2026 TileXR Project.
 * Flushes the native profiling records to GM for the SST dump.
 */
#pragma once

#include "kernel_operator.h"
#include <tilexr/comm/udma/device/api.h>
#include <tilexr/profiling/device/direct_writer.h>

namespace HtSstProfile {
__aicore__ inline void Flush(GM_ADDR descriptor, uint32_t lane)
{
    const auto *view = reinterpret_cast<__gm__ const TileXR::Profiling::DeviceView *>(descriptor);
    const uint64_t records = uint64_t(lane) * view->recordStride;
    const uint64_t state = uint64_t(lane) * view->stateStride * sizeof(uint32_t);
    __asm__ __volatile__("" ::: "memory");
    const auto *counts = reinterpret_cast<__gm__ const uint32_t *>(view->countsAddress + state);
    const uint32_t count = *counts;
    TileXR::UDMACleanCacheLines(reinterpret_cast<GM_ADDR>(view->timestampsAddress + records * sizeof(uint64_t)),
        uint64_t(count) * sizeof(uint64_t));
    TileXR::UDMACleanCacheLines(reinterpret_cast<GM_ADDR>(view->eventIdsAddress + records * sizeof(uint32_t)),
        uint64_t(count) * sizeof(uint32_t));
    TileXR::UDMACleanCacheLines(reinterpret_cast<GM_ADDR>(view->countsAddress + state), sizeof(uint32_t));
    TileXR::UDMACleanCacheLines(reinterpret_cast<GM_ADDR>(view->droppedAddress + state), sizeof(uint32_t));
    TileXR::UDMACleanCacheLines(reinterpret_cast<GM_ADDR>(view->statesAddress + state), sizeof(uint32_t));
    __asm__ __volatile__("" ::: "memory");
}
} // namespace HtSstProfile
