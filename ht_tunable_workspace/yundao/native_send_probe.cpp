/* Copyright (c) 2026 TileXR Project.
 * Feed registered same-host peers to the production native send transport.
 */
#include "inter_server_send_worker.h"

extern "C" __global__ __aicore__ void tilexr_ht_ep8_send_probe(GM_ADDR comm, GM_ADDR workspace,
    GM_ADDR ranges, GM_ADDR status, TileXREpHt::SendConfig config)
{
    using namespace TileXREpHt;
    using namespace TileXREpHt::InterServerSendDevice;
    const uint32_t owner = AscendC::GetBlockIdx();
    TPipe pipe;
    DeviceTransport transport(comm, workspace, nullptr, nullptr, ranges, config, owner, pipe);
    uint32_t result = Success;
    for (uint32_t peer = 0, slot = 0; peer < kRanksPerServer; ++peer) {
        if (peer != config.rank) {
            transport.LoadRange(peer, slot++, 0);
        }
    }
    const uint32_t rowsPerOwner = config.tokens / (kRanksPerServer * SendCoreCount(config));
    for (uint32_t row = 0; row < rowsPerOwner && result == Success; ++row) {
        for (uint32_t peer = 0; peer < kRanksPerServer && result == Success; ++peer) {
            if (peer != config.rank) {
                const uint32_t token = config.rank * SendCoreCount(config) * rowsPerOwner + owner * rowsPerOwner + row;
                result = transport.Add(peer, row % 4 == 3, {0, int32_t(token), int32_t(token), 0, 0, 0});
            }
        }
    }
    if (result == Success) {
        result = transport.Flush();
    }
    for (uint32_t peer = 0; peer < kRanksPerServer && result == Success; ++peer) {
        if (peer == config.rank) {
            continue;
        }
        for (uint32_t lane = 0; lane < kLanes && result == Success; ++lane) {
            const auto offset = config.completionOffset +
                ((config.rank * SendCoreCount(config) + owner) * kLanes + lane) * kSlotBytes;
            result = transport.NotifyAt(peer, lane, offset);
        }
    }
    const uint32_t drained = transport.Drain();
    if (result == Success) {
        result = drained;
    }
    TBuf<QuePosition::VECCALC> buffer;
    pipe.InitBuffer(buffer, 32U);
    auto local = buffer.Get<uint32_t>();
    local.SetValue(0, result);
    GlobalTensor<uint32_t> output;
    output.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(status) + owner * 8);
    SetFlag<HardEvent::S_MTE3>(EVENT_ID2);
    WaitFlag<HardEvent::S_MTE3>(EVENT_ID2);
    DataCopyPad(output, local, DataCopyExtParams(1, 4, 0, 0, 0));
    SetFlag<HardEvent::MTE3_S>(EVENT_ID2);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID2);
}
