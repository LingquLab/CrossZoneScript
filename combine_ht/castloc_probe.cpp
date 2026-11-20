/* Copyright (c) 2026 TileXR Project. */
/* Captures BF16 UB bits with scalar reads before publishing the same tile through MTE3. */
#include "kernel_operator.h"

using namespace AscendC;

namespace {
constexpr uint32_t kElements = 256;
constexpr uint32_t kBf16Bytes = kElements * sizeof(bfloat16_t);
constexpr uint32_t kFp32Bytes = kElements * sizeof(float);

__aicore__ inline void CaptureBits(const LocalTensor<bfloat16_t>& bf16, LocalTensor<uint32_t>& snapshot)
{
    const auto bits = bf16.ReinterpretCast<uint16_t>();
    for (uint32_t i = 0; i < kElements; ++i) {
        snapshot.SetValue(i, ((0xA500U + i) << 16U) | uint32_t(bits.GetValue(i)));
    }
}
} // namespace

extern "C" __global__ __aicore__ void tilexr_castloc_probe(GM_ADDR input, GM_ADDR output)
{
    TPipe pipe;
    TBuf<QuePosition::VECCALC> fp32Buf, bf16Buf, snapshotBuf;
    pipe.InitBuffer(fp32Buf, kFp32Bytes);
    pipe.InitBuffer(bf16Buf, kBf16Bytes);
    pipe.InitBuffer(snapshotBuf, kFp32Bytes);
    auto fp32 = fp32Buf.Get<float>();
    auto bf16 = bf16Buf.Get<bfloat16_t>();
    auto snapshot = snapshotBuf.Get<uint32_t>();

    GlobalTensor<float> fp32Input, fp32Output;
    GlobalTensor<bfloat16_t> bf16Input, bf16Output;
    GlobalTensor<uint32_t> scalarOutput;
    fp32Input.SetGlobalBuffer(reinterpret_cast<__gm__ float *>(input));
    bf16Input.SetGlobalBuffer(reinterpret_cast<__gm__ bfloat16_t *>(input + kFp32Bytes));
    bf16Output.SetGlobalBuffer(reinterpret_cast<__gm__ bfloat16_t *>(output));
    scalarOutput.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(output + 2 * kBf16Bytes));
    fp32Output.SetGlobalBuffer(reinterpret_cast<__gm__ float *>(output + 2 * kBf16Bytes + 2 * kFp32Bytes));

    DataCopy(fp32, fp32Input, kElements);
    DataCopy(bf16, bf16Input, kElements);
    SetFlag<HardEvent::MTE2_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);
    SetFlag<HardEvent::MTE2_V>(EVENT_ID0);
    WaitFlag<HardEvent::MTE2_V>(EVENT_ID0);
    CaptureBits(bf16, snapshot);
    SetFlag<HardEvent::S_MTE3>(EVENT_ID0);
    WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
    DataCopy(bf16Output, bf16, kElements);
    DataCopy(scalarOutput, snapshot, kElements);
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);

    SetFlag<HardEvent::S_V>(EVENT_ID0);
    WaitFlag<HardEvent::S_V>(EVENT_ID0);
    Cast(bf16, fp32, RoundMode::CAST_RINT, kElements);
    PipeBarrier<PIPE_V>();
    SetFlag<HardEvent::V_S>(EVENT_ID0);
    WaitFlag<HardEvent::V_S>(EVENT_ID0);
    CaptureBits(bf16, snapshot);
    SetFlag<HardEvent::V_MTE3>(EVENT_ID0);
    WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);
    SetFlag<HardEvent::S_MTE3>(EVENT_ID0);
    WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
    DataCopy(bf16Output[kElements], bf16, kElements);
    DataCopy(scalarOutput[kElements], snapshot, kElements);
    DataCopy(fp32Output, fp32, kElements);
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}
