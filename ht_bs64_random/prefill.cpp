#include "combine_ht_prefill.h"
#include <fstream>
#include <iostream>

int main(int argc, char **argv)
{
    if (argc != 4) return 2;
    using namespace TileXRCombineHT;
    std::ifstream memory(argv[1], std::ios::binary);
    TileXRCombineHTConfig config{};
    if (!(std::cin >> config.rank >> config.input_rows >> config.gather_rows)) return 3;
    config.world = 64;
    config.tokens = 64;
    config.hidden = 128;
    config.topk = 6;
    config.chunk_tokens = 256;
    config.first_chunk_splits = 4;
    config.reduce_core_begin = 8;
    config.reduce_core_count = 8;
    config.run_mask = 3;
    config.wait_policy = TILEXR_COMBINE_HT_CONTRIBUTORS;
    TileXRCombineHTLayout layout{};
    if (TileXRMoonEpCombineHTGetLayout(&config, &layout)) return 4;
    constexpr uint64_t stride = 0x2000000000ULL, work = 0x200000000ULL, input = 0x404000000ULL;
    std::array<PrefillEndpoint, kLocalRanks> endpoints{};
    std::array<std::vector<int64_t>, kLocalRanks> rows;
    std::array<int32_t, kLocalRanks> counts{};
    std::vector<int32_t> ranges(layout.backward_range_elements);
    const uint32_t firstPeer = config.rank / kLocalRanks * kLocalRanks;
    for (uint32_t gateway = 0; gateway < kLocalRanks; ++gateway) {
        const uint32_t peer = firstPeer + gateway;
        auto &endpoint = endpoints[gateway];
        memory.seekg((peer * 32 + gateway) * sizeof(endpoint.memory));
        memory.read(reinterpret_cast<char *>(&endpoint.memory), sizeof(endpoint.memory));
        if (!memory) return 5;
        endpoint.base = peer * stride + work;
        endpoint.eid[0] = peer * stride;
        for (uint32_t chunk = 0; chunk < layout.chunks; ++chunk) {
            const uint32_t stage = gateway * layout.chunks + chunk;
            uint32_t entries;
            if (!(std::cin >> entries)) return 6;
            ranges[stage * 2] = counts[gateway];
            for (uint32_t i = 0; i < entries; ++i) {
                int64_t source, inputRow, gatherRow;
                if (!(std::cin >> source >> inputRow >> gatherRow)) return 7;
                rows[gateway].insert(rows[gateway].end(), {source, inputRow, gatherRow});
            }
            counts[gateway] += entries;
            ranges[stage * 2 + 1] = counts[gateway];
        }
    }
    std::vector<uint8_t> image;
    if (BuildPrefillImage(config, config.rank * stride + input, config.rank * stride + work,
                         rows, counts.data(), ranges.data(), endpoints, image)) return 8;
    KernelArgs args{};
    args.config = config;
    args.layout = layout;
    args.magic = 2026;
    args.comm_peer_begin = firstPeer;
    std::ofstream plan(argv[2], std::ios::binary);
    plan.write(reinterpret_cast<const char *>(&args), sizeof(args));
    std::ofstream result(argv[3], std::ios::binary);
    result.write(reinterpret_cast<const char *>(image.data()), image.size());
    return plan && result ? 0 : 9;
}
