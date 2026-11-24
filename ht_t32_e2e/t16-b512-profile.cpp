/*
 * Copyright (c) 2026 TileXR Project.
 * 调用共享调度器，将已注册工作区中的 BF16 partial 写回源 rank，并发送 magic 完成通知。
 * 每个 AIV 独占一个 owner 的双 lane；工作区和通信资源由调用方保持到 kernel 完成。
 */
#include "kernel_operator.h"
#include "send_schedule.h"
#include <tilexr/comm/udma/device/api.h>

using namespace AscendC;
namespace {
using namespace TileXREpHt;

enum StageId {
    Kernel, SendSetup, SendSubmit, SendDrain, LocalReady, RouteRead, PostBatch, Doorbell, SubmitSO, WaitCQ,
    ReduceSetup, ReduceTotal, WaitRemoteSO, ReduceRows, LoadPartial, AddPartial, StoreOutput, WriteStatus
};
class StageTrace {
public:
    static constexpr uint32_t Bytes = 16384;
    __aicore__ void Init(TPipe &pipe)
    {
        pipe.InitBuffer(buffer_, Bytes);
        data_ = buffer_.Get<uint64_t>();
    }
    __aicore__ void Mark(uint32_t event)
    {
        const auto cycle = static_cast<uint64_t>(AscendC::GetSystemCycle());
        if (cursor_ + 2 <= Bytes / sizeof(uint64_t)) {
            data_.SetValue(cursor_++, cycle);
            data_.SetValue(cursor_++, event);
        } else {
            ++lost_;
        }
    }
    __aicore__ void Export(GM_ADDR address)
    {
        data_.SetValue(0, cursor_);
        data_.SetValue(1, lost_);
        if (cursor_ % 4 != 0) {
            data_.SetValue(cursor_, 0);
            data_.SetValue(cursor_ + 1, 0);
        }
        GlobalTensor<uint64_t> target;
        target.SetGlobalBuffer(reinterpret_cast<__gm__ uint64_t *>(address));
        SetFlag<HardEvent::S_MTE3>(EVENT_ID1);
        WaitFlag<HardEvent::S_MTE3>(EVENT_ID1);
        DataCopyPad(target, data_, DataCopyExtParams(1, ((cursor_ + 3) & ~3U) * sizeof(uint64_t), 0, 0, 0));
        SetFlag<HardEvent::MTE3_S>(EVENT_ID1);
        WaitFlag<HardEvent::MTE3_S>(EVENT_ID1);
    }
private:
    TBuf<QuePosition::VECCALC> buffer_;
    LocalTensor<uint64_t> data_;
    uint32_t cursor_ = 2;
    uint32_t lost_ = 0;
};

class StageScope {
public:
    __aicore__ StageScope(StageTrace &trace, StageId id) : trace_(trace), event_(2 * id + 1)
    {
        trace_.Mark(event_);
    }
    __aicore__ ~StageScope() { trace_.Mark(event_ + 1); }
private:
    StageTrace &trace_;
    uint32_t event_;
};

class DeviceTransport {
public:
    __aicore__ DeviceTransport(GM_ADDR comm, GM_ADDR workspace, GM_ADDR forward, GM_ADDR counts, GM_ADDR ranges,
        const SendConfig &config, uint32_t owner, TPipe &pipe, StageTrace &trace)
        : args_(reinterpret_cast<__gm__ TileXR::CommArgs *>(comm)), workspace_(workspace), forward_(forward),
          counts_(counts), ranges_(ranges), c_(config), owner_(owner), trace_(trace)
    {
        pipe.InitBuffer(metadata_, 512);
        pipe.InitBuffer(rangesCache_, kMaxPeersPerOwner * RangeCacheStride() * sizeof(int32_t));
        pipe.InitBuffer(ready_, kReducers * kUbBlockBytes);
        pipe.InitBuffer(forwardCache_, kMaxPeersPerOwner * kForwardPageRows * kUbBlockBytes);
        pipe.InitBuffer(wqe_, kBatchRows * TileXR::TILEXR_UDMA_WQE_BB_BYTES);
        auto source = metadata_.Get<int64_t>();
        source.SetValue(0, c_.magic);
        GlobalTensor<int64_t> gm;
        gm.SetGlobalBuffer(
            reinterpret_cast<__gm__ int64_t *>(workspace_ + c_.signalOffset + uint64_t(owner_) * kSlotBytes));
        SetFlag<HardEvent::S_MTE3>(EVENT_ID1);
        WaitFlag<HardEvent::S_MTE3>(EVENT_ID1);
        DataCopyPad(gm, source, DataCopyExtParams(1, 8, 0, 0, 0));
        SetFlag<HardEvent::MTE3_S>(EVENT_ID1);
        WaitFlag<HardEvent::MTE3_S>(EVENT_ID1);
    }
    template <class T>
    __aicore__ LocalTensor<T> Load(GM_ADDR address, uint32_t count, LocalTensor<T> ub)
    {
        GlobalTensor<T> gm;
        gm.SetGlobalBuffer(reinterpret_cast<__gm__ T *>(address));
        gm.SetL2CacheHint(CacheMode::CACHE_MODE_DISABLE);
        SetFlag<HardEvent::S_MTE2>(EVENT_ID0);
        WaitFlag<HardEvent::S_MTE2>(EVENT_ID0);
        DataCopyPad(ub, gm, DataCopyExtParams(1, count * sizeof(T), 0, 0, 0), DataCopyPadExtParams<T> {false, 0, 0, 0});
        SetFlag<HardEvent::MTE2_S>(EVENT_ID0);
        WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);
        return ub;
    }
    __aicore__ uint32_t RangeCacheStride() const
    {
        constexpr uint32_t wordsPerBlock = kUbBlockBytes / sizeof(int32_t);
        return (c_.chunks * 2 + wordsPerBlock - 1) / wordsPerBlock * wordsPerBlock;
    }
    __aicore__ Range LoadRange(uint32_t peer, uint32_t slot, uint32_t chunk)
    {
        StageScope timing(trace_, RouteRead);  // Perfetto 11 begin, 12 end (send_read_routes)
        cachePeer_[slot] = peer;
        auto ranges = rangesCache_.Get<int32_t>()[slot * RangeCacheStride()];
        const uint32_t mask = uint32_t(1) << slot;
        if (!(loadedRanges_ & mask)) {
            Load<int32_t>(ranges_ + uint64_t(peer) * c_.chunks * 8, c_.chunks * 2, ranges);
            loadedRanges_ |= mask;
        }
        return {ranges.GetValue(chunk * 2), ranges.GetValue(chunk * 2 + 1)};
    }
    __aicore__ uint32_t CacheSlot(uint32_t peer) const
    {
        for (uint32_t slot = 0; slot < kMaxPeersPerOwner; ++slot) {
            if (cachePeer_[slot] == peer) {
                return slot;
            }
        }
        return kMaxPeersPerOwner;
    }
    __aicore__ int32_t LoadCount(uint32_t peer)
    {
        StageScope timing(trace_, RouteRead);  // Perfetto 11 begin, 12 end (send_read_routes)
        const int32_t count = Load<int32_t>(counts_ + peer * 4, 1, metadata_.Get<int32_t>()).GetValue(0);
        const uint32_t slot = CacheSlot(peer);
        if (slot < kMaxPeersPerOwner) {
            validRows_[slot] = count;
        }
        return count;
    }
    __aicore__ Record LoadRecord(uint32_t peer, uint32_t row)
    {
        StageScope timing(trace_, RouteRead);  // Perfetto 11 begin, 12 end (send_read_routes)
        const uint32_t slot = CacheSlot(peer);
        const uint32_t first = row / kForwardPageRows * kForwardPageRows;
        auto page = forwardCache_.Get<int32_t>()[slot * kForwardPageRows * kUbBlockBytes / sizeof(int32_t)];
        if (!pageLoaded_[slot] || pageBegin_[slot] != first) {
            const uint32_t remaining = uint32_t(validRows_[slot]) - first;
            const uint32_t rows = remaining < kForwardPageRows ? remaining : kForwardPageRows;
            GlobalTensor<int32_t> gm;
            gm.SetGlobalBuffer(reinterpret_cast<__gm__ int32_t *>(
                forward_ + (uint64_t(peer) * c_.forwardCapacity + first) * kUbBlockBytes));
            gm.SetL2CacheHint(CacheMode::CACHE_MODE_DISABLE);
            SetFlag<HardEvent::S_MTE2>(EVENT_ID0);
            WaitFlag<HardEvent::S_MTE2>(EVENT_ID0);
            // The public records remain packed 24B. Each independent source
            // block is padded to a 32B UB row, including the bounded last page.
            DataCopyPad(page, gm, DataCopyExtParams(rows, kUbBlockBytes, 0, 0, 0),
                DataCopyPadExtParams<int32_t> {false, 0, 0, 0});
            SetFlag<HardEvent::MTE2_S>(EVENT_ID0);
            WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);
            pageBegin_[slot] = first;
            pageLoaded_[slot] = true;
        }
        const uint32_t at = (row - first) * kUbBlockBytes / sizeof(int32_t);
        return {page.GetValue(at), page.GetValue(at + 1), page.GetValue(at + 2), page.GetValue(at + 3),
            page.GetValue(at + 4), page.GetValue(at + 5)};
    }
    __aicore__ uint32_t WaitReady(uint32_t chunk)
    {
        StageScope timing(trace_, LocalReady);  // Perfetto 9 begin, 10 end (send_wait_local_ready)
        const int64_t epoch = int64_t(chunk) + 1;
        if (readyThrough_ >= epoch) {
            return Success;
        }
        auto values = ready_.Get<int64_t>();
        GlobalTensor<int64_t> flags;
        flags.SetGlobalBuffer(reinterpret_cast<__gm__ int64_t *>(workspace_ + c_.readyOffset));
        flags.SetL2CacheHint(CacheMode::CACHE_MODE_DISABLE);
        constexpr uint32_t readyBytes = 2 * sizeof(int64_t);
        constexpr uint32_t wordsPerSlot = kUbBlockBytes / sizeof(int64_t);
        const DataCopyExtParams copy(static_cast<uint16_t>(c_.reducerCount), readyBytes, kSlotBytes - readyBytes, 0, 0);
        const DataCopyPadExtParams<int64_t> pad {false, 0, 0, 0};
        for (uint64_t spin = 0; spin < c_.waitIterations; ++spin) {
            SetFlag<HardEvent::S_MTE2>(EVENT_ID0);
            WaitFlag<HardEvent::S_MTE2>(EVENT_ID0);
            DataCopyPad(values, flags, copy, pad);
            SetFlag<HardEvent::MTE2_S>(EVENT_ID0);
            WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);

            // Each two-word GM flag occupies one 32B block in UB.
            uint32_t reducer = 0;
            int64_t readyThrough = c_.chunks;
            for (; reducer < c_.reducerCount; ++reducer) {
                const uint32_t word = reducer * wordsPerSlot;
                const int64_t reducerEpoch = values.GetValue(word + 1);
                if (values.GetValue(word) != c_.magic || reducerEpoch < epoch) {
                    break;
                }
                if (reducerEpoch < readyThrough) {
                    readyThrough = reducerEpoch;
                }
            }
            if (reducer == c_.reducerCount) {
                // Partial rows already published for this call remain valid until it finishes.
                readyThrough_ = readyThrough;
                return Success;
            }
        }
        return ReadyTimeout;
    }
    __aicore__ uint32_t Add(uint32_t peer, uint32_t lane, const Record &record)
    {
        const uint64_t base = record.contributor < 0 ? c_.inputOffset : c_.partialOffset;
        batch_[lane][pending_[lane]++] = {
            base + uint64_t(record.primary) * RowBytes(), ReturnOffset(c_, record.token, c_.rank, sizeof(float)), peer};
        return pending_[lane] == kBatchRows ? FlushLane(lane) : uint32_t(Success);
    }
    __aicore__ uint32_t Flush()
    {
        for (uint32_t lane = 0; lane < kLanes; ++lane) {
            const uint32_t result = FlushLane(lane);
            if (result != Success) {
                return result;
            }
        }
        return Success;
    }
    __aicore__ uint32_t Notify(uint32_t peer, uint32_t lane, uint32_t chunk)
    {
        StageScope timing(trace_, SubmitSO);  // Perfetto 17 begin, 18 end (send_submit_so)
        const uint32_t qp = kLanes * owner_ + lane;
        auto source = reinterpret_cast<__gm__ uint64_t *>(workspace_ + c_.signalOffset + uint64_t(owner_) * kSlotBytes);
        const uint32_t ret = TileXR::UDMAPutNbiOnQp<uint64_t>(
            args_, wqe_.Get<uint8_t>(), peer, qp, source, CompletionOffset(c_, c_.rank, chunk, lane), 8);
        touched_[lane] |= PeerBit(peer);
        return ret == TileXR::TILEXR_UDMA_STATUS_SUCCESS ? uint32_t(Success) : uint32_t(TransportError);
    }
    __aicore__ uint32_t Drain()
    {
        uint32_t result = Success;
        for (uint32_t lane = 0; lane < kLanes; ++lane) {
            for (uint64_t peers = touched_[lane]; peers; peers &= peers - 1) {
                const uint32_t peer = __builtin_ctzll(peers);
                const uint32_t qp = kLanes * owner_ + lane;
                const uint32_t flush =
                    (unpublished_[lane] & PeerBit(peer)) ? TileXR::UDMAFlushQpDoorbell(args_, peer, qp) : 0;
                uint32_t quiet;
                {
                    StageScope timing(trace_, WaitCQ);  // Perfetto 19 begin, 20 end (send_wait_cq)
                    quiet = TileXR::UDMAQuietStatusOnQp(args_, peer, qp);
                }
                if (flush || quiet) {
                    result = TransportError;
                }
            }
        }
        return result;
    }

private:
    StageTrace &trace_;
    static constexpr uint32_t kBatchRows = 32;
    static constexpr uint32_t kForwardPageRows = 16;
    static_assert(sizeof(Record) == 24, "Packed forward record ABI");
    static constexpr uint32_t kUbBlockBytes = 32;
    static_assert(kWorld <= 64, "Each peer occupies one bit");

    struct Descriptor {
        uint64_t sourceOffset, targetOffset;
        uint32_t peer;
    };

    __aicore__ static uint64_t PeerBit(uint32_t peer)
    {
        return uint64_t(1) << peer;
    }
    __aicore__ uint64_t RowBytes() const
    {
        return uint64_t(c_.hidden) * sizeof(float);
    }

    __aicore__ uint32_t PostWrite(const Descriptor &d, uint32_t qp, uint64_t offset, uint64_t bytes)
    {
        auto source = reinterpret_cast<__gm__ uint8_t *>(workspace_ + d.sourceOffset + offset);
        auto scratch = wqe_.Get<uint8_t>();
        constexpr uint32_t completion = TileXR::TILEXR_UDMA_SQE_FLAG_COMPLETION;
        uint32_t ret = TileXR::UDMAPutNbiOnQpWithFlagDeferred<uint8_t>(
            args_, scratch, d.peer, qp, source, d.targetOffset + offset, bytes, completion);
        if (ret == TileXR::TILEXR_UDMA_STATUS_SQ_FULL) {
            if (TileXR::UDMAFlushQpDoorbell(args_, d.peer, qp) || TileXR::UDMAQuietStatusOnQp(args_, d.peer, qp)) {
                return TransportError;
            }
            ret = TileXR::UDMAPutNbiOnQpWithFlagDeferred<uint8_t>(
                args_, scratch, d.peer, qp, source, d.targetOffset + offset, bytes, completion);
        }
        return ret == TileXR::TILEXR_UDMA_STATUS_SUCCESS ? uint32_t(Success) : uint32_t(TransportError);
    }

    __aicore__ uint32_t PostRow(const Descriptor &d, uint32_t qp, uint64_t bytes)
    {
#ifdef TILEXR_EP_HT_SST_SPLIT_WRITES
        // SST striping requires each WRITE to stay within both 512B address stripes.
        for (uint64_t offset = 0; offset < bytes;) {
            uint64_t length = bytes - offset;
            const uint64_t sourceTail = kSlotBytes - (d.sourceOffset + offset) % kSlotBytes;
            const uint64_t targetTail = kSlotBytes - (d.targetOffset + offset) % kSlotBytes;
            length = length < sourceTail ? length : sourceTail;
            length = length < targetTail ? length : targetTail;
            const uint32_t result = PostWrite(d, qp, offset, length);
            if (result != Success) {
                return result;
            }
            offset += length;
        }
        return Success;
#else
        return PostWrite(d, qp, 0, bytes);
#endif
    }

    __aicore__ uint32_t PostPeerBatch(uint32_t peer, uint32_t lane, uint32_t rows)
    {
        StageScope timing(trace_, PostBatch);  // Perfetto 13 begin, 14 end (send_post_batch)
        const uint32_t qp = kLanes * owner_ + lane;
        if (!TileXR::UDMAQueueOperationValid(args_, peer, qp)) {
            return TransportError;
        }
        auto info = TileXR::GetUDMAInfo(args_);
        auto context = TileXR::UDMAGetWQCtx(info, peer, qp);
        auto memory = TileXR::UDMAGetRemoteMemInfo(info, peer, qp);
        if (!context || !memory || !context->headAddr || !context->tailAddr || !context->wqeCntAddr ||
            context->depth != TileXR::TILEXR_UDMA_SQ_BB_COUNT || !memory->eidAddr) {
            return TransportError;
        }
        uint32_t head = TileXR::UDMASstLoadState(reinterpret_cast<__gm__ uint32_t *>(context->headAddr));
        uint32_t tail = TileXR::UDMASstLoadState(reinterpret_cast<__gm__ uint32_t *>(context->tailAddr));
        const uint32_t depth = context->depth;
        if (head - tail > depth) {
            return TransportError;
        }
        // Reserve one BB for a following completion SO. Every payload has a
        // CQE, so the exact minimum tail watermark is always reclaimable.
        const uint32_t required = rows + 1;
        if (required > depth - (head - tail)) {
            TileXR::UDMARingDoorbell(head, context);
            const uint32_t target = head + required - depth;
            if (TileXR::UDMAPollCQUntilSqTail(info, peer, qp, target)) {
                return TransportError;
            }
        }
        auto scratch = wqe_.Get<uint8_t>();
        auto bytes = reinterpret_cast<__ubuf__ uint8_t *>(scratch.GetPhyAddr());
        uint32_t count = 0;
        for (uint32_t i = 0; i < pending_[lane]; ++i) {
            const Descriptor &d = batch_[lane][i];
            if (d.peer != peer) {
                continue;
            }
            auto source = reinterpret_cast<__gm__ uint8_t *>(workspace_ + d.sourceOffset);
            if (!TileXR::UDMARegisteredOperationValid(args_, peer, qp, d.targetOffset, RowBytes())) {
                return TransportError;
            }
            auto target = TileXR::UDMARegisteredRemoteAddr(TileXR::GetUDMARegistry(args_), peer, d.targetOffset);
            const uint32_t validated = TileXR::UDMAValidatePostSend<TileXR::UDMAOpcode::WRITE>(
                info, target, source, qp, RowBytes(), TileXR::TILEXR_UDMA_SQE_FLAG_COMPLETION, context);
            if (validated != TileXR::TILEXR_UDMA_STATUS_SUCCESS) {
                return TransportError;
            }
            auto wqe = bytes + count * TileXR::TILEXR_UDMA_WQE_BB_BYTES;
            TileXR::UDMAClearWqeScratch(wqe, TileXR::TILEXR_UDMA_WQE_BB_BYTES);
            TileXR::UDMAFillSqeCtx<TileXR::UDMAOpcode::WRITE>(reinterpret_cast<__ubuf__ TileXR::UDMASqeCtx *>(wqe),
                target, memory, head + count, TileXR::TILEXR_UDMA_SQE_FLAG_COMPLETION);
            TileXR::UDMAFillSgeCtx(reinterpret_cast<__ubuf__ TileXR::UDMASgeCtx *>(wqe + sizeof(TileXR::UDMASqeCtx)),
                RowBytes(), source, context->localTokenId);
            ++count;
        }
        // Publish the contiguous UB batch in one MTE3 copy, or two at wrap.
        // No SQ/head state changes until all descriptors have been validated.
        TileXR::UDMAPublishWqe(scratch, context, head, count);
        const uint32_t wqeCount = TileXR::UDMASstLoadState(reinterpret_cast<__gm__ uint32_t *>(context->wqeCntAddr));
        TileXR::UDMASstStoreState(reinterpret_cast<__gm__ uint32_t *>(context->headAddr), head + count);
        TileXR::UDMASstStoreState(reinterpret_cast<__gm__ uint32_t *>(context->wqeCntAddr), wqeCount + count);
        touched_[lane] |= PeerBit(peer);
        {
            StageScope timing(trace_, Doorbell);  // Perfetto 15 begin, 16 end (send_ring_doorbell)
            TileXR::UDMARingDoorbell(head + count, context);
        }
        return Success;
    }

#ifdef TILEXR_EP_HT_SST_SPLIT_WRITES
    __aicore__ uint32_t FlushSplitLane(uint32_t lane)
    {
        auto &dirtyPeers = unpublished_[lane];
        const uint32_t qp = kLanes * owner_ + lane;
        const uint64_t rowBytes = RowBytes();
        for (uint32_t i = 0; i < pending_[lane]; ++i) {
            const Descriptor &d = batch_[lane][i];
            const uint64_t peer = PeerBit(d.peer);
            touched_[lane] |= peer;
            dirtyPeers |= peer;
            const uint32_t result = PostRow(d, qp, rowBytes);
            if (result != Success) {
                return result;
            }
        }
        for (; dirtyPeers; dirtyPeers &= dirtyPeers - 1) {
            if (TileXR::UDMAFlushQpDoorbell(args_, __builtin_ctzll(dirtyPeers), qp)) {
                return TransportError;
            }
        }
        pending_[lane] = 0;
        return Success;
    }
#endif
    __aicore__ uint32_t FlushLane(uint32_t lane)
    {
#ifdef TILEXR_EP_HT_SST_SPLIT_WRITES
        // Retain native batching when each payload already fits both SST
        // stripes (including the T4/H256 fixture). Only split incompatible rows.
        for (uint32_t i = 0; i < pending_[lane]; ++i) {
            const Descriptor &d = batch_[lane][i];
            if (RowBytes() > kSlotBytes - d.sourceOffset % kSlotBytes ||
                RowBytes() > kSlotBytes - d.targetOffset % kSlotBytes) {
                return FlushSplitLane(lane);
            }
        }
#endif
        uint64_t peers = 0;
        for (uint32_t i = 0; i < pending_[lane]; ++i) {
            peers |= PeerBit(batch_[lane][i].peer);
        }
        for (; peers; peers &= peers - 1) {
            const uint32_t peer = __builtin_ctzll(peers);
            uint32_t rows = 0;
            for (uint32_t i = 0; i < pending_[lane]; ++i) {
                rows += batch_[lane][i].peer == peer;
            }
            const uint32_t result = PostPeerBatch(peer, lane, rows);
            if (result != Success) {
                return result;
            }
        }
        pending_[lane] = 0;
        return Success;
    }
    __gm__ TileXR::CommArgs *args_;
    GM_ADDR workspace_;
    GM_ADDR forward_;
    GM_ADDR counts_;
    GM_ADDR ranges_;
    const SendConfig &c_;
    uint32_t owner_;
    int64_t readyThrough_ = 0;
    uint32_t loadedRanges_ = 0;
    uint32_t cachePeer_[kMaxPeersPerOwner] = {kWorld, kWorld, kWorld, kWorld};
    int32_t validRows_[kMaxPeersPerOwner] = {};
    uint32_t pageBegin_[kMaxPeersPerOwner] = {};
    bool pageLoaded_[kMaxPeersPerOwner] = {};
    TBuf<QuePosition::VECCALC> forwardCache_;
    TBuf<QuePosition::VECCALC> rangesCache_;
    TBuf<QuePosition::VECCALC> metadata_;
    TBuf<QuePosition::VECCALC> ready_;
    TBuf<TPosition::VECOUT> wqe_;
    Descriptor batch_[kLanes][kBatchRows];
    uint32_t pending_[kLanes] = {};
    uint64_t touched_[kLanes] = {};
    uint64_t unpublished_[kLanes] = {};
};
} // namespace

/*
 * Copyright (c) 2026 TileXR Project.
 * Reduce the returned partials after all gateways publish their completion flags.
 */
#include "final_reduce_schedule.h"
#include "kernel_operator.h"

using namespace AscendC;
using namespace TileXREpHt;

namespace {
constexpr uint32_t kWordsPerBlock = 32 / sizeof(uint64_t);
constexpr uint32_t kMaskBatch = 32;
constexpr uint32_t kBankAlignment = 256;
constexpr uint32_t kBankMask = 1;

#if !defined(ASCENDC_CPU_DEBUG) && !defined(TILEXR_EP_HT_FINAL_REDUCE_SST_FP32)
namespace Reg = AscendC::MicroAPI;
constexpr Reg::CastTrait kToFloat = {
    Reg::RegLayout::ZERO, Reg::SatMode::NO_SAT, Reg::MaskMergeMode::ZEROING, RoundMode::UNKNOWN};
constexpr Reg::CastTrait kToBf16 = {
    Reg::RegLayout::ZERO, Reg::SatMode::NO_SAT, Reg::MaskMergeMode::ZEROING, RoundMode::CAST_RINT};

template <bool First, bool Last>
__simd_vf__ void AccumulateRow(
    __ubuf__ bfloat16_t *input, __ubuf__ float *sum, __ubuf__ bfloat16_t *output, uint32_t hidden)
{
    uint32_t remaining = hidden;
    for (uint32_t offset = 0; offset < hidden; offset += 64) {
        auto mask = Reg::UpdateMask<float>(remaining);
        Reg::RegTensor<bfloat16_t> payload;
        Reg::RegTensor<float> value;
        Reg::LoadAlign<bfloat16_t, Reg::LoadDist::DIST_UNPACK_B16>(payload, input + offset);
        Reg::Cast<float, bfloat16_t, kToFloat>(value, payload, mask);
        if constexpr (!First) {
            Reg::RegTensor<float> prior;
            Reg::LoadAlign(prior, sum + offset);
            Reg::Add(value, prior, value, mask);
        }
        if constexpr (Last) {
            Reg::Cast<bfloat16_t, float, kToBf16>(payload, value, mask);
            Reg::StoreAlign<bfloat16_t, Reg::StoreDist::DIST_PACK_B32>(output + offset, payload, mask);
        } else {
            Reg::StoreAlign(sum + offset, value, mask);
        }
    }
}
#endif

#ifdef TILEXR_EP_HT_FINAL_REDUCE_SST_FP32
using RowElement = float;
#else
using RowElement = bfloat16_t;
#endif

template <HardEvent event>
__aicore__ inline void Sync()
{
    SetFlag<event>(EVENT_ID0);
    WaitFlag<event>(EVENT_ID0);
}

class FinalReduceWorker {
public:
    __aicore__ FinalReduceWorker(GM_ADDR workspace, GM_ADDR serverMask, GM_ADDR localReady, GM_ADDR output,
        const SendConfig &config, TPipe &pipe, StageTrace &trace)
        : workspace_(workspace), serverMask_(serverMask), localReady_(localReady), output_(output), config_(config), trace_(trace)
    {
        const uint32_t rowBytes = config.hidden * sizeof(RowElement);
        bankElements_ = ((rowBytes + kBankAlignment - 1) & ~(kBankAlignment - 1)) / sizeof(RowElement);
        tokenStride_ = ReturnSlots(config.topk) * config.hidden;
        hidden_ = config.hidden;
        auto *returned = reinterpret_cast<__gm__ RowElement *>(workspace + config.returnOffset);
        returnedGm_.SetGlobalBuffer(returned);
        returnedGm_.SetL2CacheHint(CacheMode::CACHE_MODE_DISABLE);
        outputGm_.SetGlobalBuffer(reinterpret_cast<__gm__ RowElement *>(output_));
        pipe.InitBuffer(metadata_, kMaskBatch * sizeof(uint64_t));
        pipe.InitBuffer(notifications_, 5 * kWorld * 32);
        notificationGap_ = config_.chunks * kLanes * kSlotBytes - sizeof(uint64_t);
#ifdef TILEXR_EP_HT_FINAL_REDUCE_SST_FP32
        pipe.InitBuffer(row_, rowBytes);
        pipe.InitBuffer(outRow_, rowBytes);
#else
        pipe.InitBuffer(row_, 2 * bankElements_ * sizeof(RowElement));
        pipe.InitBuffer(outRow_, 2 * bankElements_ * sizeof(RowElement));
#endif
#if defined(ASCENDC_CPU_DEBUG) && !defined(TILEXR_EP_HT_FINAL_REDUCE_SST_FP32)
        pipe.InitBuffer(converted_, config.hidden * sizeof(float));
#endif
#ifndef TILEXR_EP_HT_FINAL_REDUCE_SST_FP32
        pipe.InitBuffer(sum_, config.hidden * sizeof(float));
#endif
    }

    __aicore__ bool WaitWindow(uint32_t window, uint32_t first, uint32_t last)
    {
        StageScope timing(trace_, WaitRemoteSO);  // Perfetto 25 begin, 26 end (reduce_wait_remote_so_and_local_ready)
        const uint32_t serverBegin = config_.rank / kRanksPerServer * kRanksPerServer;
        const uint64_t remote = ~(uint64_t {(1U << kRanksPerServer) - 1} << serverBegin);
        uint64_t previous = 0;
        if (first != last && LogicalChunk(config_, last - 1) != LogicalChunk(config_, last)) {
            for (uint64_t pending = remote; pending; pending &= pending - 1) {
                const uint32_t gateway = __builtin_ctzll(pending);
                if (NeedsPreviousNotify(config_, gateway, first, last)) {
                    previous |= uint64_t {1} << gateway;
                }
            }
        }

        // Each plane is a single strided DMA. Queue all required planes before
        // synchronizing Scalar once, and reread only planes with missing SOs.
        uint64_t pending[] = {(1U << kRanksPerServer) - 1, remote, remote, previous, previous};
        GM_ADDR const addresses[] = {localReady_ + LocalReturnReadyOffset(0, window),
            workspace_ + CompletionOffset(config_, 0, last, 0), workspace_ + CompletionOffset(config_, 0, last, 1),
            workspace_ + CompletionOffset(config_, 0, last - (last != 0), 0),
            workspace_ + CompletionOffset(config_, 0, last - (last != 0), 1)};
        auto flags = notifications_.Get<uint64_t>();
        const uint64_t magic = uint64_t(config_.magic);
        for (uint64_t attempt = 0; attempt < config_.waitIterations; ++attempt) {
            for (uint32_t group = 0; group < 5; ++group) {
                if (pending[group]) {
                    CopyMetadata(flags[group * kFlagPlaneWords], addresses[group],
                        group == 0 ? kRanksPerServer : kWorld, sizeof(uint64_t),
                        group == 0 ? kSlotBytes - sizeof(uint64_t) : notificationGap_);
                }
            }
            Sync<HardEvent::MTE2_S>();
            uint64_t missing = 0;
            for (uint32_t group = 0; group < 5; ++group) {
                auto plane = flags[group * kFlagPlaneWords];
                for (uint64_t remaining = pending[group]; remaining; remaining &= remaining - 1) {
                    const uint32_t slot = __builtin_ctzll(remaining);
                    if (plane.GetValue(slot * kWordsPerBlock) == magic) {
                        pending[group] &= ~(uint64_t {1} << slot);
                    }
                }
                missing |= pending[group];
            }
            if (missing == 0) {
                return true;
            }
        }
        return false;
    }

    __aicore__ uint32_t ReduceTokens(uint32_t begin, uint32_t end)
    {
        StageScope timing(trace_, ReduceRows);  // Perfetto 27 begin, 28 end (reduce_rows)
#ifndef TILEXR_EP_HT_FINAL_REDUCE_SST_FP32
        uint64_t rowOffset = uint64_t(begin) * tokenStride_;
#endif
        for (uint32_t token = begin; token < end;) {
            const uint32_t count = end - token < kMaskBatch ? end - token : kMaskBatch;
            auto masks = ReadMetadata(serverMask_ + uint64_t(token) * sizeof(uint64_t), count * sizeof(uint64_t));
#ifdef TILEXR_EP_HT_FINAL_REDUCE_SST_FP32
            for (uint32_t index = 0; index < count; ++index, ++token) {
                const uint64_t mask = masks.GetValue(index);
                if (mask >> kServerCount) {
                    return FinalReduceInvalidMask;
                }
                ReduceFp32Token(token, mask);
            }
#else
            uint64_t mask = masks.GetValue(0);
            if (mask >> kServerCount) {
                return FinalReduceInvalidMask;
            }
            if (mask) {
                Prefetch(rowOffset, mask, inputBank_);
            }
            for (uint32_t index = 0; index < count; ++index, ++token) {
                const uint64_t nextMask = index + 1 < count ? masks.GetValue(index + 1) : 0;
                ReduceToken(token, rowOffset, mask, nextMask >> kServerCount ? 0 : nextMask);
                if (nextMask >> kServerCount) {
                    return FinalReduceInvalidMask;
                }
                mask = nextMask;
                rowOffset += tokenStride_;
            }
#endif
        }
        return FinalReduceSuccess;
    }

    __aicore__ void Drain()
    {
#ifndef TILEXR_EP_HT_FINAL_REDUCE_SST_FP32
        for (uint32_t bank = 0; bank < 2; ++bank) {
            WaitOutput(bank);
            if (inputPending_[bank]) {
                WaitFlag<HardEvent::V_MTE2>(EVENT_ID0 + bank);
            }
        }
#endif
    }

private:
    static constexpr uint32_t kFlagPlaneWords = kWorld * kWordsPerBlock;

    __aicore__ void CopyMetadata(
        LocalTensor<uint64_t> local, GM_ADDR source, uint16_t blocks, uint32_t blockBytes, uint32_t gap)
    {
        GlobalTensor<uint64_t> gm;
        gm.SetGlobalBuffer(reinterpret_cast<__gm__ uint64_t *>(source));
        gm.SetL2CacheHint(CacheMode::CACHE_MODE_DISABLE);
        DataCopyPad(local, gm, DataCopyExtParams(blocks, blockBytes, gap, 0, 0),
            DataCopyPadExtParams<uint64_t> {false, 0, 0, 0});
    }

    __aicore__ LocalTensor<uint64_t> ReadMetadata(GM_ADDR source, uint32_t bytes)
    {
        auto local = metadata_.Get<uint64_t>();
        CopyMetadata(local, source, 1, bytes, 0);
        Sync<HardEvent::MTE2_S>();
        return local;
    }

    __aicore__ void WaitOutput(uint32_t bank)
    {
        if (outputPending_[bank]) {
            WaitFlag<HardEvent::MTE3_V>(EVENT_ID2 + bank);
            outputPending_[bank] = false;
        }
    }

    __aicore__ void WriteOutput(uint32_t token, uint32_t bank, LocalTensor<RowElement> output)
    {
        SetFlag<HardEvent::V_MTE3>(EVENT_ID2 + bank);
        WaitFlag<HardEvent::V_MTE3>(EVENT_ID2 + bank);
        DataCopy(outputGm_[uint64_t(token) * hidden_], output, hidden_);
        SetFlag<HardEvent::MTE3_V>(EVENT_ID2 + bank);
        outputPending_[bank] = true;
    }

#ifdef TILEXR_EP_HT_FINAL_REDUCE_SST_FP32
    // The standalone model uses the baseline FP32 compute and copy schedule.
    __aicore__ void ReduceFp32Token(uint32_t token, uint64_t mask)
    {
        auto row = row_.Get<float>();
        auto output = outRow_.Get<float>();
        const uint32_t hidden = config_.hidden;
        Duplicate(output, 0.0F, hidden);
        PipeBarrier<PIPE_V>();
        for (uint32_t server = 0; server < kServerCount; ++server) {
            if ((mask & (uint64_t {1} << server)) == 0) {
                continue;
            }
            GlobalTensor<float> returned;
            returned.SetGlobalBuffer(reinterpret_cast<__gm__ float *>(
                workspace_ + ReturnOffset(config_, token, server * kRanksPerServer, sizeof(float))));
            returned.SetL2CacheHint(CacheMode::CACHE_MODE_DISABLE);
            {
                StageScope timing(trace_, LoadPartial);  // Perfetto 29 begin, 30 end (reduce_load_partial)
                DataCopy(row, returned, hidden);
                Sync<HardEvent::MTE2_V>();
            }
            {
                StageScope timing(trace_, AddPartial);  // Perfetto 31 begin, 32 end (reduce_add_partial)
                Add(output, output, row, hidden);
                Sync<HardEvent::V_MTE2>();
            }
        }
        StageScope timing(trace_, StoreOutput);  // Perfetto 33 begin, 34 end (reduce_write_output)
        Sync<HardEvent::V_MTE3>();
        GlobalTensor<float> result;
        result.SetGlobalBuffer(reinterpret_cast<__gm__ float *>(output_) + uint64_t(token) * hidden);
        DataCopy(result, output, hidden);
        Sync<HardEvent::MTE3_V>();
    }
#else
    __aicore__ void Prefetch(uint64_t rowOffset, uint64_t mask, uint32_t bank)
    {
        if (inputPending_[bank]) {
            WaitFlag<HardEvent::V_MTE2>(EVENT_ID0 + bank);
            inputPending_[bank] = false;
        }
        const uint32_t server = __builtin_ctzll(mask);
        DataCopy(row_.Get<RowElement>()[bank * bankElements_],
            returnedGm_[rowOffset + uint64_t(server) * config_.hidden], config_.hidden);
        SetFlag<HardEvent::MTE2_V>(EVENT_ID0 + bank);
    }

    template <bool First, bool Last>
    __aicore__ void Compute(LocalTensor<RowElement> input, LocalTensor<RowElement> output)
    {
        auto sum = sum_.Get<float>();
#if defined(ASCENDC_CPU_DEBUG)
        auto converted = converted_.Get<float>();
        if constexpr (First) {
            Cast(sum, input, RoundMode::CAST_NONE, config_.hidden);
        } else {
            Cast(converted, input, RoundMode::CAST_NONE, config_.hidden);
            PipeBarrier<PIPE_V>();
            Add(sum, sum, converted, config_.hidden);
        }
        if constexpr (Last) {
            PipeBarrier<PIPE_V>();
            Cast(output, sum, RoundMode::CAST_RINT, config_.hidden);
        }
#else
        asc_vf_call<AccumulateRow<First, Last>>(reinterpret_cast<__ubuf__ bfloat16_t *>(input.GetPhyAddr()),
            reinterpret_cast<__ubuf__ float *>(sum.GetPhyAddr()),
            reinterpret_cast<__ubuf__ bfloat16_t *>(output.GetPhyAddr()), config_.hidden);
#endif
    }

    __aicore__ void ReduceToken(uint32_t token, uint64_t rowOffset, uint64_t mask, uint64_t nextMask)
    {
        const uint32_t outBank = token & kBankMask;
        auto output = outRow_.Get<RowElement>()[outBank * bankElements_];
        if (mask == 0) {
            if (nextMask) {
                Prefetch(rowOffset + tokenStride_, nextMask, inputBank_);
            }
            WaitOutput(outBank);
            Duplicate(output, static_cast<RowElement>(0.0F), config_.hidden);
        }
        bool first = true;
        while (mask) {
            const uint32_t bank = inputBank_;
            inputBank_ ^= kBankMask;
            mask &= mask - 1;
            if (mask) {
                Prefetch(rowOffset, mask, inputBank_);
            } else if (nextMask) {
                Prefetch(rowOffset + tokenStride_, nextMask, inputBank_);
            }
            WaitFlag<HardEvent::MTE2_V>(EVENT_ID0 + bank);
            if (mask == 0) {
                WaitOutput(outBank);
            }
            auto input = row_.Get<RowElement>()[bank * bankElements_];
            if (first) {
                if (mask) {
                    Compute<true, false>(input, output);
                } else {
                    Compute<true, true>(input, output);
                }
                first = false;
            } else if (mask) {
                Compute<false, false>(input, output);
            } else {
                Compute<false, true>(input, output);
            }
            if (mask) {
                PipeBarrier<PIPE_V>();
            }
            SetFlag<HardEvent::V_MTE2>(EVENT_ID0 + bank);
            inputPending_[bank] = true;
        }
        WriteOutput(token, outBank, output);
    }
#endif

    GM_ADDR workspace_;
    GM_ADDR serverMask_;
    StageTrace &trace_;
    GM_ADDR localReady_;
    GM_ADDR output_;
    const SendConfig &config_;
    GlobalTensor<RowElement> returnedGm_, outputGm_;
    uint32_t bankElements_, tokenStride_, hidden_, notificationGap_;
    bool outputPending_[2] = {};
    TBuf<TPosition::VECCALC> metadata_, notifications_, row_, outRow_;
#ifndef TILEXR_EP_HT_FINAL_REDUCE_SST_FP32
    uint32_t inputBank_ = 0;
    bool inputPending_[2] = {};
    TBuf<TPosition::VECCALC> sum_;
#endif
#if defined(ASCENDC_CPU_DEBUG) && !defined(TILEXR_EP_HT_FINAL_REDUCE_SST_FP32)
    TBuf<TPosition::VECCALC> converted_;
#endif
};
} // namespace


namespace {
__aicore__ inline void ExportStatus(LocalTensor<uint32_t> local, GM_ADDR address, uint32_t result)
{
    local.SetValue(0, result);
    GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(address));
    Sync<HardEvent::S_MTE3>();
    DataCopyPad(gm, local, DataCopyExtParams(1, sizeof(uint32_t), 0, 0, 0));
    Sync<HardEvent::MTE3_S>();
}
}

extern "C" __global__ __aicore__ void tilexr_ep_ht_combine_sst_fp32(
    GM_ADDR commArgs, GM_ADDR workspace, GM_ADDR forward, GM_ADDR counts, GM_ADDR ranges, GM_ADDR status,
    GM_ADDR serverMask, GM_ADDR localReady, GM_ADDR output, SendConfig config)
{
    TPipe pipe;
    StageTrace trace;
    trace.Init(pipe);
    trace.Mark(2 * Kernel + 1);  // Perfetto 1 begin (kernel)
    TBuf<QuePosition::VECCALC> queueStateScratch;
    trace.Mark(2 * SendSetup + 1);  // Perfetto 3 begin (send_setup)
    pipe.InitBuffer(queueStateScratch, 32);
    const uint32_t block = GetBlockIdx();
    DeviceTransport transport(commArgs, workspace, forward, counts, ranges, config, block, pipe, trace);
    trace.Mark(2 * SendSetup + 2);  // Perfetto 4 end (send_setup)

    uint32_t sendResult;
    {
        StageScope timing(trace, SendSubmit);  // Perfetto 5 begin, 6 end (send_submit)
        sendResult = Submit(config, block, transport);
    }
    {
        StageScope timing(trace, SendDrain);  // Perfetto 7 begin, 8 end (send_drain)
        const uint32_t drained = Drain(transport);
        if (sendResult == Success) {
            sendResult = drained;
        }
    }
    TBuf<TPosition::VECCALC> statusBuffer;
    pipe.InitBuffer(statusBuffer, 32);
    auto local = statusBuffer.Get<uint32_t>();
    {
        StageScope timing(trace, WriteStatus);  // Perfetto 35 begin, 36 end (write_status)
        ExportStatus(local, status + block * 32, sendResult);
    }
    trace.Mark(2 * ReduceSetup + 1);  // Perfetto 21 begin (reduce_setup)
    FinalReduceWorker worker(workspace, serverMask, localReady, output, config, pipe, trace);
    trace.Mark(2 * ReduceSetup + 2);  // Perfetto 22 end (reduce_setup)
    uint32_t reduceResult;
    {
        StageScope timing(trace, ReduceTotal);  // Perfetto 23 begin, 24 end (reduce_total)
        reduceResult = ReduceWindows(config, block, GetBlockNum(), worker);
        worker.Drain();
    }
    {
        StageScope timing(trace, WriteStatus);  // Perfetto 35 begin, 36 end (write_status)
        ExportStatus(local, status + GetBlockNum() * 32 + block * 32, reduceResult);
    }
    trace.Mark(2 * Kernel + 2);  // Perfetto 2 end (kernel)
    trace.Export(status + 4096 + uint64_t(block) * StageTrace::Bytes);
}
