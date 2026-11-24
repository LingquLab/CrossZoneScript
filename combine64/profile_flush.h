/* Copyright (c) 2026 TileXR Project.
 * Flush a completed shared profiling lane before the SST kernel returns.
 * The immutable stage code writes records; this harness publishes their cache lines. */
#pragma once
#include <tilexr/profiling/types.h>
#include <tilexr/comm/udma/device/api.h>
__aicore__ inline void FlushProfileRange(uint64_t address, uint64_t bytes)
{
    __asm__ __volatile__("" ::: "memory");
    TileXR::UDMACleanCacheLines(reinterpret_cast<__gm__ uint8_t *>(address), bytes);
    __asm__ __volatile__("" ::: "memory");
}
__aicore__ inline void FlushProfile(GM_ADDR descriptor)
{
    if (descriptor == nullptr) return;
    const auto *view = reinterpret_cast<__gm__ const TileXR::Profiling::DeviceView *>(descriptor);
    const uint32_t lane = AscendC::GetBlockIdx();
    if (lane >= view->logicalStreamCount) return;
    const uint64_t statusOffset = uint64_t(lane) * view->stateStride * sizeof(uint32_t);
    const uint64_t countAddress = view->countsAddress + statusOffset;
    FlushProfileRange(countAddress, sizeof(uint32_t));
    const uint32_t count = ld_dev(reinterpret_cast<__gm__ uint32_t *>(countAddress), 0);
    if (count > view->recordsPerStream) return;
    const uint64_t recordOffset = uint64_t(lane) * view->recordStride;
    FlushProfileRange(view->timestampsAddress + recordOffset * sizeof(uint64_t), uint64_t(count) * sizeof(uint64_t));
    FlushProfileRange(view->eventIdsAddress + recordOffset * sizeof(uint32_t), uint64_t(count) * sizeof(uint32_t));
    FlushProfileRange(view->droppedAddress + statusOffset, sizeof(uint32_t));
    FlushProfileRange(view->statesAddress + statusOffset, sizeof(uint32_t));
}
