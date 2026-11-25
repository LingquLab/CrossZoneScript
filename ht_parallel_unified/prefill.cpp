#include "combine_ht_prefill.h"
#include "ht_combine_parallel.h"
#include <fstream>
#include <string>

using TileXREpHt::ParallelKernelArgs;
static_assert(sizeof(ParallelKernelArgs) == 464, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, attached) == 0, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, send) == 288, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, serverMask) == 400, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, output) == 408, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, localReturnStatus) == 416, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, crossReturnStatus) == 424, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, finalStatus) == 432, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, localSignalOffset) == 440, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, localQpBase) == 448, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, finalBlocks) == 452, "parallel ABI");
static_assert(offsetof(ParallelKernelArgs, returnForward) == 456, "parallel ABI");

int main(int argc, char **argv)
{
    if (argc != 5) return 2;
    using namespace TileXRCombineHT;
    const uint32_t rank = std::stoul(argv[1]);
    const uint32_t local = rank % 8;
    const uint64_t offset = std::stoull(argv[2]);
    TileXRCombineHTConfig config{rank, 64, 4, 128, 8, 8, 32, 256, 4, 8, 8, 3, 0};
    TileXRCombineHTLayout layout{};
    if (TileXRMoonEpCombineHTGetLayout(&config, &layout) || layout.chunks != 4 || layout.groups != 1) return 3;
    constexpr uint64_t stride = 0x2000000000ULL, workspace = 0x200000000ULL;
    std::array<PrefillEndpoint, kLocalRanks> endpoints{};
    std::array<std::vector<int64_t>, kLocalRanks> rows;
    std::array<int32_t, kLocalRanks> counts{};
    std::array<int32_t, kLocalRanks * 4 * 2> ranges{};
    std::ifstream memory(argv[3], std::ios::binary);
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
            ranges[2 * (gateway * config.tokens + token)] = token;
            ranges[2 * (gateway * config.tokens + token) + 1] = token + 1;
        }
        counts[gateway] = config.tokens;
    }
    std::vector<uint8_t> image;
    if (BuildPrefillImage(config, rank * stride + workspace, rank * stride + workspace + offset,
                         rows, counts.data(), ranges.data(), endpoints, image)) return 5;
    KernelArgs args{};
    args.config = config;
    args.layout = layout;
    args.magic = 2026;
    args.comm_peer_begin = rank - local;
    const std::string stem(argv[4]);
    std::ofstream plan(stem + ".args", std::ios::binary);
    plan.write(reinterpret_cast<const char *>(&args), sizeof(args));
    std::ofstream result(stem + ".bin", std::ios::binary);
    result.write(reinterpret_cast<const char *>(image.data()), image.size());
    return plan && result ? 0 : 6;
}
