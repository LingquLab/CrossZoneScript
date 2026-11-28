/* Copyright (c) 2026 TileXR Project.
 * Run the standalone LocalReturn or native shared-QP send probe with an EP8 communicator.
 */
#include "local_return_args.h"
#include "runtime/kernel.h"
#include <acl/acl.h>
#include <tilexr/comm/api.h>
#include <tilexr/ep/local_return.h>

#include <algorithm>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

using namespace TileXREpHt;

namespace {
constexpr uint32_t hidden = 128;
constexpr unsigned ranks = 8;
constexpr unsigned guard = 512;

void Check(int64_t code, const char *label)
{
    if (code != 0) {
        throw std::runtime_error(std::string(label) + " code=" + std::to_string(code));
    }
}

void Require(bool condition, const char *label)
{
    Check(!condition, label);
}

struct Buffer {
    void *ptr = nullptr;
    size_t bytes;
    explicit Buffer(size_t size) : bytes(size)
    {
        Check(aclrtMalloc(&ptr, bytes, ACL_MEM_MALLOC_HUGE_FIRST), "malloc");
    }
    ~Buffer() { aclrtFree(ptr); }
    void Upload(const void *source) const
    {
        Check(aclrtMemcpy(ptr, bytes, source, bytes, ACL_MEMCPY_HOST_TO_DEVICE), "upload");
    }
    void Download(void *target) const
    {
        Check(aclrtMemcpy(target, bytes, ptr, bytes, ACL_MEMCPY_DEVICE_TO_HOST), "download");
    }
    uint64_t Address() const { return reinterpret_cast<uint64_t>(ptr); }
};

struct NativeSendArgs {
    uint64_t comm, workspace, ranges, status;
    SendConfig config;
};

class BareKernel {
public:
    BareKernel(const char *path, const char *name)
    {
        std::ifstream file(path, std::ios::binary);
        image_.assign(std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>());
        Require(!image_.empty(), "kernel binary");
        rtDevBinary_t binary {};
        binary.magic = 0x41415246U;
        binary.data = image_.data();
        binary.length = image_.size();
        Check(rtDevBinaryRegister(&binary, &handle_), "binary register");
        Check(rtFunctionRegister(handle_, &signature_, name, name, 0), "function register");
    }
    template <typename Args>
    void Launch(Args &args, uint32_t blocks, aclrtStream stream)
    {
        rtArgsEx_t launch {};
        launch.args = &args;
        launch.argsSize = sizeof(args);
        rtTaskCfgInfo_t task {};
        task.schemMode = 1;
        Check(rtKernelLaunchWithFlagV2(&signature_, blocks, &launch, nullptr, stream, 0, &task), "launch");
    }
private:
    std::vector<char> image_;
    void *handle_ = nullptr;
    uint8_t signature_ = 0;
};

void StoreMagic(std::vector<uint8_t> &image, uint64_t offset, int64_t magic)
{
    std::memcpy(image.data() + offset, &magic, sizeof(magic));
}

void ExpectMagic(const std::vector<uint8_t> &image, uint64_t offset, int64_t expected)
{
    int64_t actual = 0;
    std::memcpy(&actual, image.data() + offset, sizeof(actual));
    Require(actual == expected, "completion magic");
}

uint16_t Value(uint32_t rank, uint32_t token, uint32_t col)
{
    return uint16_t(0x3e00U + (rank * 79U + token * 37U + col) % 512U);
}

void SeedRow(std::vector<uint8_t> &image, uint64_t offset, uint32_t sender, uint32_t token)
{
    for (uint32_t col = 0; col < hidden; ++col) {
        const uint16_t value = Value(sender, token, col);
        std::memcpy(image.data() + offset + col * 2, &value, 2);
    }
}

void ExpectRow(const std::vector<uint8_t> &image, uint64_t offset, uint32_t sender, uint32_t token)
{
    for (uint32_t col = 0; col < hidden; ++col) {
        uint16_t got = 0;
        std::memcpy(&got, image.data() + offset + col * 2, 2);
        Require(got == Value(sender, token, col), "payload mismatch");
    }
}

void Run(uint32_t rank, uint32_t tokens, uint32_t sync, bool send, bool direct,
    uint32_t cores, const char *binary)
{
    Check(aclInit(nullptr), "ACL init");
    Check(aclrtSetDevice(rank), "device");
    aclrtStream stream = nullptr;
    aclrtEvent start = nullptr, stop = nullptr;
    Check(aclrtCreateStream(&stream), "stream");
    Check(aclrtCreateEvent(&start), "start");
    Check(aclrtCreateEvent(&stop), "stop");
    TileXRCommPtr comm = nullptr;
    Check(TileXRCommInitRankWithSharedQpDomain(0, ranks, rank, &comm), "communicator");
    {
        SendConfig config {};
        // World 64 provides the production workspace and enough UB cache slots
        // for all same-host test peers even with eight sending AIVs.
        Require(MakeSendConfig(rank, tokens, hidden, 1, 0, tokens, 1, config,
                               64, 1, cores, sync), "SendConfig");
        uint32_t directBase = 0, directCount = 0;
        Check(TileXRUDMAGetSharedQpLayout(comm, &config.sixPortQps, &config.twoPortQps, &directBase,
                                          &directCount), "layout");
        if (direct) {
            Require(directCount == 7, "direct layout");
        }
        const uint64_t ready = config.workspaceBytes;
        const uint64_t signal = ready + LocalReturnReadyBytes(config);
        const uint64_t size = signal + uint64_t(ranks) * kSlotBytes;
        Buffer workspace(size + guard);
        auto *base = static_cast<GM_ADDR>(workspace.ptr);
        TileXRUDMAMemHandle handle = 0;
        Check(TileXRUDMARegister(comm, base, size, &handle), "register");
        Buffer forward(uint64_t(config.world) * config.forwardCapacity * sizeof(int32_t) * kForwardRecordWords);
        Buffer counts(config.world * sizeof(int32_t));
        Buffer ranges(uint64_t(config.world) * config.chunks * 2 * sizeof(int32_t));
        Buffer status(std::max<unsigned>(ranks, cores) * 32);
        std::vector<int32_t> hostForward(forward.bytes / 4), hostCounts(counts.bytes / 4), hostRanges(ranges.bytes / 4);
        for (uint32_t peer = 0; peer < ranks; ++peer) {
            if (!send) {
                for (uint32_t chunk = 0, token = 0; chunk < config.chunks; ++chunk) {
                    const auto at = (peer * config.chunks + chunk) * 2;
                    hostRanges[at] = hostCounts[peer];
                    for (; token < ChunkEnd(config, chunk); ++token) {
                        if ((token + peer) % ranks != rank) {
                            continue;
                        }
                        const uint32_t index = hostCounts[peer]++;
                        auto *record = hostForward.data() + (peer * config.forwardCapacity + index) * kForwardRecordWords;
                        record[0] = index + 1;
                        record[1] = token;
                        record[2] = token;
                        record[3] = 0;
                    }
                    hostRanges[at + 1] = hostCounts[peer];
                }
            }
        }
        forward.Upload(hostForward.data());
        counts.Upload(hostCounts.data());
        ranges.Upload(hostRanges.data());
        std::unique_ptr<BareKernel> kernel;
        if (send || direct) {
            kernel.reset(new BareKernel(binary, send ? "tilexr_ht_ep8_send_probe" : "tilexr_ep_ht_local_return_kernel"));
        }
        GM_ADDR commArgs = nullptr, registry = nullptr;
        TileXR::CommArgs *commHost = nullptr;
        Check(TileXRGetCommArgsDev(comm, commArgs), "device comm");
        Check(TileXRGetCommArgsHost(comm, commHost), "host comm");
        Check(TileXRGetUDMARegistryDev(comm, registry), "device registry");
        std::vector<uint8_t> initial(workspace.bytes, 0xa5), actual(workspace.bytes);
        std::fill(initial.begin(), initial.end() - guard, 0);
        std::vector<uint32_t> statuses(status.bytes / 4, UINT32_MAX);
        for (uint32_t token = 0; token < tokens; ++token) {
            SeedRow(initial, config.partialOffset + uint64_t(token) * hidden * 2, rank, token);
        }
        for (uint32_t round = 0; round < 4; ++round) {
            Check(TileXRCommNextMagic(comm, &config.magic), "magic");
            StoreMagic(initial, config.readyOffset, config.magic);
            StoreMagic(initial, config.readyOffset + 8, config.chunks);
            if (round == 0) {
                workspace.Upload(initial.data());
            } else {
                Check(aclrtMemcpy(base + config.readyOffset, 16, initial.data() + config.readyOffset,
                                  16, ACL_MEMCPY_HOST_TO_DEVICE), "publish ready");
            }
            std::fill(statuses.begin(), statuses.end(), UINT32_MAX);
            status.Upload(statuses.data());
            std::cout << "LAUNCH_READY " << round << std::endl;
            std::string go;
            Require(bool(std::getline(std::cin, go)) && go == "go", "launch barrier");
            Check(aclrtRecordEvent(start, stream), "record start");
            if (send) {
                NativeSendArgs args {reinterpret_cast<uint64_t>(commArgs), reinterpret_cast<uint64_t>(base),
                                     ranges.Address(), status.Address(), config};
                kernel->Launch(args, cores, stream);
            } else if (direct) {
                LocalReturnArgs args {reinterpret_cast<uint64_t>(commHost->udmaInfoPtr),
                    reinterpret_cast<uint64_t>(registry), reinterpret_cast<uint64_t>(base), forward.Address(),
                    counts.Address(), ranges.Address(), status.Address(), config, ready, signal, 0, directBase, 1, 1};
                kernel->Launch(args, ranks, stream);
            } else {
                Check(TileXREpHtLocalReturn(comm, base, static_cast<int32_t *>(forward.ptr),
                          static_cast<int32_t *>(counts.ptr), static_cast<int32_t *>(ranges.ptr),
                          static_cast<uint32_t *>(status.ptr), &config, ready, signal, stream), "LocalReturn");
            }
            Check(aclrtRecordEvent(stop, stream), "record stop");
            Check(aclrtSynchronizeStream(stream), "synchronize");
            float ms = 0;
            Check(aclrtEventElapsedTime(&ms, start, stop), "elapsed");
            status.Download(statuses.data());
            for (uint32_t owner = 0; owner < (send ? cores : ranks); ++owner) {
                Check(statuses.at(owner * 8), "AIV status");
            }
            // The process launcher inserts a host barrier here before downloading.
            std::cout << "ROUND_READY " << round << std::endl;
            Require(bool(std::getline(std::cin, go)) && go == "go", "round barrier");
            workspace.Download(actual.data());
            const auto sentinel = [](uint8_t value) { return value == 0xa5; };
            Require(std::all_of(actual.end() - guard, actual.end(), sentinel), "guard");
            for (uint32_t token = 0; token < tokens; ++token) {
                const uint32_t sender = send ? token / (tokens / ranks) : (token + rank) % ranks;
                if (!send || sender != rank) {
                    ExpectRow(actual, ReturnOffset(config, token, 0), sender, token);
                }
            }
            for (uint32_t sender = 0; sender < ranks; ++sender) {
                if (send) {
                    if (sender == rank) {
                        continue;
                    }
                    for (uint32_t q = 0; q < cores * kLanes; ++q) {
                        ExpectMagic(actual, config.completionOffset +
                            (sender * cores * kLanes + q) * kSlotBytes, config.magic);
                    }
                } else {
                    for (uint32_t step = 0; step < SyncStepCount(config); ++step) {
                        ExpectMagic(actual, ready + LocalReturnReadyOffset(config, sender, step), config.magic);
                    }
                }
            }
            std::cout << "MODULE_RESULT " << round << ' ' << SyncStepCount(config) << ' '
                      << LocalReturnSlotStride(config) << ' ' << ms * 1000 << std::endl;
        }
        Check(TileXRUDMAUnregister(comm, handle), "unregister");
    }
    Check(TileXRCommDestroy(comm), "destroy");
    Check(aclrtDestroyEvent(start), "destroy start");
    Check(aclrtDestroyEvent(stop), "destroy stop");
    Check(aclrtDestroyStream(stream), "destroy stream");
    Check(aclrtResetDevice(rank), "reset");
    Check(aclFinalize(), "finalize");
}
} // namespace

int main(int argc, char **argv)
{
    try {
        Require(argc == 7, "mode rank tokens sync cores binary");
        const std::string mode = argv[1];
        Run(std::stoul(argv[2]), std::stoul(argv[3]), std::stoul(argv[4]), mode == "send",
            mode == "direct", std::stoul(argv[5]), argv[6]);
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
