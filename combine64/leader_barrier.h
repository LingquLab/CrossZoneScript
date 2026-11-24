/* Copyright (c) 2026 TileXR Project.
 * Keep full per-rank rendezvous while reducing follower polling to one word.
 * The release word occupies unused padding in the existing 8192-byte image. */
__aicore__ bool JointBarrier(const PipelineArgs &a,uint32_t epoch) {
    using namespace AscendC;
    const uint32_t block=GetBlockIdx();
    constexpr uint32_t releaseWord=2040; // 8160 bytes: last slot's unused final 32B.
    TPipe pipe;
    TBuf<QuePosition::VECCALC> read,write;
    pipe.InitBuffer(read,block==0?8192:32);
    pipe.InitBuffer(write,32);
    auto source=write.Get<uint32_t>();
    source.SetValue(0,epoch);
    GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(a.barrier));
    gm.SetL2CacheHint(CacheMode::CACHE_MODE_DISABLE);
    JointSync<HardEvent::S_MTE3>();
    DataCopyPad(gm[uint64_t(block)*128],source,DataCopyExtParams(1,4,0,0,0));
    JointSync<HardEvent::MTE3_S>();
    auto snapshot=read.Get<uint32_t>();
    for(uint64_t attempt=0;attempt<a.returned.config.waitIterations;++attempt) {
        JointSync<HardEvent::S_MTE2>();
        if(block==0) DataCopy(snapshot,gm,16*128);
        else DataCopyPad(snapshot,gm[releaseWord],DataCopyExtParams(1,4,0,0,0),
                         DataCopyPadExtParams<uint32_t>{false,0,0,0});
        JointSync<HardEvent::MTE2_S>();
        if(block!=0) {
            if(snapshot.GetValue(0)>=epoch) return true;
            continue;
        }
        uint32_t pending=0;
        for(uint32_t lane=0;lane<16;++lane) if(snapshot.GetValue(lane*128)<epoch) ++pending;
        if(!pending) {
            JointSync<HardEvent::S_MTE3>();
            DataCopyPad(gm[releaseWord],source,DataCopyExtParams(1,4,0,0,0));
            JointSync<HardEvent::MTE3_S>();
            return true;
        }
    }
    JointStatus(pipe,a.barrierStatus+uint64_t(block)*32,100+epoch);
    return false;
}
