// Diagnostic: publish one real WQE through UB/MTE3 and qualify scalar doorbell width.
#include "kernel_operator.h"
#include "combine_ht_prefill.h"
#include "/work/candidate/src/moonep/combine_ht/kernels/combine_ht_queue_state.h"
using namespace AscendC;
struct ProbeArgs { GM_ADDR image; GM_ADDR info; GM_ADDR marker; GM_ADDR gm; uint32_t rank; };
struct Observer {
    uint32_t count = 0, tail = 0;
    __aicore__ inline void Completed(uint32_t value) { ++count; tail = value; }
};
extern "C" __global__ __aicore__ void tilexr_moonep_combine_ht_kernel(ProbeArgs args)
{
    if (GetBlockIdx() || args.rank != (PROBE_GM64 ? 0U : 3U)) return;
    TPipe pipe;
    TBuf<QuePosition::VECCALC> read, write, wqes;
    pipe.InitBuffer(read, 64); pipe.InitBuffer(write, 64); pipe.InitBuffer(wqes, 64);
    TileXRCombineHT::MteQueueState state(read.Get<uint32_t>(), write.Get<uint32_t>());
    uint32_t status = 0;
    Observer observer;
#if PROBE_GM64
    st_dev(uint64_t(0x2468ace013579bdfULL),
        reinterpret_cast<__gm__ uint64_t *>(reinterpret_cast<uint64_t>(args.gm) >> 7), 0);
#else
    auto *info = reinterpret_cast<__gm__ TileXR::UDMAInfo *>(args.info);
    auto *sq = TileXR::UDMAGetWQCtx(info, 2, 2);
    GlobalTensor<uint8_t> image;
    image.SetGlobalBuffer(args.image, 64);
    DataCopy(wqes.Get<uint8_t>(), image, 64);
    SetFlag<HardEvent::MTE2_S>(EVENT_ID0); WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);
    auto words = wqes.Get<uint32_t>();
    words.SetValue(0, TileXRCombineHT::PrefillQueueWord(words.GetValue(0), 0,
        TileXR::TILEXR_UDMA_SQE_FLAG_ORDERED_COMPLETION));
    TileXR::UDMAPublishWqe(wqes.Get<uint8_t>(), sq, 0, 1);
    state.Store(sq->headAddr, 1); state.Store(sq->wqeCntAddr, 1);
#if PROBE_STORE_BITS == 64
    st_dev(uint64_t(1), reinterpret_cast<__gm__ uint64_t *>(sq->dbAddr >> 7), 0);
#else
    st_dev(uint32_t(1), reinterpret_cast<__gm__ uint32_t *>(sq->dbAddr >> 3), 0);
#endif
    status = TileXR::UDMAPollCQUntilSqTail(info, 2, 2, 1, observer, state);
#endif
    const uint64_t marker = reinterpret_cast<uint64_t>(args.marker);
    state.Store(marker, status); state.Store(marker + 4, observer.count); state.Store(marker + 8, observer.tail);
}
