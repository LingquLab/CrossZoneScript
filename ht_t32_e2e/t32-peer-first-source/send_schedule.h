/*
 * Copyright (c) 2026 TileXR Project.
 * 顺序扫描 forward token run，按 owner/lane 调用传输接口；Host 模型和设备复用此流程。
 * 路由只读，游标与 ordinal 由当前 worker 私有持有，Submit 后必须调用 Drain。
 */
#ifndef TILEXR_EP_HT_SEND_SCHEDULE_H
#define TILEXR_EP_HT_SEND_SCHEDULE_H
#include <tilexr/ep/inter_server_send_types.h>
#if defined(__CCE__) && defined(__CCE_IS_AICORE__)
#define HT_INLINE __aicore__ inline
#else
#define HT_INLINE inline
#endif
namespace TileXREpHt
{
enum Status : uint32_t
{
    Success = 0,
    InvalidRoute = 1,
    ReadyTimeout = 2,
    TransportError = 3
};
struct Record
{
    int32_t end, token, primary, contributor, inputRow, gatherRow;
};
struct Range
{
    int32_t begin, end;
};

// The EP64 peer order starts with the paired server in the opposite half.
HT_INLINE uint32_t Owner(const SendConfig &c, uint32_t peer, uint32_t chunk)
{
    const uint32_t localServer = c.rank / kRanksPerServer;
    const uint32_t peerServer = peer / kRanksPerServer;
    if (peer >= kWorld || peerServer == localServer) {
        return kOwners;
    }

    constexpr uint32_t serversPerHalf = kWorld / (2 * kRanksPerServer);
    constexpr uint32_t pairedServers = 2 * kRanksPerServer;
    const uint32_t group = (peerServer + serversPerHalf - localServer % serversPerHalf) % serversPerHalf;
    const uint32_t localPosition =
        (peer % kRanksPerServer + kRanksPerServer - c.rank % kRanksPerServer) % kRanksPerServer;
    uint32_t peerGroupStart = 0;
    if (group != 0) {
        peerGroupStart = kRanksPerServer + (group - 1) * pairedServers + peerServer / serversPerHalf * kRanksPerServer;
    }
    const uint32_t position = peerGroupStart + localPosition;
    constexpr uint32_t fixedPeers = kWorld - 2 * kRanksPerServer;
    if (position >= fixedPeers) {
        return localPosition + LogicalChunk(c, chunk) % 2 * (kOwners / 2);
    }
    return (position + kOwners / 2) % kOwners;
}
HT_INLINE bool NotifyRequired(const SendConfig &c, uint32_t peer, uint32_t chunk)
{
    const uint32_t end = WindowEnd(c, chunk);
    return chunk == end || (chunk + 1 == end && Owner(c, peer, chunk) != Owner(c, peer, end));
}

constexpr uint32_t kMaxPeersPerOwner = (kWorld - kRanksPerServer + kOwners - 1) / kOwners;
constexpr uint32_t kRowsPerPeerVisit = 8;
constexpr uint32_t kLanePeriod = 4;

struct PeerSchedule {
    uint32_t peer, ownerByParity[2], ordinal;
    Range range;
    int32_t validRows;
};

template <class Transport>
HT_INLINE uint32_t SubmitPeerRows(
    const SendConfig &c, PeerSchedule &peer, uint32_t tokenBegin, uint32_t tokenEnd, Transport &transport)
{
    uint32_t sent[kLanes] = {};
    while (peer.range.begin < peer.range.end && sent[0] < kRowsPerPeerVisit && sent[1] < kRowsPerPeerVisit) {
        const Record r = transport.LoadRecord(peer.peer, peer.range.begin);
        if (r.end <= peer.range.begin || r.end > peer.range.end || r.token < 0 || uint32_t(r.token) < tokenBegin ||
            uint32_t(r.token) >= tokenEnd || r.primary < 0 ||
            uint32_t(r.primary) >= (r.contributor < 0 ? c.inputRows : c.partialRows)) {
            return InvalidRoute;
        }
        const uint32_t lane = peer.ordinal % kLanePeriod == kLanePeriod - 1 ? 1U : 0U;
        const uint32_t status = transport.Add(peer.peer, lane, r);
        if (status != Success)
        {
            return status;
        }
        peer.range.begin = r.end;
        ++peer.ordinal;
        ++sent[lane];
    }
    return Success;
}

template <class Transport>
HT_INLINE uint32_t SubmitChunk(const SendConfig &c, uint32_t owner, PeerSchedule *peers, uint32_t peerCount,
    uint32_t chunk, uint32_t tokenBegin, uint32_t tokenEnd, Transport &transport)
{
    uint32_t status = transport.WaitReady(chunk);
    if (status != Success) {
        return status;
    }

    const uint32_t parity = LogicalChunk(c, chunk) % 2;
    PeerSchedule *activePeers[kMaxPeersPerOwner];
    uint32_t activeCount = 0;
    for (uint32_t i = 0; i < peerCount; ++i) {
        auto &peer = peers[i];
        if (peer.ownerByParity[parity] != owner) {
            continue;
        }
        const Range range = transport.LoadRange(peer.peer, i, chunk);
        if (peer.validRows < 0) {
            peer.validRows = transport.LoadCount(peer.peer);
        }
        const int32_t valid = peer.validRows;
        if (range.begin < 0 || range.end < range.begin || range.end > valid || valid < 0 ||
            uint32_t(valid) > c.forwardCapacity) {
            return InvalidRoute;
        }
        peer.range = range;
        activePeers[activeCount++] = &peer;
    }

    for (uint32_t i = 0; i < activeCount; ++i) {
        auto &peer = *activePeers[i];
        while (peer.range.begin < peer.range.end) {
            status = SubmitPeerRows(c, peer, tokenBegin, tokenEnd, transport);
            if (status != Success) {
                return status;
            }
        }
        status = transport.Flush();
        if (status != Success) {
            return status;
        }
        for (uint32_t lane = 0; lane < kLanes; ++lane) {
            status = transport.Notify(peer.peer, lane, chunk);
            if (status != Success) {
                return status;
            }
        }
    }
    return Success;
}

template <class Transport>
HT_INLINE uint32_t Submit(const SendConfig &c, uint32_t owner, Transport &transport)
{
    PeerSchedule peers[kMaxPeersPerOwner];
    uint32_t count = 0;
    for (uint32_t peer = 0; peer < kWorld; ++peer) {
        const uint32_t firstOwner = Owner(c, peer, 0);
        const uint32_t secondOwner = c.tokens > kTokensPerChunk ? Owner(c, peer, FirstSplits(c.tokens)) : firstOwner;
        if (firstOwner == owner || secondOwner == owner) {
            peers[count++] = {peer, {firstOwner, secondOwner}, 0, {}, -1};
        }
    }
    uint32_t tokenBegin = 0;
    for (uint32_t chunk = 0; chunk < c.chunks; ++chunk) {
        const uint32_t tokenEnd = ChunkEnd(c, chunk);
        const uint32_t status = SubmitChunk(c, owner, peers, count, chunk, tokenBegin, tokenEnd, transport);
        if (status != Success) {
            return status;
        }
        tokenBegin = tokenEnd;
    }
    return Success;
}
template <class Transport> HT_INLINE uint32_t Drain(Transport &transport) { return transport.Drain(); }
} // namespace TileXREpHt
#undef HT_INLINE
#endif
