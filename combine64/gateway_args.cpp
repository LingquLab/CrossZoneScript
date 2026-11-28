/* Serialize the standalone GatewayReduce ABI and source schedule using the pinned Host implementation. */
#include "combine_ht.h"
#include <fstream>
#include <vector>

template <typename T>
void Write(const std::string &path, const T *data, size_t count)
{
    std::ofstream output(path, std::ios::binary);
    output.write(reinterpret_cast<const char *>(data), count * sizeof(T));
    if (!output) {
        throw std::runtime_error("fixture write failed");
    }
}

int main(int argc, char **argv)
{
    using namespace TileXRCombineHT;
    if (argc != 2) {
        return 2;
    }
    const std::string path(argv[1]);
    KernelArgs args{};
    std::ifstream config(path + ".config", std::ios::binary);
    config.read(reinterpret_cast<char *>(&args.config), sizeof(args.config));
    if (!config || TileXRMoonEpCombineHTGetLayout(&args.config, &args.layout)) {
        return 3;
    }
    std::vector<uint32_t> schedule(uint64_t(args.config.world) * TileXREpHt::ScheduleWords(args.config.world));
    if (TileXRMoonEpCombineHTBuildSchedule(&args.config, nullptr, schedule.data())) {
        return 4;
    }
    const uint64_t base = args.config.rank * 0x2000000000ULL;
    args.workspace = reinterpret_cast<uint8_t *>(base + 0x200000000ULL);
    args.input = reinterpret_cast<uint8_t *>(base + 0x404000000ULL);
    args.forward = reinterpret_cast<uint8_t *>(base + 0x400000000ULL);
    args.ranges = reinterpret_cast<uint8_t *>(base + 0x402000000ULL);
    args.masks = reinterpret_cast<uint8_t *>(base + 0x406000000ULL);
    args.profiling = reinterpret_cast<uint8_t *>(base + 0x430000000ULL);
    args.magic = 2026;
    Write(path + ".args", &args, 1);
    Write(path + ".layout", &args.layout, 1);
    Write(path + ".schedule", schedule.data(), schedule.size());
}
