/*
 * Copyright (c) 2026 TileXR Project.
 * 定义 HT partial 返回的共享 ABI、工作区布局和 Host 启动接口。
 * 调用方持有已注册工作区、路由表和本轮 magic，直到 stream 完成。
 */
#ifndef TILEXR_EP_INTER_SERVER_SEND_TYPES_H
#define TILEXR_EP_INTER_SERVER_SEND_TYPES_H
#include <cstddef>
#include <cstdint>

#if defined(__CCE__) && defined(__CCE_IS_AICORE__)
#define HT_ABI_INLINE __aicore__ inline
#else
#define HT_ABI_INLINE inline
#endif
namespace TileXREpHt
{
constexpr uint32_t kWorld = 64;
constexpr uint32_t kRanksPerServer = 8;
constexpr uint32_t kOwners = 16;
constexpr uint32_t kLanes = 2;
constexpr uint32_t kReducers = 40;
constexpr uint32_t kSlotBytes = 512;
constexpr uint32_t kTokensPerChunk = 256;
constexpr uint32_t kMaxFirstSplits = 1;
constexpr uint32_t kMaxCompletionWindows = 3;
constexpr uint32_t kServerCount = kWorld / kRanksPerServer;

struct SendConfig
{
    uint32_t rank, tokens, hidden, topk;
    uint32_t inputRows, partialRows, forwardCapacity, chunks;
    uint32_t reducerCount, reserved;
    uint64_t inputOffset, partialOffset, readyOffset, completionOffset;
    uint64_t returnOffset, signalOffset, workspaceBytes, waitIterations;
    int64_t magic;
};
static_assert(sizeof(SendConfig) == 112, "HT config ABI");

// Six GM addresses followed by a by-value SendConfig; loader uses this exact order.
struct SendKernelArgs
{
    uint64_t commArgs, workspace, forward, counts, ranges, status;
    SendConfig config;
};
static_assert(offsetof(SendKernelArgs, config) == 48 && sizeof(SendKernelArgs) == 160, "HT entry ABI");

inline uint64_t Align512(uint64_t bytes) { return (bytes + 511U) / 512U * 512U; }
HT_ABI_INLINE uint32_t FirstSplits(uint32_t tokens)
{
    return tokens < kMaxFirstSplits ? tokens : kMaxFirstSplits;
}
inline uint32_t ChunkCount(uint32_t tokens)
{
    return (tokens + kTokensPerChunk - 1) / kTokensPerChunk + (tokens < kMaxFirstSplits ? tokens : kMaxFirstSplits) - 1;
}
HT_ABI_INLINE uint32_t ReturnSlots(uint32_t topk)
{
    return topk < kServerCount ? kServerCount : topk;
}

// All data used as a UDMA source is placed inside this registered workspace.
inline bool MakeSendConfig(uint32_t rank, uint32_t tokens, uint32_t hidden, uint32_t topk, uint32_t inputRows,
                           uint32_t partialRows, int64_t magic, SendConfig &c)
{
    if (rank >= kWorld || !tokens || tokens > 16384 || !hidden || hidden > 7168 || hidden % 16 || !topk || topk > 16 ||
        magic <= 0 || inputRows > uint64_t(kWorld) * tokens * topk || partialRows > uint64_t(kWorld) * tokens * topk) {
        return false;
    }
    c = {};
    c.rank = rank;
    c.tokens = tokens;
    c.hidden = hidden;
    c.topk = topk;
    c.inputRows = inputRows;
    c.partialRows = partialRows;
    c.forwardCapacity = tokens * topk;
    c.chunks = ChunkCount(tokens);
    c.reducerCount = kReducers;
    c.magic = magic;
    c.waitIterations = 1000000;
    c.inputOffset = 0;
    c.partialOffset = Align512(uint64_t(inputRows) * hidden * 2);
    c.readyOffset = c.partialOffset + Align512(uint64_t(partialRows) * hidden * 2);
    c.completionOffset = c.readyOffset + uint64_t(c.reducerCount) * kSlotBytes;
    c.returnOffset = c.completionOffset + uint64_t(kWorld) * c.chunks * kLanes * kSlotBytes;
    const uint32_t returnSlots = topk < kServerCount ? kServerCount : topk;
    c.signalOffset = c.returnOffset + Align512(uint64_t(tokens) * returnSlots * hidden * 2);
    c.workspaceBytes = c.signalOffset + uint64_t(kOwners) * kSlotBytes;
    return true;
}

HT_ABI_INLINE uint32_t LogicalChunk(const SendConfig &c, uint32_t chunk)
{
    const uint32_t splits = FirstSplits(c.tokens);
    return chunk < splits ? 0U : chunk - splits + 1;
}
HT_ABI_INLINE uint32_t ChunkEnd(const SendConfig &c, uint32_t chunk)
{
    const uint32_t first = c.tokens < kTokensPerChunk ? c.tokens : kTokensPerChunk;
    const uint32_t splits = FirstSplits(c.tokens);
    const uint32_t end =
        chunk < splits ? ((chunk + 1) * first + splits - 1) / splits : (LogicalChunk(c, chunk) + 1) * kTokensPerChunk;
    return end < c.tokens ? end : c.tokens;
}
HT_ABI_INLINE uint32_t WindowEnd(const SendConfig &c, uint32_t chunk)
{
    const uint32_t logicalCount = (c.tokens + kTokensPerChunk - 1) / kTokensPerChunk;
    const uint32_t windows = logicalCount < kMaxCompletionWindows ? logicalCount : kMaxCompletionWindows;
    const uint32_t logical = LogicalChunk(c, chunk);
    uint32_t end;
    if (windows == kMaxCompletionWindows && logicalCount >= 4)
    {
        const uint32_t half = (logicalCount + 1) / 2;
        const uint32_t threeQuarters = half + (logicalCount - half + 1) / 2;
        end = logical < half ? half : (logical < threeQuarters ? threeQuarters : logicalCount);
    }
    else
    {
        const uint32_t window = logical * windows / logicalCount;
        end = ((window + 1) * logicalCount + windows - 1) / windows;
    }
    return end - 1 + FirstSplits(c.tokens) - 1;
}

// Offsets are for the registered workspace of each peer; all ranks use identical shapes.
HT_ABI_INLINE uint64_t CompletionOffset(const SendConfig &c, uint32_t gateway, uint32_t chunk, uint32_t lane)
{
    return c.completionOffset + ((uint64_t(gateway) * c.chunks + chunk) * kLanes + lane) * kSlotBytes;
}
HT_ABI_INLINE uint64_t ReturnOffset(const SendConfig &c, uint32_t token, uint32_t gateway,
                                   uint32_t elementBytes = sizeof(uint16_t))
{
    return c.returnOffset + (uint64_t(token) * ReturnSlots(c.topk) + gateway / kRanksPerServer) * c.hidden * elementBytes;
}
} // namespace TileXREpHt
#undef HT_ABI_INLINE

#endif
