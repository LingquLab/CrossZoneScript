/* Copyright (c) 2026 TileXR Project. */
/* Calls the pinned SIMD/SIMT WQE builder with the production event and a V-to-S control. */
#include "combine_ht_kernel.h"

using namespace AscendC;

extern "C" __global__ __aicore__ void tilexr_wqe_probe(GM_ADDR input, GM_ADDR output)
{
    constexpr uint32_t rows = 7, descriptorBytes = 64, rowBytes = 512;
    TPipe pipe;
    TBuf<QuePosition::VECCALC> headerBuf, recordsBuf, wqeBuf;
    pipe.InitBuffer(headerBuf, descriptorBytes);
    pipe.InitBuffer(recordsBuf, 192);
    pipe.InitBuffer(wqeBuf, rows * descriptorBytes);
    auto header = headerBuf.Get<uint32_t>();
    auto records = recordsBuf.Get<int64_t>();
    auto wqes = wqeBuf.Get<uint8_t>();
    GlobalTensor<int64_t> source;
    GlobalTensor<uint8_t> dest;
    source.SetGlobalBuffer(reinterpret_cast<__gm__ int64_t *>(input));
    dest.SetGlobalBuffer(output);
    DataCopy(records, source, 24);
    SetFlag<HardEvent::MTE2_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);
    Duplicate(header, uint32_t(0), descriptorBytes / sizeof(uint32_t));
    SetFlag<HardEvent::V_S>(EVENT_ID0);
    WaitFlag<HardEvent::V_S>(EVENT_ID0);
    auto *sqe = reinterpret_cast<__ubuf__ TileXR::UDMASqeCtx *>(header.GetPhyAddr());
    sqe->opcode = 3;
    sqe->sgeNum = 1;
    for (uint32_t trial = 0; trial < 2; ++trial) {
        SetFlag<HardEvent::S_V>(EVENT_ID0);
        WaitFlag<HardEvent::S_V>(EVENT_ID0);
        Simt::VF_CALL<BuildPutWqesVf<false>>(Simt::Dim3{kBuilderThreads, 1, 1},
            reinterpret_cast<__ubuf__ uint64_t *>(wqes.GetPhyAddr()),
            reinterpret_cast<__ubuf__ uint64_t *>(header.GetPhyAddr()),
            reinterpret_cast<__ubuf__ int64_t *>(records.GetPhyAddr()),
            rows, 6 + trial * 8, 0x200000000ULL, 0x210000000ULL, rowBytes, 0, rows);
        if (trial == 1) {
            SetFlag<HardEvent::V_S>(EVENT_ID0);
            WaitFlag<HardEvent::V_S>(EVENT_ID0);
        }
        SetFlag<HardEvent::V_MTE3>(EVENT_ID0);
        WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);
        SetFlag<HardEvent::S_MTE3>(EVENT_ID0);
        WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
        DataCopyPad(dest[trial * 512], wqes, DataCopyExtParams(1, rows * descriptorBytes, 0, 0, 0));
        SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
        WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
    }
}
