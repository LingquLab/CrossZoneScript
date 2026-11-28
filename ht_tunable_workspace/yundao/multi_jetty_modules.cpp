/* Copyright (c) 2026 TileXR Project.
 * Run the standalone native LocalReturn and FinalReduce kernels on one host.
 */
#include <acl/acl.h>
#include <tilexr/ep/final_reduce.h>
#include <tilexr/ep/local_return.h>

#include <algorithm>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace TileXREpHt;

namespace {
constexpr uint32_t kHidden = 128, kLocal = 8, kGuard = 512, kRounds = 4;

void Check(int code, const char *step)
{
    if (code != 0) {
        throw std::runtime_error(std::string(step) + " code=" + std::to_string(code));
    }
}

struct Buffer {
    void *ptr = nullptr;
    size_t size;
    explicit Buffer(size_t bytes) : size(bytes)
    {
        Check(aclrtMalloc(&ptr, size, ACL_MEM_MALLOC_HUGE_FIRST), "allocate");
    }
    ~Buffer() { aclrtFree(ptr); }
    void Upload(const void *data) const
    {
        Check(aclrtMemcpy(ptr, size, data, size, ACL_MEMCPY_HOST_TO_DEVICE), "upload");
    }
    void Download(void *data) const
    {
        Check(aclrtMemcpy(data, size, ptr, size, ACL_MEMCPY_DEVICE_TO_HOST), "download");
    }
};

template <typename T>
void Put(std::vector<uint8_t> &bytes, size_t offset, T value)
{
    std::memcpy(bytes.data() + offset, &value, sizeof(T));
}

template <typename T>
T Get(const std::vector<uint8_t> &bytes, size_t offset)
{
    T value;
    std::memcpy(&value, bytes.data() + offset, sizeof(T));
    return value;
}

void Barrier(const char *label, uint32_t round)
{
    std::cout << label << ' ' << round << std::endl;
    std::string command;
    Check(!(std::getline(std::cin, command) && command == "go"), "host barrier");
}

uint16_t Payload(uint32_t sender, uint32_t token, uint32_t column)
{
    return uint16_t(0x3e00U + (sender * 79 + token * 37 + column) % 512);
}

void LocalReturn(SendConfig c, aclrtStream stream)
{
    TileXRCommPtr comm = nullptr;
    Check(TileXRCommInitRankWithSharedQpDomain(0, kLocal, c.rank, &comm), "communicator");
    const uint64_t ready = c.workspaceBytes, signal = ready + LocalReadyBytes(c);
    const uint64_t used = signal + kLocal * kSlotBytes;
    Buffer workspace(used + kGuard);
    TileXRUDMAMemHandle registration = 0;
    Check(TileXRUDMARegister(comm, static_cast<GM_ADDR>(workspace.ptr), used, &registration), "register");
    Buffer forward(uint64_t(c.world) * c.forwardCapacity * kForwardRecordWords * sizeof(int32_t));
    Buffer counts(c.world * sizeof(int32_t)), ranges(uint64_t(c.world) * c.chunks * 2 * sizeof(int32_t));
    Buffer status(kLocal * kFinalReduceStatusStride * sizeof(uint32_t));
    std::vector<int32_t> records(forward.size / 4), count(c.world), range(ranges.size / 4);
    for (uint32_t peer = 0; peer < kLocal; ++peer) {
        for (uint32_t chunk = 0, token = 0; chunk < c.chunks; ++chunk) {
            const size_t at = (peer * c.chunks + chunk) * 2;
            range[at] = count[peer];
            for (; token < ChunkEnd(c, chunk); ++token) {
                if ((token + peer) % kLocal != c.rank) {
                    continue;
                }
                const uint32_t index = count[peer]++;
                auto *row = records.data() + (peer * c.forwardCapacity + index) * kForwardRecordWords;
                row[0] = index + 1;
                row[1] = token;
                row[2] = token;
            }
            range[at + 1] = count[peer];
        }
    }
    forward.Upload(records.data());
    counts.Upload(count.data());
    ranges.Upload(range.data());
    std::vector<uint8_t> seed(workspace.size), result(workspace.size);
    std::fill(seed.begin() + used, seed.end(), 0xa5);
    std::vector<uint32_t> codes(status.size / 4);
    for (uint32_t token = 0; token < c.tokens; ++token) {
        for (uint32_t col = 0; col < kHidden; ++col) {
            Put(seed, c.partialOffset + (uint64_t(token) * kHidden + col) * 2, Payload(c.rank, token, col));
        }
    }
    aclrtEvent start = nullptr, end = nullptr;
    Check(aclrtCreateEvent(&start), "start event");
    Check(aclrtCreateEvent(&end), "end event");
    for (uint32_t round = 0; round < kRounds; ++round) {
        Check(TileXRCommNextMagic(comm, &c.magic), "magic");
        for (uint32_t chunk = 0; chunk < c.chunks; ++chunk) {
            const uint64_t at = ReadyOffset(c, 0, (c.groups - 1) * c.chunks + chunk);
            Put(seed, at, c.magic);
        }
        if (round == 0) {
            workspace.Upload(seed.data());
        } else {
            auto *base = static_cast<uint8_t *>(workspace.ptr);
            const uint64_t at = ReadyOffset(c, 0, (c.groups - 1) * c.chunks);
            const size_t bytes = c.chunks * sizeof(int64_t);
            Check(aclrtMemcpy(base + at, bytes, seed.data() + at, bytes, ACL_MEMCPY_HOST_TO_DEVICE), "update ready");
        }
        std::fill(codes.begin(), codes.end(), UINT32_MAX);
        status.Upload(codes.data());
        Barrier("LAUNCH_READY", round);
        Check(aclrtRecordEvent(start, stream), "record start");
        Check(TileXREpHtLocalReturn(comm, workspace.ptr, static_cast<int32_t *>(forward.ptr),
                  static_cast<int32_t *>(counts.ptr), static_cast<int32_t *>(ranges.ptr),
                  static_cast<uint32_t *>(status.ptr), &c, ready, signal, stream), "LocalReturn launch");
        Check(aclrtRecordEvent(end, stream), "record end");
        Check(aclrtSynchronizeStream(stream), "LocalReturn synchronize");
        float ms = 0;
        Check(aclrtEventElapsedTime(&ms, start, end), "elapsed");
        status.Download(codes.data());
        for (uint32_t index = 0; index < kLocal; ++index) {
            Check(codes[index * kFinalReduceStatusStride], "LocalReturn status");
        }
        Barrier("ROUND_READY", round);
        workspace.Download(result.data());
        for (uint32_t token = 0; token < c.tokens; ++token) {
            for (uint32_t col = 0; col < kHidden; ++col) {
                const uint16_t actual = Get<uint16_t>(result, ReturnOffset(c, token, 0) + col * 2);
                Check(actual != Payload((token + c.rank) % kLocal, token, col), "LocalReturn payload mismatch");
            }
        }
        for (uint32_t peer = 0; peer < kLocal; ++peer) {
            for (uint32_t chunk = 0; chunk < c.chunks; ++chunk) {
                const auto at = ready + LocalReturnReadyOffset(peer, chunk, LocalReadyStride(c));
                Check(Get<int64_t>(result, at) != c.magic, "LocalReturn ready mismatch");
            }
        }
        Check(!std::equal(result.begin() + used, result.end(), seed.begin() + used), "LocalReturn guard mismatch");
        std::cout << "MODULE_RESULT " << round << ' ' << c.chunks << ' ' << LocalReadyStride(c) << ' ' << ms * 1000
                  << std::endl;
    }
    Check(aclrtDestroyEvent(start), "destroy start event");
    Check(aclrtDestroyEvent(end), "destroy end event");
    Check(TileXRUDMAUnregister(comm, registration), "unregister");
    Check(TileXRCommDestroy(comm), "destroy communicator");
}

uint16_t Bf16(float value)
{
    uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return uint16_t((bits + 0x7fffU + ((bits >> 16) & 1U)) >> 16);
}

void FinalReduce(const SendConfig &c, aclrtStream stream)
{
    constexpr uint32_t blocks = 16;
    std::vector<uint8_t> seed(c.workspaceBytes), ready(LocalReadyBytes(c));
    std::vector<uint64_t> masks(c.tokens, 255);
    for (uint32_t token = 0; token < c.tokens; ++token) {
        for (uint32_t server = 0; server < kLocal; ++server) {
            for (uint32_t col = 0; col < kHidden; ++col) {
                Put(seed, ReturnOffset(c, token, server * kLocal) + 2 * col,
                    Bf16(float(1 + server + (token + col) % 8)));
            }
        }
    }
    for (uint32_t chunk = 0; chunk < c.chunks; ++chunk) {
        for (uint32_t gateway = 0; gateway < c.world; ++gateway) {
            for (uint32_t jetty = 0; jetty < TotalJetties(c); ++jetty) {
                Put(seed, CompletionOffset(c, gateway, chunk, jetty), c.magic);
            }
        }
        for (uint32_t gateway = 0; gateway < kLocal; ++gateway) {
            Put(ready, LocalReturnReadyOffset(gateway, chunk, LocalReadyStride(c)), c.magic);
        }
    }
    const size_t outputBytes = size_t(c.tokens) * kHidden * 2;
    Buffer workspace(seed.size()), flags(ready.size()), serverMask(masks.size() * sizeof(uint64_t));
    Buffer output(outputBytes + 2 * kGuard), status(blocks * kFinalReduceStatusStride * sizeof(uint32_t));
    std::vector<uint8_t> actual(output.size, 0xa5);
    std::vector<uint32_t> codes(status.size / 4, UINT32_MAX);
    workspace.Upload(seed.data());
    flags.Upload(ready.data());
    serverMask.Upload(masks.data());
    output.Upload(actual.data());
    status.Upload(codes.data());
    Check(TileXREpHtFinalReduce(workspace.ptr, static_cast<uint64_t *>(serverMask.ptr), flags.ptr,
              static_cast<uint8_t *>(output.ptr) + kGuard, static_cast<uint32_t *>(status.ptr), &c, blocks, stream),
          "FinalReduce launch");
    Check(aclrtSynchronizeStream(stream), "FinalReduce synchronize");
    output.Download(actual.data());
    status.Download(codes.data());
    for (uint32_t block = 0; block < blocks; ++block) {
        Check(codes[block * kFinalReduceStatusStride], "FinalReduce status");
    }
    for (uint32_t token = 0; token < c.tokens; ++token) {
        for (uint32_t col = 0; col < kHidden; ++col) {
            const auto val = Get<uint16_t>(actual, kGuard + (uint64_t(token) * kHidden + col) * 2);
            Check(val != Bf16(float(36 + 8 * ((token + col) % 8))), "FinalReduce output mismatch");
        }
    }
    const auto intact = [](uint8_t byte) { return byte == 0xa5; };
    Check(!std::all_of(actual.begin(), actual.begin() + kGuard, intact) ||
          !std::all_of(actual.end() - kGuard, actual.end(), intact), "FinalReduce guard mismatch");
    std::cout << "FINAL_RESULT " << c.tokens * kHidden << ' ' << blocks << ' ' << c.chunks << " 2\n";
}
} // namespace

int main(int argc, char **argv)
{
    try {
        Check(argc != 6 && argc != 7, "expected mode device tokens chunk splits [intra cores]");
        const std::string mode = argv[1];
        const uint32_t device = std::stoul(argv[2]), tokens = std::stoul(argv[3]);
        const uint32_t chunk = std::stoul(argv[4]), splits = std::stoul(argv[5]);
        const uint32_t intraCores = argc == 7 ? std::stoul(argv[6]) : kLocal;
        SendConfig config {};
        Check(!MakeSendConfig(mode == "final" ? 0 : device, tokens, kHidden, 1, 0, tokens, 2026, config,
                              64, 1, chunk, splits, kOwners, 1, 1, 1, 1, intraCores), "layout");
        Check(aclInit(nullptr), "acl init");
        Check(aclrtSetDevice(device), "set device");
        aclrtStream stream = nullptr;
        Check(aclrtCreateStream(&stream), "create stream");
        if (mode == "final") {
            FinalReduce(config, stream);
        } else {
            LocalReturn(config, stream);
        }
        Check(aclrtDestroyStream(stream), "destroy stream");
        Check(aclrtResetDevice(device), "reset device");
        Check(aclFinalize(), "finalize");
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
