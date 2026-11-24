// Diagnostic only: qualify the V1.1.0 model's B32 st_dev address units.
#include "kernel_operator.h"
using namespace AscendC;
struct ProbeArgs { __gm__ uint8_t *destination; uint32_t rank; };
extern "C" __global__ __aicore__ void tilexr_moonep_combine_ht_kernel(ProbeArgs args)
{
    if (args.rank || GetBlockIdx()) return;
    const uint64_t address = reinterpret_cast<uint64_t>(args.destination);
    st_dev(uint32_t(0x13579bdfU), reinterpret_cast<__gm__ uint32_t *>(address >> 3), 0);
    st_dev(uint32_t(0x2468ace0U), reinterpret_cast<__gm__ uint32_t *>((address + 512) >> 3), 0);
    st_dev(uint32_t(0x31415926U), reinterpret_cast<__gm__ uint32_t *>(address + 1024), 0);
    TPipe pipe;
    TBuf<QuePosition::VECCALC> scratch;
    pipe.InitBuffer(scratch, 32);
    auto ub = scratch.Get<uint32_t>();
    ub.SetValue(0, 0x12345678U);
    GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(address + 1536));
    SetFlag<HardEvent::S_MTE3>(EVENT_ID0);
    WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
    DataCopyPad(gm, ub, DataCopyExtParams{1, 4, 0, 0, 0});
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}
