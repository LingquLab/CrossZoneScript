#include "final_reduce_worker.h"

using namespace AscendC;
using namespace TileXREpHt;

extern "C" __global__ __aicore__ void tilexr_probe_final_reduce_bench(
    GM_ADDR workspace, GM_ADDR masks, GM_ADDR ready, GM_ADDR output, GM_ADDR status, SendConfig config)
{
    const uint32_t block = GetBlockIdx();
    const uint32_t blocks = GetBlockNum();
    TPipe pipe;
    FinalReduceDevice::FinalReduceWorker worker(workspace, masks, ready, output, config, pipe);
    // Bench-only selector. Both paths run the same token worker and drain.
    const uint32_t result = config.reserved
        ? worker.ReduceTokens(config.tokens * block / blocks, config.tokens * (block + 1U) / blocks)
        : ReduceWindows(config, block, blocks, worker);
    worker.Drain();
    TBuf<TPosition::VECCALC> statusBuffer;
    pipe.InitBuffer(statusBuffer, 32);
    auto local = statusBuffer.Get<uint32_t>();
    local.SetValue(0, result);
    GlobalTensor<uint32_t> statusGm;
    statusGm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(status) + block * kFinalReduceStatusStride);
    FinalReduceDevice::Sync<HardEvent::S_MTE3>();
    DataCopyPad(statusGm, local, DataCopyExtParams(1, sizeof(uint32_t), 0, 0, 0));
    FinalReduceDevice::Sync<HardEvent::MTE3_S>();
}
