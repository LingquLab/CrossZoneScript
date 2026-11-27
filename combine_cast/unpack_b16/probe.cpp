#include "kernel_operator.h"

using namespace AscendC;
namespace Reg = AscendC::MicroAPI;

namespace {
constexpr uint32_t kWords = 64;
constexpr uint32_t kInputElements = 2 * kWords;

template <typename T, Reg::LoadDist Dist>
__simd_vf__ void Capture(__ubuf__ T *input, __ubuf__ uint32_t *output)
{
    Reg::RegTensor<T> loaded;
    Reg::LoadAlign<T, Dist>(loaded, input);
    auto &words = reinterpret_cast<Reg::RegTensor<uint32_t> &>(loaded);
    uint32_t remaining = kWords;
    auto mask = Reg::UpdateMask<uint32_t>(remaining);
    Reg::StoreAlign(output, words, mask);
}
} // namespace

extern "C" __global__ __aicore__ void tilexr_probe_unpack_b16(GM_ADDR input, GM_ADDR output)
{
    TPipe pipe;
    TBuf<TPosition::VECCALC> inputBuffer, outputBuffer;
    pipe.InitBuffer(inputBuffer, kInputElements * sizeof(uint16_t));
    pipe.InitBuffer(outputBuffer, 3 * kWords * sizeof(uint32_t));

    GlobalTensor<uint16_t> inputGM;
    inputGM.SetGlobalBuffer(reinterpret_cast<__gm__ uint16_t *>(input));
    auto inputUB = inputBuffer.Get<uint16_t>();
    DataCopy(inputUB, inputGM, kInputElements);
    SetFlag<HardEvent::MTE2_V>(EVENT_ID0);
    WaitFlag<HardEvent::MTE2_V>(EVENT_ID0);

    auto outputUB = outputBuffer.Get<uint32_t>();
    auto *bf16 = reinterpret_cast<__ubuf__ bfloat16_t *>(inputUB.GetPhyAddr());
    auto *u16 = reinterpret_cast<__ubuf__ uint16_t *>(inputUB.GetPhyAddr());
    auto *out = reinterpret_cast<__ubuf__ uint32_t *>(outputUB.GetPhyAddr());
    asc_vf_call<Capture<bfloat16_t, Reg::LoadDist::DIST_NORM>>(bf16, out);
    asc_vf_call<Capture<bfloat16_t, Reg::LoadDist::DIST_UNPACK_B16>>(bf16, out + kWords);
    asc_vf_call<Capture<uint16_t, Reg::LoadDist::DIST_UNPACK_B16>>(u16, out + 2 * kWords);
    SetFlag<HardEvent::V_MTE3>(EVENT_ID0);
    WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);

    GlobalTensor<uint32_t> outputGM;
    outputGM.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(output));
    DataCopy(outputGM, outputUB, 3 * kWords);
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}
