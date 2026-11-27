#include "combine_ht_kernel.h"

static_assert(TileXREpHt::kElementBytes == 2 && TileXRCombineHT::kRowElementBytes == 2);

extern "C" __global__ __aicore__ void tilexr_probe_gateway_reduce(TileXRCombineHT::PipelineArgs args)
{
    const auto &local = args.local;
    const auto &send = args.send;
    const uint32_t reducer = GetBlockIdx();
    TileXR::Profiling::DirectWriter writer;
    writer.Initialize(local.profiling, reducer);
    ReduceView view{
        local.workspace, local.input, local.forward, local.ranges, local.masks,
        local.config, local.layout, local.config.world, local.layout.chunks, reducer,
        reinterpret_cast<GM_ADDR>(send.workspace + send.config.partialOffset),
        reinterpret_cast<GM_ADDR>(send.workspace + send.config.readyOffset),
        send.config.magic, send.config.waitIterations};
    const uint32_t result = GatewayReduce::Run(view, writer, local.profiling != nullptr);
    TPipe pipe;
    TBuf<QuePosition::VECCALC> status;
    pipe.InitBuffer(status, TileXRCombineHT::kUbAlign);
    auto value = status.Get<uint32_t>();
    value.SetValue(0, result);
    NativeStore(reinterpret_cast<__gm__ uint32_t *>(local.status) + args.cores.gatewayReduceBegin + reducer, value, 1);
    writer.Publish();
}

extern "C" __global__ __aicore__ void tilexr_probe_final_reduce(TileXRCombineHT::PipelineArgs args)
{
    TileXREpHt::FinalReduceDevice::Run(
        reinterpret_cast<GM_ADDR>(args.send.workspace), reinterpret_cast<GM_ADDR>(args.serverMask),
        reinterpret_cast<GM_ADDR>(args.send.workspace + args.localReadyOffset),
        reinterpret_cast<GM_ADDR>(args.output), reinterpret_cast<GM_ADDR>(args.finalStatus),
        args.send.config, GetBlockIdx(), args.finalBlocks, args.local.profiling);
}
