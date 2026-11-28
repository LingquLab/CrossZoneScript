/*
 * Copyright (c) 2026 TileXR Project.
 * Standalone single-card NPU benchmark for TileXREpHtFinalReduce across
 * SyncWindow / SyncLogicalChunk / SyncPhysicalChunk granularities.
 */
#include <acl/acl.h>
#include <tilexr/ep/final_reduce.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using namespace TileXREpHt;

#if defined(TILEXR_COMBINE_HT_SST_FP32) && TILEXR_COMBINE_HT_SST_FP32 == 1
using RowElement = float;
RowElement EncodeRow(float value) { return value; }
float DecodeRow(RowElement value) { return value; }
const char *kDtypeName = "fp32";
#else
using RowElement = uint16_t;
RowElement EncodeRow(float value)
{
    uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    return static_cast<uint16_t>((bits + 0x7fffU + ((bits >> 16U) & 1U)) >> 16U);
}
float DecodeRow(RowElement value)
{
    const uint32_t bits = static_cast<uint32_t>(value) << 16U;
    float result = 0.0F;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}
const char *kDtypeName = "bf16";
#endif

static_assert(sizeof(RowElement) == kElementBytes, "bench element size must match SendConfig");

void CheckAcl(aclError code, const char *op)
{
    if (code != ACL_SUCCESS) {
        throw std::runtime_error(std::string(op) + " failed with aclError=" + std::to_string(code));
    }
}

void CheckTileXR(int code, const char *op)
{
    if (code != 0) {
        throw std::runtime_error(std::string(op) + " failed with code=" + std::to_string(code));
    }
}

void Require(bool condition, const char *message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

struct DeviceBuffer {
    void *ptr = nullptr;
    size_t bytes = 0;

    DeviceBuffer() = default;
    explicit DeviceBuffer(size_t n) : bytes(n)
    {
        if (n > 0) {
            CheckAcl(aclrtMalloc(&ptr, n, ACL_MEM_MALLOC_HUGE_FIRST), "aclrtMalloc");
        }
    }
    DeviceBuffer(const DeviceBuffer &) = delete;
    DeviceBuffer &operator=(const DeviceBuffer &) = delete;
    ~DeviceBuffer()
    {
        if (ptr) {
            aclrtFree(ptr);
        }
    }

    void Upload(const void *host) const
    {
        if (bytes > 0) {
            CheckAcl(aclrtMemcpy(ptr, bytes, host, bytes, ACL_MEMCPY_HOST_TO_DEVICE), "aclrtMemcpy H2D");
        }
    }
    void Download(void *host) const
    {
        if (bytes > 0) {
            CheckAcl(aclrtMemcpy(host, bytes, ptr, bytes, ACL_MEMCPY_DEVICE_TO_HOST), "aclrtMemcpy D2H");
        }
    }
};

struct AclEvent {
    aclrtEvent handle = nullptr;

    AclEvent() { CheckAcl(aclrtCreateEvent(&handle), "aclrtCreateEvent"); }
    AclEvent(const AclEvent &) = delete;
    AclEvent &operator=(const AclEvent &) = delete;
    ~AclEvent()
    {
        if (handle) {
            aclrtDestroyEvent(handle);
        }
    }
};

const char *SyncModeName(uint32_t mode)
{
    switch (mode) {
    case SyncWindow:
        return "window";
    case SyncLogicalChunk:
        return "logical_chunk";
    case SyncPhysicalChunk:
        return "physical_chunk";
    default:
        return "unknown";
    }
}

uint32_t ParseSyncModeToken(const std::string &token)
{
    if (token == "0" || token == "window" || token == "SyncWindow") {
        return SyncWindow;
    }
    if (token == "1" || token == "logical" || token == "logical_chunk" || token == "SyncLogicalChunk") {
        return SyncLogicalChunk;
    }
    if (token == "2" || token == "physical" || token == "physical_chunk" || token == "SyncPhysicalChunk") {
        return SyncPhysicalChunk;
    }
    throw std::invalid_argument("Unsupported sync mode: " + token);
}

std::vector<std::string> SplitComma(const std::string &input)
{
    std::vector<std::string> out;
    std::stringstream ss(input);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (!item.empty()) {
            out.push_back(item);
        }
    }
    return out;
}

uint64_t SplitMix64(uint64_t &state)
{
    uint64_t z = (state += 0x9e3779b97f4a7c15ULL);
    z = (z ^ (z >> 30U)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27U)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31U);
}

uint32_t PopCount64(uint64_t x)
{
    return static_cast<uint32_t>(__builtin_popcountll(x));
}

struct BenchOptions {
    uint32_t device = 0;
    uint32_t world = kWorld;
    uint32_t rank = 0;
    uint32_t tokens = 4096;
    uint32_t hidden = 7168;
    uint32_t topk = 8;
    uint32_t rotationPolicy = 0;
    uint32_t activeServers = 0; // 0 means default min(topk, world / 8)
    std::string maskMode = "fixed";
    uint64_t seed = 2026;
    uint32_t warmup = 10;
    uint32_t iters = 50;
    bool verify = true;
    bool csv = false;
    std::vector<uint32_t> blocks = {16};
    std::vector<uint32_t> syncModes = {SyncWindow, SyncLogicalChunk, SyncPhysicalChunk};
};

void PrintUsage(const char *program)
{
    std::cout << "Usage: " << program << " [options]\n"
              << "Single-card benchmark for TileXREpHtFinalReduce across sync granularities.\n\n"
              << "Options:\n"
              << "  --device <id>               NPU device id (default: 0)\n"
              << "  --world <16|32|64>          EP world size (default: 64)\n"
              << "  --rank <id>                 Logical receiver rank (default: 0)\n"
              << "  --tokens <1..16384>         Token count (default: 4096)\n"
              << "  --hidden <16..7168>         Hidden size, multiple of 16 (default: 7168)\n"
              << "  --topk <1..16>              Top-k value (default: 8)\n"
              << "  --blocks <list>             Comma-separated AIV block counts (default: 16)\n"
              << "  --sync-modes <list>         Comma-separated modes: 0/window,1/logical,2/physical (default: 0,1,2)\n"
              << "  --rotation-policy <0|1|2>   SendConfig::rotationPolicy (default: 0; 0=5-plane window, 1=3-plane)\n"
              << "  --mask-mode <mode>          Server mask pattern: fixed|random_topk|mixed (default: fixed)\n"
              << "  --active-servers <N>        Active servers/token for mask-mode=fixed (default: min(topk, world/8))\n"
              << "  --seed <N>                  RNG seed for mask-mode=random_topk (default: 2026)\n"
              << "  --warmup <N>                Warmup iterations (default: 10)\n"
              << "  --iters <N>                 Timed iterations (default: 50)\n"
              << "  --verify <0|1>              Check bit-exact output before timing (default: 1)\n"
              << "  --csv <0|1>                 Print CSV rows instead of formatted table (default: 0)\n"
              << "  --help                      Show this message\n";
}

BenchOptions ParseArgs(int argc, char **argv)
{
    BenchOptions opt;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--help" || arg == "-h") {
            PrintUsage(argv[0]);
            std::exit(0);
        }
        Require(i + 1 < argc, ("Missing value for option: " + arg).c_str());
        const std::string val = argv[++i];
        if (arg == "--device") {
            opt.device = static_cast<uint32_t>(std::stoul(val));
        } else if (arg == "--world") {
            opt.world = static_cast<uint32_t>(std::stoul(val));
        } else if (arg == "--rank") {
            opt.rank = static_cast<uint32_t>(std::stoul(val));
        } else if (arg == "--tokens") {
            opt.tokens = static_cast<uint32_t>(std::stoul(val));
        } else if (arg == "--hidden") {
            opt.hidden = static_cast<uint32_t>(std::stoul(val));
        } else if (arg == "--topk") {
            opt.topk = static_cast<uint32_t>(std::stoul(val));
        } else if (arg == "--rotation-policy") {
            opt.rotationPolicy = static_cast<uint32_t>(std::stoul(val));
        } else if (arg == "--active-servers") {
            opt.activeServers = static_cast<uint32_t>(std::stoul(val));
        } else if (arg == "--mask-mode") {
            opt.maskMode = val;
        } else if (arg == "--seed") {
            opt.seed = std::stoull(val);
        } else if (arg == "--warmup") {
            opt.warmup = static_cast<uint32_t>(std::stoul(val));
        } else if (arg == "--iters") {
            opt.iters = static_cast<uint32_t>(std::stoul(val));
        } else if (arg == "--verify") {
            opt.verify = std::stoul(val) != 0;
        } else if (arg == "--csv") {
            opt.csv = std::stoul(val) != 0;
        } else if (arg == "--blocks") {
            opt.blocks.clear();
            for (const auto &tok : SplitComma(val)) {
                opt.blocks.push_back(static_cast<uint32_t>(std::stoul(tok)));
            }
        } else if (arg == "--sync-modes") {
            opt.syncModes.clear();
            for (const auto &tok : SplitComma(val)) {
                opt.syncModes.push_back(ParseSyncModeToken(tok));
            }
        } else {
            throw std::invalid_argument("Unknown option: " + arg);
        }
    }
    Require(opt.world == 16 || opt.world == 32 || opt.world == 64, "--world must be 16, 32 or 64");
    Require(opt.rank < opt.world, "--rank must be < --world");
    Require(opt.tokens >= 1 && opt.tokens <= 16384, "--tokens must be in [1, 16384]");
    Require(opt.hidden >= 16 && opt.hidden <= 7168 && opt.hidden % 16 == 0,
        "--hidden must be in [16, 7168] and a multiple of 16");
    Require(opt.topk >= 1 && opt.topk <= 16, "--topk must be in [1, 16]");
    Require(!opt.blocks.empty(), "--blocks must not be empty");
    for (uint32_t b : opt.blocks) {
        Require(b >= 1 && b <= kMaxFinalReduceBlocks, "--blocks entries must be in [1, 64]");
    }
    Require(!opt.syncModes.empty(), "--sync-modes must not be empty");
    Require(opt.iters >= 1, "--iters must be >= 1");
    Require(opt.maskMode == "fixed" || opt.maskMode == "random_topk" || opt.maskMode == "mixed",
        "--mask-mode must be fixed, random_topk or mixed");
    const uint32_t servers = opt.world / kRanksPerServer;
    if (opt.activeServers == 0) {
        opt.activeServers = std::min(opt.topk, servers);
    }
    Require(opt.activeServers <= servers, "--active-servers exceeds world / 8");
    return opt;
}

std::vector<uint64_t> BuildServerMasks(const BenchOptions &opt, uint64_t &totalActivePartials)
{
    const uint32_t servers = opt.world / kRanksPerServer;
    const uint64_t allMask = (uint64_t {1} << servers) - 1;
    std::vector<uint64_t> masks(opt.tokens, 0);
    totalActivePartials = 0;

    if (opt.maskMode == "fixed") {
        const uint32_t k = opt.activeServers;
        const uint64_t base = k == servers ? allMask : ((uint64_t {1} << k) - 1);
        for (uint32_t t = 0; t < opt.tokens; ++t) {
            const uint32_t shift = t % servers;
            const uint64_t rotated = ((base << shift) | (base >> (servers - shift))) & allMask;
            masks[t] = rotated;
            totalActivePartials += k;
        }
    } else if (opt.maskMode == "mixed") {
        for (uint32_t t = 0; t < opt.tokens; ++t) {
            const uint32_t k = t % (servers + 1);
            const uint64_t base = k == servers ? allMask : ((uint64_t {1} << k) - 1);
            const uint32_t shift = (t / (servers + 1)) % servers;
            const uint64_t rotated = ((base << shift) | (base >> (servers - shift))) & allMask;
            masks[t] = rotated;
            totalActivePartials += k;
        }
    } else {
        uint64_t rng = opt.seed;
        for (uint32_t t = 0; t < opt.tokens; ++t) {
            uint64_t m = 0;
            for (uint32_t k = 0; k < opt.topk; ++k) {
                const uint32_t targetRank = static_cast<uint32_t>(SplitMix64(rng) % opt.world);
                m |= uint64_t {1} << (targetRank / kRanksPerServer);
            }
            masks[t] = m;
            totalActivePartials += PopCount64(m);
        }
    }
    return masks;
}

inline RowElement PartialPatternValue(uint32_t token, uint32_t server, uint32_t h)
{
    const int sign = ((token + server) & 1U) == 0 ? 1 : -1;
    const uint32_t mag = ((token * 3U + server * 5U + h) & 31U) + 1U;
    return EncodeRow(static_cast<float>(sign) * static_cast<float>(mag) / 16.0F);
}

struct TimingStats {
    double minUs = 0.0;
    double p50Us = 0.0;
    double meanUs = 0.0;
    double p95Us = 0.0;
    double p99Us = 0.0;
    double maxUs = 0.0;
};

TimingStats Summarize(std::vector<double> samplesUs)
{
    std::sort(samplesUs.begin(), samplesUs.end());
    auto quantile = [&](double q) {
        const size_t n = samplesUs.size();
        const double idx = q * static_cast<double>(n - 1);
        const size_t lo = static_cast<size_t>(idx);
        const size_t hi = std::min(lo + 1, n - 1);
        const double frac = idx - static_cast<double>(lo);
        return samplesUs[lo] * (1.0 - frac) + samplesUs[hi] * frac;
    };
    TimingStats stats;
    stats.minUs = samplesUs.front();
    stats.p50Us = quantile(0.50);
    stats.p95Us = quantile(0.95);
    stats.p99Us = quantile(0.99);
    stats.maxUs = samplesUs.back();
    stats.meanUs = std::accumulate(samplesUs.begin(), samplesUs.end(), 0.0) / static_cast<double>(samplesUs.size());
    return stats;
}

struct CaseResult {
    uint32_t blocks = 0;
    uint32_t syncMode = 0;
    uint32_t steps = 0;
    uint32_t waitPlanes = 0;
    TimingStats stats;
    double bwGBs = 0.0;
    bool verified = false;
};

CaseResult RunCase(const BenchOptions &opt, const std::vector<uint64_t> &masks, uint64_t totalActivePartials,
    const std::vector<RowElement> &expectedOutput, const DeviceBuffer &serverMaskDev, const DeviceBuffer &workspaceDev,
    const DeviceBuffer &outputDev, const SendConfig &baseConfig, uint32_t blocks, uint32_t syncMode, aclrtStream stream)
{
    SendConfig config {};
    constexpr int64_t kMagic = 2026;
    Require(MakeSendConfig(opt.rank, opt.tokens, opt.hidden, opt.topk, 1, 0, kMagic, config, opt.world, 1, kOwners,
                syncMode),
        "MakeSendConfig failed");
    config.rotationPolicy = opt.rotationPolicy;
    Require(config.workspaceBytes == baseConfig.workspaceBytes && config.returnOffset == baseConfig.returnOffset &&
            config.completionOffset == baseConfig.completionOffset,
        "SendConfig workspace layout unexpectedly changed across syncMode");

    const uint64_t localReadyBytes = LocalReturnReadyBytes(config);
    std::vector<uint8_t> localReadyHost(localReadyBytes, 0);
    const uint32_t steps = SyncStepCount(config);
    for (uint32_t gateway = 0; gateway < kRanksPerServer; ++gateway) {
        for (uint32_t step = 0; step < steps; ++step) {
            std::memcpy(localReadyHost.data() + LocalReturnReadyOffset(config, gateway, step), &kMagic, sizeof(kMagic));
        }
    }
    DeviceBuffer localReadyDev(localReadyBytes);
    localReadyDev.Upload(localReadyHost.data());

    const uint64_t statusWords = uint64_t(blocks) * kFinalReduceStatusStride;
    DeviceBuffer statusDev(statusWords * sizeof(uint32_t));
    std::vector<uint32_t> statusHost(statusWords, UINT32_MAX);

    const auto checkStatus = [&](const char *phase) {
        statusDev.Download(statusHost.data());
        for (uint32_t b = 0; b < blocks; ++b) {
            const uint32_t code = statusHost[uint64_t(b) * kFinalReduceStatusStride];
            if (code != FinalReduceSuccess) {
                throw std::runtime_error(std::string(phase) + " failed on block " + std::to_string(b) +
                    " status=" + std::to_string(code));
            }
        }
    };

    bool verified = false;
    if (opt.verify) {
        CheckAcl(aclrtMemset(outputDev.ptr, outputDev.bytes, 0x5a, outputDev.bytes), "memset output");
        CheckAcl(aclrtMemset(statusDev.ptr, statusDev.bytes, 0xff, statusDev.bytes), "memset status");
        CheckTileXR(TileXREpHtFinalReduce(workspaceDev.ptr, static_cast<const uint64_t *>(serverMaskDev.ptr),
                        localReadyDev.ptr, outputDev.ptr, static_cast<uint32_t *>(statusDev.ptr), &config, blocks,
                        stream),
            "TileXREpHtFinalReduce verify");
        CheckAcl(aclrtSynchronizeStream(stream), "sync verify");
        checkStatus("verify");

        std::vector<RowElement> actual(uint64_t(opt.tokens) * opt.hidden);
        outputDev.Download(actual.data());
        for (size_t idx = 0; idx < actual.size(); ++idx) {
            if (actual[idx] != expectedOutput[idx]) {
                const uint32_t token = static_cast<uint32_t>(idx / opt.hidden);
                const uint32_t h = static_cast<uint32_t>(idx % opt.hidden);
                std::ostringstream oss;
                oss << "Verification mismatch at mode=" << SyncModeName(syncMode) << " blocks=" << blocks
                    << " token=" << token << " h=" << h << " mask=0x" << std::hex << masks[token] << std::dec
                    << " expected=" << DecodeRow(expectedOutput[idx]) << " actual=" << DecodeRow(actual[idx]);
                throw std::runtime_error(oss.str());
            }
        }
        verified = true;
    }

    for (uint32_t i = 0; i < opt.warmup; ++i) {
        CheckTileXR(TileXREpHtFinalReduce(workspaceDev.ptr, static_cast<const uint64_t *>(serverMaskDev.ptr),
                        localReadyDev.ptr, outputDev.ptr, static_cast<uint32_t *>(statusDev.ptr), &config, blocks,
                        stream),
            "TileXREpHtFinalReduce warmup");
    }
    CheckAcl(aclrtSynchronizeStream(stream), "sync warmup");

    CheckAcl(aclrtMemset(statusDev.ptr, statusDev.bytes, 0xff, statusDev.bytes), "memset status before timing");
    AclEvent startEv;
    AclEvent stopEv;
    std::vector<double> samplesUs;
    samplesUs.reserve(opt.iters);
    for (uint32_t i = 0; i < opt.iters; ++i) {
        CheckAcl(aclrtRecordEvent(startEv.handle, stream), "record start");
        CheckTileXR(TileXREpHtFinalReduce(workspaceDev.ptr, static_cast<const uint64_t *>(serverMaskDev.ptr),
                        localReadyDev.ptr, outputDev.ptr, static_cast<uint32_t *>(statusDev.ptr), &config, blocks,
                        stream),
            "TileXREpHtFinalReduce timed");
        CheckAcl(aclrtRecordEvent(stopEv.handle, stream), "record stop");
        CheckAcl(aclrtSynchronizeEvent(stopEv.handle), "sync stop event");
        float elapsedMs = 0.0F;
        CheckAcl(aclrtEventElapsedTime(&elapsedMs, startEv.handle, stopEv.handle), "elapsed time");
        samplesUs.push_back(static_cast<double>(elapsedMs) * 1000.0);
    }
    checkStatus("timed");

    const uint64_t readBytes = totalActivePartials * opt.hidden * sizeof(RowElement);
    const uint64_t writeBytes = uint64_t(opt.tokens) * opt.hidden * sizeof(RowElement);
    const TimingStats stats = Summarize(std::move(samplesUs));
    const double bwGBs = stats.p50Us > 0.0 ? (static_cast<double>(readBytes + writeBytes) / (stats.p50Us * 1e-6) / 1e9)
                                           : 0.0;

    // Count max wait planes used by WaitWindow for visibility in output.
    uint32_t waitPlanes = 3;
    if (syncMode == SyncWindow && opt.rotationPolicy == 0) {
        for (uint32_t first = 0; first < config.chunks;) {
            const uint32_t last = SyncStepEnd(config, first);
            if (first != last && LogicalChunk(config, last - 1) != LogicalChunk(config, last)) {
                waitPlanes = 5;
                break;
            }
            first = last + 1;
        }
    }

    return CaseResult {blocks, syncMode, steps, waitPlanes, stats, bwGBs, verified};
}

} // namespace

int main(int argc, char **argv)
{
    try {
        const BenchOptions opt = ParseArgs(argc, argv);
        const uint32_t servers = opt.world / kRanksPerServer;
        constexpr int64_t kMagic = 2026;

        SendConfig baseConfig {};
        Require(MakeSendConfig(opt.rank, opt.tokens, opt.hidden, opt.topk, 1, 0, kMagic, baseConfig, opt.world, 1,
                    kOwners, SyncWindow),
            "Base MakeSendConfig failed");

        uint64_t totalActivePartials = 0;
        const std::vector<uint64_t> masks = BuildServerMasks(opt, totalActivePartials);
        const double avgServers = static_cast<double>(totalActivePartials) / static_cast<double>(opt.tokens);

        // Prepare shared read-only workspace once (returned partials + all remote completion SOs).
        std::vector<uint8_t> workspaceHost(baseConfig.workspaceBytes, 0);
        for (uint32_t gateway = 0; gateway < opt.world; ++gateway) {
            for (uint32_t chunk = 0; chunk < baseConfig.chunks; ++chunk) {
                for (uint32_t lane = 0; lane < kLanes; ++lane) {
                    std::memcpy(workspaceHost.data() + CompletionOffset(baseConfig, gateway, chunk, lane), &kMagic,
                        sizeof(kMagic));
                }
            }
        }

        const RowElement poison = EncodeRow(-17.0F);
        std::vector<RowElement> expectedOutput;
        if (opt.verify) {
            expectedOutput.resize(uint64_t(opt.tokens) * opt.hidden);
        }
        std::vector<float> accum(opt.verify ? opt.hidden : 0, 0.0F);
        for (uint32_t token = 0; token < opt.tokens; ++token) {
            const uint64_t mask = masks[token];
            if (opt.verify) {
                std::fill(accum.begin(), accum.end(), 0.0F);
            }
            for (uint32_t server = 0; server < servers; ++server) {
                auto *dst = reinterpret_cast<RowElement *>(
                    workspaceHost.data() + ReturnOffset(baseConfig, token, server * kRanksPerServer));
                if (mask & (uint64_t {1} << server)) {
                    for (uint32_t h = 0; h < opt.hidden; ++h) {
                        const RowElement v = PartialPatternValue(token, server, h);
                        dst[h] = v;
                        if (opt.verify) {
                            accum[h] += DecodeRow(v);
                        }
                    }
                } else {
                    std::fill_n(dst, opt.hidden, poison);
                }
            }
            if (opt.verify) {
                RowElement *expRow = expectedOutput.data() + uint64_t(token) * opt.hidden;
                for (uint32_t h = 0; h < opt.hidden; ++h) {
                    expRow[h] = EncodeRow(accum[h]);
                }
            }
        }

        CheckAcl(aclInit(nullptr), "aclInit");
        CheckAcl(aclrtSetDevice(static_cast<int32_t>(opt.device)), "aclrtSetDevice");
        aclrtStream stream = nullptr;
        CheckAcl(aclrtCreateStream(&stream), "aclrtCreateStream");

        std::vector<CaseResult> results;
        {
            DeviceBuffer workspaceDev(baseConfig.workspaceBytes);
            DeviceBuffer serverMaskDev(uint64_t(opt.tokens) * sizeof(uint64_t));
            DeviceBuffer outputDev(uint64_t(opt.tokens) * opt.hidden * sizeof(RowElement));
            workspaceDev.Upload(workspaceHost.data());
            serverMaskDev.Upload(masks.data());

            for (uint32_t blocks : opt.blocks) {
                for (uint32_t mode : opt.syncModes) {
                    results.push_back(RunCase(opt, masks, totalActivePartials, expectedOutput, serverMaskDev,
                        workspaceDev, outputDev, baseConfig, blocks, mode, stream));
                }
            }
        }

        CheckAcl(aclrtDestroyStream(stream), "aclrtDestroyStream");
        CheckAcl(aclrtResetDevice(static_cast<int32_t>(opt.device)), "aclrtResetDevice");
        CheckAcl(aclFinalize(), "aclFinalize");

        const double totalTrafficMB =
            static_cast<double>((totalActivePartials + opt.tokens) * opt.hidden * sizeof(RowElement)) / (1024.0 * 1024.0);

        if (opt.csv) {
            std::cout << "dtype,world,tokens,hidden,topk,mask_mode,avg_servers,rotation_policy,blocks,sync_mode,steps,"
                         "wait_planes,min_us,p50_us,mean_us,p95_us,p99_us,max_us,eff_bw_gbs,verified\n";
            for (const auto &r : results) {
                std::cout << kDtypeName << ',' << opt.world << ',' << opt.tokens << ',' << opt.hidden << ',' << opt.topk
                          << ',' << opt.maskMode << ',' << std::fixed << std::setprecision(2) << avgServers << ','
                          << opt.rotationPolicy << ',' << r.blocks << ',' << SyncModeName(r.syncMode) << ',' << r.steps
                          << ',' << r.waitPlanes << ',' << std::setprecision(2) << r.stats.minUs << ',' << r.stats.p50Us
                          << ',' << r.stats.meanUs << ',' << r.stats.p95Us << ',' << r.stats.p99Us << ','
                          << r.stats.maxUs << ',' << std::setprecision(2) << r.bwGBs << ',' << (r.verified ? 1 : 0)
                          << '\n';
            }
        } else {
            std::cout << "FinalReduce Single-Card Benchmark (pre-ready pure reduction overhead)\n"
                      << "  dtype=" << kDtypeName << " world=" << opt.world << " rank=" << opt.rank
                      << " tokens=" << opt.tokens << " hidden=" << opt.hidden << " topk=" << opt.topk
                      << " mask=" << opt.maskMode << " avg_servers=" << std::fixed << std::setprecision(2) << avgServers
                      << " traffic=" << std::setprecision(2) << totalTrafficMB << " MiB"
                      << " rotation_policy=" << opt.rotationPolicy << " warmup=" << opt.warmup
                      << " iters=" << opt.iters << "\n\n";
            std::cout << std::left << std::setw(8) << "blocks" << std::setw(16) << "sync_mode" << std::right
                      << std::setw(6) << "steps" << std::setw(8) << "planes" << std::setw(11) << "min(us)"
                      << std::setw(11) << "p50(us)" << std::setw(11) << "mean(us)" << std::setw(11) << "p99(us)"
                      << std::setw(11) << "BW(GB/s)" << std::setw(13) << "vs_window" << std::setw(8) << "check\n";
            std::cout << std::string(114, '-') << '\n';

            for (const auto &r : results) {
                double baseP50 = -1.0;
                for (const auto &cand : results) {
                    if (cand.blocks == r.blocks && cand.syncMode == SyncWindow) {
                        baseP50 = cand.stats.p50Us;
                        break;
                    }
                }
                std::ostringstream delta;
                if (baseP50 > 0.0) {
                    const double diffUs = r.stats.p50Us - baseP50;
                    const double pct = diffUs / baseP50 * 100.0;
                    delta << std::showpos << std::fixed << std::setprecision(1) << diffUs << "us(" << pct << "%)";
                } else {
                    delta << "-";
                }
                std::cout << std::left << std::setw(8) << r.blocks << std::setw(16) << SyncModeName(r.syncMode)
                          << std::right << std::setw(6) << r.steps << std::setw(8) << r.waitPlanes << std::fixed
                          << std::setprecision(2) << std::setw(11) << r.stats.minUs << std::setw(11) << r.stats.p50Us
                          << std::setw(11) << r.stats.meanUs << std::setw(11) << r.stats.p99Us << std::setw(11)
                          << r.bwGBs << std::setw(13) << delta.str() << std::setw(8)
                          << (r.verified ? "PASS" : "SKIP") << '\n';
            }
        }
        return 0;
    } catch (const std::exception &err) {
        std::cerr << "Error: " << err.what() << '\n';
        return 1;
    }
}
