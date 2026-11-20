/* Copyright (c) 2026 TileXR Project. */
/* Compares one SQ-sized copy across a 512-byte boundary with two split copies. */
#include "kernel_operator.h"

using namespace AscendC;

extern "C" __global__ __aicore__ void tilexr_mte3_probe(GM_ADDR input, GM_ADDR output)
{
    constexpr uint32_t stripe = 512, dstOffset = 384, first = stripe - dstOffset, bytes = 7 * 64;
    TPipe pipe;
    TBuf<QuePosition::VECCALC> data;
    pipe.InitBuffer(data, bytes);
    auto payload = data.Get<uint8_t>();
    GlobalTensor<uint8_t> src, dst;
    src.SetGlobalBuffer(input);
    dst.SetGlobalBuffer(output);
    DataCopy(payload, src, bytes);
    SetFlag<HardEvent::MTE2_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);
    SetFlag<HardEvent::S_MTE3>(EVENT_ID0);
    WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
    DataCopyPad(dst[dstOffset], payload, DataCopyExtParams(1, bytes, 0, 0, 0));
    DataCopyPad(dst[2 * stripe + dstOffset], payload, DataCopyExtParams(1, first, 0, 0, 0));
    DataCopyPad(dst[3 * stripe], payload[first], DataCopyExtParams(1, bytes - first, 0, 0, 0));
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}
