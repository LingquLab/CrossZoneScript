#include "send_plan.h"

#include <array>
#include <fstream>
#include <string>

int main(int argc, char **argv)
{
    using namespace TileXREpHt;
    if (argc != 6) {
        return 2;
    }
    const uint32_t rank = std::stoul(argv[1]);
    const uint32_t six = std::stoul(argv[2]);
    const uint32_t two = std::stoul(argv[3]);
    const auto policy = static_cast<PeerRotationPolicy>(std::stoul(argv[4]));
    std::array<uint32_t, kMaxPeersPerOwner> custom{};
    size_t count = 0;
    for (uint32_t local = 0; local < kRanksPerServer; ++local) {
        for (uint32_t server = 0; server < kServerCount; ++server) {
            if (server != rank / kRanksPerServer) {
                custom[count++] = server * kRanksPerServer + (rank + local) % kRanksPerServer;
            }
        }
    }
    SendPlanSpec spec{rank, kWorld, 8, six, two, policy, 1, custom.data(), uint32_t(count)};
    InterServerSendPlan plan;
    if (!BuildInterServerSendPlan(spec, plan)) {
        return 3;
    }
    static_assert(sizeof(InterServerSendPlan) == 3136);
    std::ofstream out(argv[5], std::ios::binary);
    out.write(reinterpret_cast<const char *>(&plan), sizeof(plan));
    return out ? 0 : 4;
}
