/* Copyright (c) 2026 TileXR Project. */
/* Compares direct GM markers, compact and strided UB stores, and scalar UB readback per SIMT lane. */
#include "kernel_operator.h"
#include "simt_api/device_functions.h"

using namespace AscendC;

namespace {
constexpr uint32_t kObservedLanes = 8;
constexpr uint32_t kBytesPerRegion = 512;
constexpr uint32_t kRegionsPerTrial = 12;
constexpr uint32_t kUbFill = 0xDEADBEEF;
constexpr uint32_t kMarker32 = 0x1A2B0000;
constexpr uint64_t kMarker64 = 0x5A70616101000000ULL;
constexpr uint64_t kGmMarker = 0x7B70616102000000ULL;

__simt_vf__ __aicore__ LAUNCH_BOUND(128) inline void ObserveLane(
    __ubuf__ uint32_t *packed, __ubuf__ uint64_t *strided, __gm__ uint64_t *markers)
{
    const uint32_t lane = threadIdx.x;
    if (lane < kObservedLanes) {
        packed[lane] = kMarker32 + lane;
        for (uint32_t word = 0; word < 8; ++word) {
            strided[lane * 8 + word] = kMarker64 + lane * 8 + word;
        }
        asc_stcg(markers + lane * (kBytesPerRegion / sizeof(uint64_t)), kGmMarker + lane);
    }
    asc_threadfence();
}
} // namespace

extern "C" __global__ __aicore__ void tilexr_lane_probe(GM_ADDR, GM_ADDR output)
{
    TPipe pipe;
    TBuf<QuePosition::VECCALC> packedBuf, stridedBuf, readbackBuf;
    pipe.InitBuffer(packedBuf, kBytesPerRegion);
    pipe.InitBuffer(stridedBuf, kBytesPerRegion);
    pipe.InitBuffer(readbackBuf, kBytesPerRegion);
    auto packed = packedBuf.Get<uint32_t>();
    auto strided = stridedBuf.Get<uint64_t>();
    auto readback = readbackBuf.Get<uint64_t>();
    GlobalTensor<uint8_t> destination;
    destination.SetGlobalBuffer(output);
    constexpr uint32_t widths[] = {2, 8, 32, 128};
    for (uint32_t trial = 0; trial < sizeof(widths) / sizeof(widths[0]); ++trial) {
        const uint32_t offset = trial * kRegionsPerTrial * kBytesPerRegion;
        Duplicate(packed, kUbFill, kBytesPerRegion / sizeof(uint32_t));
        Duplicate(stridedBuf.Get<uint32_t>(), kUbFill, kBytesPerRegion / sizeof(uint32_t));
        SetFlag<HardEvent::V_S>(EVENT_ID0);
        WaitFlag<HardEvent::V_S>(EVENT_ID0);
        SetFlag<HardEvent::S_V>(EVENT_ID0);
        WaitFlag<HardEvent::S_V>(EVENT_ID0);
        Simt::VF_CALL<ObserveLane>(Simt::Dim3{widths[trial], 1, 1},
            reinterpret_cast<__ubuf__ uint32_t *>(packed.GetPhyAddr()),
            reinterpret_cast<__ubuf__ uint64_t *>(strided.GetPhyAddr()),
            reinterpret_cast<__gm__ uint64_t *>(output + offset + 4 * kBytesPerRegion));
        SetFlag<HardEvent::V_S>(EVENT_ID0);
        WaitFlag<HardEvent::V_S>(EVENT_ID0);
        for (uint32_t word = 0; word < kBytesPerRegion / sizeof(uint64_t); ++word) {
            readback.SetValue(word, strided.GetValue(word));
        }
        SetFlag<HardEvent::V_MTE3>(EVENT_ID0);
        WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);
        SetFlag<HardEvent::S_MTE3>(EVENT_ID0);
        WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
        const DataCopyExtParams copy(1, kBytesPerRegion, 0, 0, 0);
        DataCopyPad(destination[offset], packedBuf.Get<uint8_t>(), copy);
        DataCopyPad(destination[offset + kBytesPerRegion], stridedBuf.Get<uint8_t>(), copy);
        DataCopyPad(destination[offset + 2 * kBytesPerRegion], readbackBuf.Get<uint8_t>(), copy);
        SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
        WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
    }
}
