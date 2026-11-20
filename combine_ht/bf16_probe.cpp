/* Copyright (c) 2026 TileXR Project. */
/* Isolates BF16 copy, conversion and addition in the SST perf model. */
#include "kernel_operator.h"

using namespace AscendC;

extern "C" __global__ __aicore__ void tilexr_bf16_probe(GM_ADDR input, GM_ADDR output)
{
    constexpr uint32_t count = 256;
    TPipe pipe;
    TBuf<QuePosition::VECCALC> aBuf, bBuf, sumBuf, scratchBuf, resultBuf;
    pipe.InitBuffer(aBuf, count * sizeof(bfloat16_t));
    pipe.InitBuffer(bBuf, count * sizeof(bfloat16_t));
    pipe.InitBuffer(sumBuf, count * sizeof(float));
    pipe.InitBuffer(scratchBuf, count * sizeof(float));
    pipe.InitBuffer(resultBuf, count * sizeof(bfloat16_t));
    auto a = aBuf.Get<bfloat16_t>();
    auto b = bBuf.Get<bfloat16_t>();
    auto sum = sumBuf.Get<float>();
    auto scratch = scratchBuf.Get<float>();
    auto result = resultBuf.Get<bfloat16_t>();
    GlobalTensor<bfloat16_t> source, outputBf16;
    GlobalTensor<float> outputFp32;
    source.SetGlobalBuffer(reinterpret_cast<__gm__ bfloat16_t *>(input));
    outputBf16.SetGlobalBuffer(reinterpret_cast<__gm__ bfloat16_t *>(output));
    outputFp32.SetGlobalBuffer(reinterpret_cast<__gm__ float *>(output + 1024));
    DataCopy(a, source, count);
    DataCopy(b, source[count], count);
    SetFlag<HardEvent::MTE2_V>(EVENT_ID0);
    WaitFlag<HardEvent::MTE2_V>(EVENT_ID0);
    Cast(sum, a, RoundMode::CAST_NONE, count);
    Cast(scratch, b, RoundMode::CAST_NONE, count);
    PipeBarrier<PIPE_V>();
    Add(sum, sum, scratch, count);
    PipeBarrier<PIPE_V>();
    Cast(result, sum, RoundMode::CAST_RINT, count);
    SetFlag<HardEvent::V_MTE3>(EVENT_ID0);
    WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);
    DataCopy(outputBf16, a, count);
    DataCopy(outputBf16[count], result, count);
    DataCopy(outputFp32, sum, count);
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}
