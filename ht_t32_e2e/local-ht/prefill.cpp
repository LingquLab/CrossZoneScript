#include "combine_ht_prefill.h"
#include <fstream>
#include <string>

int main(int argc, char **argv)
{
    if (argc != 4) return 2;
    using namespace TileXRCombineHT;
    const uint32_t rank = std::stoul(argv[1]);
    const uint32_t local = rank % 8;
    TileXRCombineHTConfig config{local, 8, 4, 128, 8, 4, 32, 256, 1, 8, 8, 3, 0};
    TileXRCombineHTLayout layout{};
    if (TileXRMoonEpCombineHTGetLayout(&config, &layout)) return 3;
    constexpr uint64_t stride = 0x2000000000ULL, work = 0x200000000ULL, input = 0x404000000ULL;
    std::array<PrefillEndpoint, kLocalRanks> endpoints{};
    std::array<std::vector<int64_t>, kLocalRanks> rows;
    std::array<int32_t, kLocalRanks> counts{};
    std::array<int32_t, kLocalRanks * 2> ranges{};
    std::ifstream memory(argv[2], std::ios::binary);
    for (uint32_t gateway = 0; gateway < kLocalRanks; ++gateway) {
        const uint32_t peer = rank - local + gateway;
        auto &endpoint = endpoints[gateway];
        memory.seekg((peer * 32 + gateway) * sizeof(endpoint.memory));
        memory.read(reinterpret_cast<char *>(&endpoint.memory), sizeof(endpoint.memory));
        if (!memory) return 4;
        endpoint.base = peer * stride + work;
        endpoint.eid[0] = peer * stride;
        if (gateway == local) continue;
        for (uint32_t t = 0; t < config.tokens; ++t) {
            rows[gateway].insert(rows[gateway].end(), {int64_t(local), int64_t(t), int64_t(local * config.tokens + t)});
        }
        counts[gateway] = config.tokens;
        ranges[2 * gateway + 1] = config.tokens;
    }
    std::vector<uint8_t> image;
    if (BuildPrefillImage(config, rank * stride + input, rank * stride + work,
                         rows, counts.data(), ranges.data(), endpoints, image)) return 5;
    KernelArgs args{};
    args.config = config;
    args.layout = layout;
    args.magic = 2026;
    args.comm_peer_begin = rank - local;
    const std::string stem(argv[3]);
    std::ofstream plan(stem + ".args", std::ios::binary);
    plan.write(reinterpret_cast<const char *>(&args), sizeof(args));
    std::ofstream result(stem + ".bin", std::ios::binary);
    result.write(reinterpret_cast<const char *>(image.data()), image.size());
    return plan && result ? 0 : 6;
}
