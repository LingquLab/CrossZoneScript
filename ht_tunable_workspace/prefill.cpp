#include "combine_ht_prefill.h"
#include <tilexr/moonep/combine_ht_pipeline.h>
#include <fstream>
#include <string>

int main(int argc, char **argv)
{
    if (argc < 5 || argc > 7) return 2;
    using namespace TileXRCombineHT;
    const uint32_t rank = std::stoul(argv[1]);
    const uint32_t local = rank % 8;
    const uint32_t tokens = std::stoul(argv[4]);
    const uint32_t chunks = TileXREpHt::ChunkCount(tokens);
    const bool direct = argc >= 6 && std::string(argv[5]) == "direct";
    PipelineArgs args{};
    auto &config = args.local.config;
    const uint32_t hidden = argc == 6 && std::string(argv[5]) == "h256" ? 256 : 128;
    config = {rank, 64, tokens, hidden, 8, 2 * tokens, 8 * tokens, TileXREpHt::kTokensPerChunk,
              TileXREpHt::FirstSplits(tokens), 24, 8, TILEXR_COMBINE_HT_SEND_REDUCE, 0};
    TileXRCombineHTExecutionOptions options{};
    options.final_reduce_pipeline = 1;
    options.six_port_qp_count = 16;
    options.two_port_qp_count = 16;
    if (direct) {
        options.send_core_count = 8;
        options.six_port_qp_count = 8;
        options.two_port_qp_count = 8;
        options.rotation_policy = TileXREpHt::PeerRotationPolicy::ServerStaggered;
        options.reuse_gather_for_local_return = 0;
        if (ResolvePipelineReturnQueues(false, true, 16, 7, args.localQpBase,
                                        args.localQpStride, args.localQpSkipSelf)) return 8;
    }
    if (argc == 7) options.sync_granularity = std::stoul(argv[6]);
    if (TileXRMoonEpCombineHTGetCorePlan(&config, 16, &options, &args.cores)) return 7;
    TileXRCombineHTPipelineLayout layout{};
    if (TileXRMoonEpCombineHTGetPipelineLayoutWithOptions(&config, 2026, &options, &layout) ||
        layout.local.chunks != chunks || layout.local.groups != 1) return 3;
    const uint64_t offset = layout.local_offset;
    args.local.layout = layout.local;
    args.local.local_offset = offset;
    args.local.magic = 2026;
    args.local.comm_peer_begin = rank - local;
    args.send.config = layout.send;
    args.localReadyOffset = layout.local_ready_offset;
    args.localSignalOffset = layout.local_signal_offset;
    args.localQpStride = 1;
    args.finalBlocks = 16;
    constexpr uint64_t stride = 0x2000000000ULL, workspace = 0x200000000ULL;
    std::array<PrefillEndpoint, kLocalRanks> endpoints{};
    std::array<std::vector<int64_t>, kLocalRanks> rows;
    std::array<int32_t, kLocalRanks> counts{};
    std::vector<int32_t> ranges(kLocalRanks * chunks * 2);
    std::ifstream memory(argv[2], std::ios::binary);
    for (uint32_t gateway = 0; gateway < kLocalRanks; ++gateway) {
        if (gateway == local) continue;
        const uint32_t peer = rank - local + gateway;
        auto &endpoint = endpoints[gateway];
        memory.seekg((peer * kLocalRanks + gateway) * sizeof(endpoint.memory));
        memory.read(reinterpret_cast<char *>(&endpoint.memory), sizeof(endpoint.memory));
        if (!memory) return 4;
        endpoint.base = peer * stride + workspace + offset;
        endpoint.eid[0] = peer * stride;
        for (uint32_t token = 0; token < config.tokens; ++token) {
            rows[gateway].insert(rows[gateway].end(), {
                int64_t(rank), int64_t(token), int64_t(local * config.tokens + token)});
        }
        uint32_t first = 0;
        for (uint32_t chunk = 0; chunk < chunks; ++chunk) {
            const uint32_t index = 2 * (gateway * chunks + chunk);
            ranges[index] = first;
            first = TileXREpHt::ChunkEnd(args.send.config, chunk);
            ranges[index + 1] = first;
        }
        counts[gateway] = config.tokens;
    }
    std::vector<uint8_t> image;
    if (BuildPrefillImage(config, rank * stride + workspace, rank * stride + workspace + offset,
                         rows, counts.data(), ranges.data(), endpoints, image)) return 5;
    const std::string stem(argv[3]);
    std::ofstream plan(stem + ".args", std::ios::binary);
    plan.write(reinterpret_cast<const char *>(&args), sizeof(args));
    std::ofstream result(stem + ".bin", std::ios::binary);
    result.write(reinterpret_cast<const char *>(image.data()), image.size());
    return plan && result ? 0 : 6;
}
