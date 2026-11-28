/* Copyright (c) 2026 TileXR Project.
 * Compare synchronization schedules with the same native BF16 token worker.
 */
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
    uint32_t result = FinalReduceSuccess;
    if (config.reserved == 1 || config.reserved == 2) {
        if (config.reserved == 2 && !worker.WaitWindow(SyncStepCount(config) - 1, 0, config.chunks - 1)) {
            result = FinalReduceTimeout;
        } else {
            result = worker.ReduceTokens(config.tokens * block / blocks, config.tokens * (block + 1U) / blocks);
        }
    } else if (config.reserved == 3) {
        uint32_t firstToken = 0;
        for (uint32_t first = 0; first < config.chunks && result == FinalReduceSuccess;) {
            const uint32_t last = SyncStepEnd(config, first);
            const uint32_t lastToken = ChunkEnd(config, last);
            const uint32_t tokens = lastToken - firstToken;
            const uint32_t begin = firstToken + tokens * block / blocks;
            const uint32_t end = firstToken + tokens * (block + 1) / blocks;
            if (begin < end) {
                result = worker.ReduceTokens(begin, end);
            }
            firstToken = lastToken;
            first = last + 1;
        }
    } else {
        result = ReduceWindows(config, block, blocks, worker);
    }
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
