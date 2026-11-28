/* Copyright (c) 2026 TileXR Project.
 * Reuse the HT fixture and pipeline binary without its cross-server stages.
 */
#include "local_return_args.h"
#include "runtime/kernel.h"
#include <fstream>
#include <iterator>
#include <set>

#define main tilexr_combine_ht_fixture_main
#include "integration.cpp"
#undef main

namespace {

struct RegisteredKernel {
    RegisteredKernel(const std::string &path, const char *name)
    {
        std::ifstream file(path, std::ios::binary);
        bytes.assign(std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>());
        Require(!bytes.empty(), "binary unavailable");
        rtDevBinary_t binary {};
        binary.magic = 0x41415246U;
        binary.data = bytes.data();
        binary.length = bytes.size();
        Check(rtDevBinaryRegister(&binary, &handle), "binary register");
        Check(rtFunctionRegister(handle, &signature, name, name, 0), "function register");
    }
    template <typename Args>
    void Launch(Args &args, uint32_t blocks, aclrtStream stream)
    {
        rtArgsEx_t launch {};
        launch.args = &args;
        launch.argsSize = sizeof(args);
        rtTaskCfgInfo_t task {};
        task.schemMode = 1;
        task.localMemorySize = TileXRCombineHT::kUbBytes;
        Check(rtKernelLaunchWithFlagV2(&signature, blocks, &launch, nullptr, stream, 0, &task), "kernel launch");
    }
    std::vector<char> bytes;
    void *handle = nullptr;
    uint8_t signature = 0;
};

TileXR::UDMAInfo LoadInfo(GM_ADDR addr)
{
    TileXR::UDMAInfo info {};
    Check(aclrtMemcpy(&info, sizeof(info), addr, sizeof(info), ACL_MEMCPY_DEVICE_TO_HOST), "SQ info");
    return info;
}

uint64_t LoadSq(const TileXR::UDMAInfo &info, uint32_t peer, uint32_t qp)
{
    Require(info.sqPtr != 0 && qp < info.qpNum, "SQ index");
    TileXR::UDMAWQCtx sq {};
    const auto index = uint64_t(peer) * info.qpNum + qp;
    const auto address = reinterpret_cast<void *>(info.sqPtr + index * sizeof(sq));
    Check(aclrtMemcpy(&sq, sizeof(sq), address, sizeof(sq), ACL_MEMCPY_DEVICE_TO_HOST), "SQ read");
    Require(sq.bufAddr && sq.headAddr && sq.dbAddr, "SQ context");
    return sq.bufAddr;
}

void CheckDistinctSq(const TileXRCombineHT::PipelineArgs &args, uint32_t rank, uint32_t closSlots)
{
    const auto fullmesh = LoadInfo(args.local.fullmesh_info);
    const auto shared = LoadInfo(reinterpret_cast<GM_ADDR>(args.localReturnInfo));
    std::set<uint64_t> gather;
    std::set<uint64_t> direct;
    std::set<uint64_t> clos;
    for (uint32_t peer = 0; peer < 8; ++peer) {
        if (peer == rank) {
            continue;
        }
        gather.insert(LoadSq(fullmesh, peer, peer));
        direct.insert(LoadSq(shared, peer, TileXREpHt::LocalReturnPeerQp(args.localQpBase, 1, true, rank, peer)));
        for (uint32_t qp = 0; qp < closSlots; ++qp) {
            clos.insert(LoadSq(shared, peer, qp));
        }
    }
    Require(gather.size() == 7 && direct.size() == 7 && clos.size() == closSlots, "unique queue counts");
    for (auto address : direct) {
        Require(!gather.count(address) && !clos.count(address), "direct SQ aliases Gather or CLOS");
    }
    for (auto address : gather) {
        Require(!clos.count(address), "Gather SQ aliases CLOS");
    }
}

void Barrier(const char *name, uint32_t round)
{
    std::cout << name << ' ' << round << std::endl;
    std::string release;
    Require(bool(std::getline(std::cin, release)) && release == "go", "host barrier");
}

void RunLocalPipeline(uint32_t rank, uint32_t tokens, uint32_t sync, bool concurrent,
                      const std::string &pipelineBinary, const std::string &localBinary)
{
    using namespace TileXREpHt;
    TileXRCombineHTConfig config {rank, 64, tokens, 128, 4, 0, 0, 256, FirstSplits(tokens),
        16, 48, TILEXR_COMBINE_HT_SEND_REDUCE, TILEXR_COMBINE_HT_CONTRIBUTORS};
    Fixture fixture(config);
    const auto &c = fixture.config;
    TileXRCombineHTExecutionOptions options {};
    options.send_core_count = 8;
    options.rotation_policy = PeerRotationPolicy::ServerStaggered;
    options.sync_granularity = sync;
    Check(aclInit(nullptr), "ACL init");
    Check(aclrtSetDevice(rank), "set device");
    aclrtStream stream = nullptr;
    aclrtEvent start = nullptr, stop = nullptr;
    Check(aclrtCreateStream(&stream), "create stream");
    Check(aclrtCreateEvent(&start), "create start");
    Check(aclrtCreateEvent(&stop), "create stop");
    TileXRCommPtr comm = nullptr;
    Check(TileXRCommInitRankWithSharedQpDomain(0, 8, rank, &comm), "communicator");
    {
        TileXRCombineHTPipelineLayout layout {};
        Check(TileXRMoonEpCombineHTGetPipelineLayoutWithOptions(&c, 1, &options, &layout), "layout");
        constexpr size_t guard = 512;
        DeviceBuffer workspace(layout.workspace_bytes + guard);
        auto *base = static_cast<GM_ADDR>(workspace.data);
        DeviceBuffer forward(fixture.forward.size() * 4), counts(fixture.counts.size() * 4),
            ranges(fixture.ranges.size() * 4), masks(fixture.masks.size() * 4),
            backward(fixture.backward.size() * 8), backwardCounts(sizeof(fixture.backwardCounts)),
            backwardRanges(fixture.backwardRanges.size() * 4), localStatus(64 * 4), returnStatus(8 * 32);
        forward.Upload(fixture.forward.data());
        counts.Upload(fixture.counts.data());
        ranges.Upload(fixture.ranges.data());
        masks.Upload(fixture.masks.data());
        backward.Upload(fixture.backward.data());
        backwardCounts.Upload(fixture.backwardCounts.data());
        backwardRanges.Upload(fixture.backwardRanges.data());
        TileXRUDMAMemHandle handle = 0;
        Check(TileXRUDMARegister(comm, base, layout.workspace_bytes, &handle), "register");
        TileXR::CommArgs *host = nullptr;
        Check(TileXRGetCommArgsHost(comm, host), "host comm");
        TileXRCombineHT::PipelineArgs args {};
        auto &local = args.local;
        local.config = c;
        local.config.reduce_core_begin = concurrent ? 16 : 8;
        local.layout = layout.local;
        local.workspace = base + layout.local_offset;
        local.input = base + layout.send.inputOffset;
        local.forward = static_cast<GM_ADDR>(forward.data);
        local.forward_counts = static_cast<GM_ADDR>(counts.data);
        local.ranges = static_cast<GM_ADDR>(ranges.data);
        local.masks = static_cast<GM_ADDR>(masks.data);
        local.backward = static_cast<GM_ADDR>(backward.data);
        local.backward_counts = static_cast<GM_ADDR>(backwardCounts.data);
        local.backward_ranges = static_cast<GM_ADDR>(backwardRanges.data);
        local.status = static_cast<GM_ADDR>(localStatus.data);
        Check(TileXRCombineHT::ResolvePipelineLocal(local, comm, base, layout.local_offset, nullptr), "Gather fullmesh");
        args.send = {0, reinterpret_cast<uint64_t>(base), reinterpret_cast<uint64_t>(forward.data),
                     reinterpret_cast<uint64_t>(counts.data), reinterpret_cast<uint64_t>(ranges.data), 0, layout.send};
        args.returnStatus = reinterpret_cast<uint64_t>(returnStatus.data);
        args.localReadyOffset = layout.local_ready_offset;
        args.localSignalOffset = layout.local_signal_offset;
        args.localReturnInfo = reinterpret_cast<uint64_t>(host->udmaInfoPtr);
        uint32_t six = 0, two = 0, direct = 0;
        Check(TileXRUDMAGetSharedQpLayout(comm, &six, &two, &args.localQpBase, &direct), "direct layout");
        Require(direct == 7 && args.localQpBase == six + two, "direct slots");
        args.localQpStride = 1;
        args.localQpSkipSelf = 1;
        const uint32_t end = local.config.reduce_core_begin + c.reduce_core_count;
        args.cores = {8, 8, local.config.reduce_core_begin, 0, local.config.reduce_core_begin, 48, end, 0};
        CheckDistinctSq(args, rank, six + two);
        RegisteredKernel pipeline(pipelineBinary, "tilexr_moonep_combine_ht_pipeline_kernel");
        RegisteredKernel localReturn(localBinary, "tilexr_ep_ht_local_return_kernel");

        std::vector<uint8_t> input(workspace.bytes, 0xa5), actual(workspace.bytes);
        std::fill(input.begin(), input.end() - guard, 0);
        auto *image = input.data();
        std::memcpy(image + layout.send.inputOffset, fixture.input.data(), fixture.input.size() * sizeof(RowElement));
        std::fill_n(reinterpret_cast<RowElement *>(image + layout.local_offset + layout.local.gather_offset),
                    (layout.local.partial_offset - layout.local.gather_offset) / sizeof(RowElement), kGuard);
        std::fill_n(reinterpret_cast<RowElement *>(image + layout.send.partialOffset), fixture.expected.size(), kGuard);
        for (uint32_t round = 0; round < 4; ++round) {
            Check(TileXRCommNextMagic(comm, &args.send.config.magic), "round magic");
            workspace.Upload(input.data());
            Check(aclrtMemsetAsync(localStatus.data, localStatus.bytes, 0xa5, localStatus.bytes, stream), "local status");
            Check(aclrtMemsetAsync(returnStatus.data, returnStatus.bytes, 0xa5, returnStatus.bytes, stream), "return status");
            Check(TileXRMoonEpCombineHTPrepareInRegion(
                &local.config, base, layout.workspace_bytes, layout.local_offset, comm, stream), "prepare");
            Check(aclrtSynchronizeStream(stream), "prepare completion");
            Barrier("LAUNCH_READY", round);
            Check(aclrtRecordEvent(start, stream), "start");
            pipeline.Launch(args, end, stream);
            if (!concurrent) {
                LocalReturnArgs ret {args.localReturnInfo, reinterpret_cast<uint64_t>(local.registry),
                    args.send.workspace, args.send.forward, args.send.counts, args.send.ranges, args.returnStatus,
                    args.send.config, args.localReadyOffset, args.localSignalOffset, 0, args.localQpBase, 1, 1};
                localReturn.Launch(ret, 8, stream);
            }
            Check(aclrtRecordEvent(stop, stream), "stop");
            Check(aclrtSynchronizeStream(stream), "finish");
            float ms = 0;
            Check(aclrtEventElapsedTime(&ms, start, stop), "elapsed");
            Barrier("ROUND_READY", round);
            std::array<uint32_t, 64> statuses {}, returns {};
            localStatus.Download(statuses.data());
            returnStatus.Download(returns.data());
            for (uint32_t i = 0; i < 8; ++i) {
                Check(statuses[i], "Gather status");
                Check(returns[i * 8], "LocalReturn status");
            }
            for (uint32_t i = local.config.reduce_core_begin; i < end; ++i) {
                Check(statuses[i], "GatewayReduce status");
            }
            workspace.Download(actual.data());
            const auto *workspaceBytes = actual.data();
            const auto sentinel = [](uint8_t v) { return v == 0xa5; };
            Require(std::all_of(actual.end() - guard, actual.end(), sentinel), "workspace guards");
            fixture.CheckGather(reinterpret_cast<const RowElement *>(
                workspaceBytes + layout.local_offset + layout.local.gather_offset));
            Require(std::memcmp(workspaceBytes + layout.send.partialOffset, fixture.expected.data(),
                fixture.expected.size() * sizeof(RowElement)) == 0, "partial output");
            for (uint32_t token = 0; token < tokens; ++token) {
                const auto *row = reinterpret_cast<const RowElement *>(workspaceBytes + ReturnOffset(layout.send, token, 0));
                for (uint32_t h = 0; h < c.hidden; ++h) {
                    Require(row[h] == ServerResult(fixture, rank, token, 0, h), "LocalReturn payload");
                }
            }
            for (uint32_t gateway = 0; gateway < 8; ++gateway) {
                for (uint32_t step = 0; step < SyncStepCount(layout.send); ++step) {
                    int64_t flag = 0;
                    std::memcpy(&flag, workspaceBytes + layout.local_ready_offset +
                        LocalReturnReadyOffset(layout.send, gateway, step), sizeof(flag));
                    Require(flag == args.send.config.magic, "local ready");
                }
            }
            std::cout << "MODULE_RESULT " << round << ' ' << SyncStepCount(layout.send) << ' '
                      << LocalReturnSlotStride(layout.send) << ' ' << ms * 1000 << std::endl;
        }
        Check(TileXRUDMAUnregister(comm, handle), "unregister");
    }
    Check(TileXRCommDestroy(comm), "destroy");
    Check(aclrtDestroyEvent(start), "destroy event");
    Check(aclrtDestroyEvent(stop), "destroy event");
    Check(aclrtDestroyStream(stream), "destroy stream");
    Check(aclrtResetDevice(rank), "reset");
    Check(aclFinalize(), "finalize");
}

} // namespace

int main(int argc, char **argv)
{
    try {
        Require(argc == 7, "rank tokens sync concurrent pipeline-object local-object");
        RunLocalPipeline(std::stoul(argv[1]), std::stoul(argv[2]), std::stoul(argv[3]), std::stoul(argv[4]) != 0,
                         argv[5], argv[6]);
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
