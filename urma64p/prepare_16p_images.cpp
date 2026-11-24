// Generate the 4+8 template, progress and profiling images using the candidate's Host helpers.
#include "alltoall_jetty_peers_full.h"
#include "tilexr/profiling/host.h"
#include "tilexr/comm/udma/registry.h"
#include <array>
#include <fstream>
#include <iostream>

namespace {
using namespace TileXRJettyPeers;
using namespace TileXR;
constexpr uint32_t kRanks = 16U;
constexpr uint32_t kQps = TileXRGrouped::kDirectSharedQpCount;

template<class T>
void Read(const std::string &path, T *data, size_t count)
{
    std::ifstream file(path, std::ios::binary);
    if (!file.read(reinterpret_cast<char *>(data), count * sizeof(T))) {
        throw std::runtime_error("could not read " + path);
    }
}

template<class T>
void Write(const std::string &path, const T *data, size_t count)
{
    std::ofstream file(path, std::ios::binary);
    if (!file.write(reinterpret_cast<const char *>(data), count * sizeof(T))) {
        throw std::runtime_error("could not write " + path);
    }
}

void Generate(const std::string &directory, uint32_t rank, uint64_t eidBase)
{
    FullKernelArgs args{};
    Read(directory + "/kernel_args.bin", &args, 1U);
    const auto &base = args.progress.base;
    const auto layout = BuildLayout(kRanks, args.progress.bytesPerPeer);
    if (!layout.valid || layout.repeat != base.repeat || layout.span != base.span ||
            layout.remoteOffset != base.remoteOffset ||
            reinterpret_cast<uint64_t>(base.workspace) + layout.statusOffset !=
                reinterpret_cast<uint64_t>(base.status)) {
        throw std::runtime_error("16P seed launch geometry mismatch");
    }
    TileXRUDMAProfileRegistry registry{};
    Read(directory + "/profile_registry.bin", &registry, 1U);
    if (registry.rankSize != kRanks || registry.qpCount != kQps) {
        throw std::runtime_error("16P seed profile geometry mismatch");
    }
    std::array<UDMAMemInfo, kRanks * kQps> memory{};
    std::array<UDMAWQCtx, kRanks * kQps> queues{};
    Read(directory + "/profile_mem_info.bin", memory.data(), memory.size());
    Read(directory + "/profile_sq_ctx.bin", queues.data(), queues.size());
    std::ifstream eidFile(directory + "/eid_table.bin", std::ios::binary | std::ios::ate);
    if (!eidFile || eidFile.tellg() < 16) throw std::runtime_error("missing EID table");
    std::vector<uint8_t> eids(static_cast<size_t>(eidFile.tellg()));
    eidFile.seekg(0);
    if (!eidFile.read(reinterpret_cast<char *>(eids.data()), eids.size())) {
        throw std::runtime_error("could not read EID table");
    }
    std::vector<PrefillEndpoint> endpoints(memory.size());
    for (uint32_t peer = 0U; peer < kRanks; ++peer) {
        if (peer == rank) continue;
        for (uint32_t qp = 0U; qp < kQps; ++qp) {
            const uint32_t index = peer * kQps + qp;
            const uint64_t address = memory[index].eidAddr;
            if (address < eidBase || address - eidBase > eids.size() - 16U) continue;
            auto &endpoint = endpoints[index];
            endpoint.memory = memory[index];
            endpoint.remoteBase = reinterpret_cast<uint64_t>(registry.regions[
                peer * TILEXR_UDMA_PROFILE_MAX_REGIONS + registry.qpBindings[qp].remoteRegion].base);
            endpoint.localToken = queues[index].localTokenId;
            std::memcpy(endpoint.eid, eids.data() + (address - eidBase), sizeof(endpoint.eid));
        }
    }
    for (uint32_t block = 0U; block < kBlocks; ++block) {
        const bool local = block < kFullmeshBlocks;
        for (uint32_t slot = 0U; slot < (local ? 1U : Destinations(kRanks)); ++slot) {
            uint32_t peer = 0U, begin = 0U, count = 0U;
            if (!SenderSlice(rank, kRanks, base.repeat, block, slot, peer, begin, count)) {
                throw std::runtime_error("invalid 4+8 sender slice");
            }
            const uint32_t qp = local ? TileXRGrouped::DirectQp(rank, peer, kQps) : Qp(block);
            if (count && !endpoints[peer * kQps + qp].remoteBase) {
                throw std::runtime_error("missing EID or remote MR for an active 4+8 queue");
            }
        }
    }
    const auto image = BuildPrefillTemplates(rank, kRanks, layout, 1U,
        reinterpret_cast<uint64_t>(base.workspace), endpoints);
    const auto progress = BuildProgressLayout(layout, kRanks, 1U);
    std::vector<uint8_t> workspace(progress.workspaceBytes);
    Read(directory + "/workspace.bin", workspace.data(), layout.statusOffset);
    std::copy(image.begin(), image.end(), workspace.begin() + layout.workspaceBytes);
    for (uint32_t batch = 1U; batch <= Batches(base.repeat); ++batch) {
        const uint64_t token = ProgressToken(args.progress.magic, batch);
        std::memcpy(workspace.data() + progress.tokensOffset + uint64_t(batch) * 64U, &token, sizeof(token));
    }
    args.progress.progressOffset = progress.progressOffset;
    args.progress.tokensOffset = progress.tokensOffset;
    using namespace TileXR::Profiling;
    DeviceView oldProfile{};
    Read(directory + "/profiling.bin", &oldProfile, 1U);
    CaptureConfig config;
    config.rank = rank;
    config.enabled = true;
    config.logicalStreamCount = kProgressBlocks;
    config.recordsPerStream = oldProfile.recordsPerStream;
    config.generation = oldProfile.generation;
    TileXR::Profiling::Layout profileLayout;
    if (!TileXR::Profiling::BuildLayout(config, &profileLayout)) {
        throw std::runtime_error("invalid profiling layout");
    }
    const auto view = MakeDeviceView(config, profileLayout, base.profilingGM);
    std::vector<uint8_t> profile(profileLayout.bytes);
    std::memcpy(profile.data(), &view, sizeof(view));
    const std::vector<uint8_t> status(kStatusBytes);
    Write(directory + "/workspace.bin", workspace.data(), workspace.size());
    Write(directory + "/status.bin", status.data(), status.size());
    Write(directory + "/profiling.bin", profile.data(), profile.size());
    Write(directory + "/kernel_args.bin", &args, 1U);
    std::cout << "{\"status_bytes\":" << status.size() << ",\"template_offset\":" << layout.workspaceBytes
              << ",\"template_bytes\":" << image.size() << ",\"prefill_end\":" << layout.workspaceBytes + image.size()
              << ",\"progress_offset\":" << progress.progressOffset << ",\"tokens_offset\":" << progress.tokensOffset
              << ",\"workspace_bytes\":" << workspace.size() << ",\"profile_bytes\":" << profile.size() << "}\n";
}
}

int main(int argc, char **argv)
{
    if (argc != 4) return 2;
    try {
        Generate(argv[1], static_cast<uint32_t>(std::stoul(argv[2])), std::stoull(argv[3], nullptr, 0));
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
