/* Copyright (c) 2026 TileXR Project.
 * Execute the pinned HT stage implementations in one SST kernel invocation.
 * The fixture supplies distinct local/return communicators and private barriers. */
#include "combine_ht_prefill.h"
#include "combine_ht_queue_state.h"
#include "kernel_operator.h"
#include "simt_api/device_functions.h"
#include <tilexr/comm/udma/device/api.h>
#include <tilexr/comm/device/sync.h>
#include <tilexr/profiling/device/direct_writer.h>
#include "queue_state.h"
#include "profile_flush.h"
#include "local_return_args.h"
#include "local_return_schedule.h"
#include "final_reduce_schedule.h"
#include <tilexr/ep/final_reduce_types.h>

namespace LocalStage {
#include "combine_ht_kernel.h"
}
namespace ReturnStage {
#include "/work/source/src/ep/combine_ht/local_return_kernel.cpp"
}
namespace InterStage {
#include "/work/source/src/ep/combine_ht/inter_server_send_kernel.cpp"
}
namespace FinalStage {
#include "/work/source/src/ep/combine_ht/final_reduce_kernel.cpp"
}

struct PipelineArgs {
    TileXRCombineHT::KernelArgs local;
    TileXREpHt::LocalReturnArgs returned;
    uint64_t serverMask, output, sendStatus, finalStatus, fenceStatus, barrier, barrierStatus, profiling;
};
static_assert(sizeof(PipelineArgs)==512,"joint SST argument layout");

namespace {
template<AscendC::HardEvent E> __aicore__ inline void JointSync() {
    AscendC::SetFlag<E>(EVENT_ID2);
    AscendC::WaitFlag<E>(EVENT_ID2);
}
__aicore__ void JointStatus(AscendC::TPipe &pipe,uint64_t address,uint32_t value) {
    AscendC::TBuf<AscendC::QuePosition::VECCALC> buffer;
    pipe.InitBuffer(buffer,32);
    auto words=buffer.Get<uint32_t>();
    words.SetValue(0,value);
    AscendC::GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(address));
    JointSync<AscendC::HardEvent::S_MTE3>();
    AscendC::DataCopyPad(gm,words,AscendC::DataCopyExtParams(1,4,0,0,0));
    JointSync<AscendC::HardEvent::MTE3_S>();
}
__aicore__ bool JointBarrier(const PipelineArgs &a,uint32_t epoch) {
    using namespace AscendC;
    TPipe pipe;
    TBuf<QuePosition::VECCALC> read,write;
    pipe.InitBuffer(read,16*512);
    pipe.InitBuffer(write,32);
    auto source=write.Get<uint32_t>();
    source.SetValue(0,epoch);
    GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(a.barrier));
    gm.SetL2CacheHint(CacheMode::CACHE_MODE_DISABLE);
    JointSync<HardEvent::S_MTE3>();
    DataCopyPad(gm[uint64_t(GetBlockIdx())*128],source,DataCopyExtParams(1,4,0,0,0));
    JointSync<HardEvent::MTE3_S>();
    auto snapshot=read.Get<uint32_t>();
    for(uint64_t attempt=0;attempt<a.returned.config.waitIterations;++attempt) {
        JointSync<HardEvent::S_MTE2>();
        DataCopy(snapshot,gm,16*128);
        JointSync<HardEvent::MTE2_S>();
        uint32_t pending=0;
        for(uint32_t block=0;block<16;++block) if(snapshot.GetValue(block*128)<epoch) ++pending;
        if(!pending) return true;
    }
    JointStatus(pipe,a.barrierStatus+uint64_t(GetBlockIdx())*32,100+epoch);
    return false;
}
__aicore__ void JointCopyPartial(const PipelineArgs &a) {
    using namespace AscendC;
    const uint64_t bytes=uint64_t(a.local.config.input_rows)*a.local.config.hidden*4;
    const uint64_t begin=bytes*GetBlockIdx()/16,end=bytes*(GetBlockIdx()+1)/16;
    TPipe pipe;
    TBuf<QuePosition::VECCALC> buffer;
    pipe.InitBuffer(buffer,8192);
    auto data=buffer.Get<uint8_t>();
    GlobalTensor<uint8_t> from,to;
    from.SetGlobalBuffer(a.local.workspace+a.local.layout.partial_offset);
    to.SetGlobalBuffer(reinterpret_cast<__gm__ uint8_t *>(a.returned.workspace+a.returned.config.partialOffset));
    for(uint64_t at=begin;at<end;) {
        const uint32_t count=end-at<8192?uint32_t(end-at):8192;
        DataCopyPad(data,from[at],DataCopyExtParams(1,count,0,0,0),DataCopyPadExtParams<uint8_t>{false,0,0,0});
        JointSync<HardEvent::MTE2_MTE3>();
        DataCopyPad(to[at],data,DataCopyExtParams(1,count,0,0,0));
        JointSync<HardEvent::MTE3_MTE2>();
        at+=count;
    }
    JointSync<HardEvent::MTE3_S>();
}
}

extern "C" __global__ __aicore__ void tilexr_ht_pipeline_sst(PipelineArgs args) {
    using namespace AscendC;
    using namespace TileXREpHt;
    const uint32_t block=GetBlockIdx();
    TileXR::Profiling::DirectWriter capture;
    capture.Initialize(reinterpret_cast<GM_ADDR>(args.profiling), block);
    capture.RecordAt(1, GetSystemCycle());
    capture.RecordAt(10, GetSystemCycle());
    LocalStage::RunCombineHT(args.local);
    capture.RecordAt(11, GetSystemCycle());
    FlushProfile(args.local.profiling);
    if(!JointBarrier(args,1)) return;
    capture.RecordAt(20, GetSystemCycle());
    JointCopyPartial(args);
    capture.RecordAt(21, GetSystemCycle());
    if(!JointBarrier(args,2)) return;
    capture.RecordAt(30, GetSystemCycle());
    if(block<8) {
        TPipe pipe;
        ReturnStage::LocalTransport transport(args.returned,pipe);
        uint32_t result=ReadyTimeout;
        if(transport.UpstreamComplete()) {
            if(block==0) for(uint32_t r=0;r<args.returned.config.reducerCount;++r)
                transport.Store<int64_t>(args.returned.workspace+args.returned.config.readyOffset+uint64_t(r)*512,
                    args.returned.config.magic,args.returned.config.chunks,2);
            result=ReturnLocal(args.returned.config,block,transport);
            const uint32_t drained=transport.Drain();
            if(result==Success) result=drained;
        }
        transport.Store<uint32_t>(args.returned.status+uint64_t(block)*32,result);
    }
    capture.RecordAt(31, GetSystemCycle());
    if(!JointBarrier(args,3)) return;
    capture.RecordAt(40, GetSystemCycle());
    {
        TPipe pipe;
        InterStage::DeviceTransport transport(reinterpret_cast<GM_ADDR>(args.returned.comm),
            reinterpret_cast<GM_ADDR>(args.returned.workspace),reinterpret_cast<GM_ADDR>(args.returned.forward),
            reinterpret_cast<GM_ADDR>(args.returned.counts),reinterpret_cast<GM_ADDR>(args.returned.ranges),
            args.returned.config,block,pipe);
        uint32_t result=Submit(args.returned.config,block,transport);
        const uint32_t drained=Drain(transport);
        if(result==Success) result=drained;
        JointStatus(pipe,args.sendStatus+uint64_t(block)*32,result);
    }
    capture.RecordAt(41, GetSystemCycle());
    if(!JointBarrier(args,4)) return;
    capture.RecordAt(50, GetSystemCycle());
    {
        TPipe pipe;
        FinalStage::FinalReduceWorker worker(reinterpret_cast<GM_ADDR>(args.returned.workspace),
            reinterpret_cast<GM_ADDR>(args.serverMask),reinterpret_cast<GM_ADDR>(args.returned.workspace+args.returned.localReadyOffset),
            reinterpret_cast<GM_ADDR>(args.output),args.returned.config,pipe);
        const uint32_t result=ReduceWindows(args.returned.config,block,16,worker);
        worker.Drain();
        JointStatus(pipe,args.finalStatus+uint64_t(block)*32,result);
    }
    capture.RecordAt(51, GetSystemCycle());
    if(!JointBarrier(args,5)) return;
    capture.RecordAt(60, GetSystemCycle());
    if(block==0) {
        TPipe pipe;
        ReturnStage::LocalTransport transport(args.returned,pipe);
        transport.Store<uint32_t>(args.fenceStatus,transport.Fence());
    }
    capture.RecordAt(61, GetSystemCycle());
    if(!JointBarrier(args,6)) return;
    TPipe pipe;
    JointStatus(pipe,args.barrierStatus+uint64_t(block)*32,0);
    capture.RecordAt(2, GetSystemCycle());
    capture.Publish();
    FlushProfile(reinterpret_cast<GM_ADDR>(args.profiling));
}
