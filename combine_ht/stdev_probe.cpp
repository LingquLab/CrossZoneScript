// Isolated CANN/SST instruction probe; no communication or production source edits.
#include "kernel_operator.h"
using namespace AscendC;
struct ProbeArgs { __gm__ uint8_t *destination; uint32_t rank; };
extern "C" __global__ __aicore__ void tilexr_moonep_combine_ht_kernel(ProbeArgs args)
{
    if (args.rank || GetBlockIdx()) return;
    st_dev(uint32_t(0x13579bdfU), reinterpret_cast<__gm__ uint32_t *>(args.destination), 0);
    st_dev(uint64_t(0x2468ace013579bdfULL), reinterpret_cast<__gm__ uint64_t *>(args.destination + 512), 0);
    st_dev(uint8_t(0x5aU), reinterpret_cast<__gm__ uint8_t *>(args.destination + 1024), 0);
    TPipe pipe;
    TBuf<QuePosition::VECCALC> scratch;
    pipe.InitBuffer(scratch, 32);
    auto ub = scratch.Get<uint32_t>();
    ub.SetValue(0, 0x13579bdfU);
    GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(args.destination + 1536));
    SetFlag<HardEvent::S_MTE3>(EVENT_ID0);
    WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
    DataCopyPad(gm, ub, DataCopyExtParams{1, 4, 0, 0, 0});
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}
