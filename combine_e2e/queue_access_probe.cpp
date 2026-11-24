/* Copyright (c) 2026 TileXR Project.
 * Compare native device accesses and MTE accesses to initialized GM counters.
 * Each rank uses one AIV and private UB; no communication queue is submitted.
 */
#include "kernel_operator.h"
using namespace AscendC;
namespace {
template <HardEvent E> __aicore__ inline void Sync()
{
    SetFlag<E>(EVENT_ID0);
    WaitFlag<E>(EVENT_ID0);
}
__aicore__ uint32_t LoadMte(uint64_t address, LocalTensor<uint32_t> ub)
{
    GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(address));
    gm.SetL2CacheHint(CacheMode::CACHE_MODE_DISABLE);
    Sync<HardEvent::S_MTE2>();
    DataCopyPad(ub, gm, DataCopyExtParams(1, 4, 0, 0, 0), DataCopyPadExtParams<uint32_t>{false, 0, 0, 0});
    Sync<HardEvent::MTE2_S>();
    return ub.GetValue(0);
}
__aicore__ void StoreMte(uint64_t address, uint32_t value, LocalTensor<uint32_t> ub)
{
    GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(address));
    gm.SetL2CacheHint(CacheMode::CACHE_MODE_DISABLE);
    ub.SetValue(0, value);
    Sync<HardEvent::S_MTE3>();
    DataCopyPad(gm, ub, DataCopyExtParams(1, 4, 0, 0, 0));
    Sync<HardEvent::MTE3_S>();
    Sync<HardEvent::MTE3_MTE2>();
}
}
extern "C" __global__ __aicore__ void tilexr_queue_access_probe(GM_ADDR counters, GM_ADDR output)
{
    TPipe pipe;
    TBuf<QuePosition::VECCALC> read, write, report;
    pipe.InitBuffer(read, 64);
    pipe.InitBuffer(write, 64);
    pipe.InitBuffer(report, 512);
    auto values = report.Get<uint32_t>();
    for (uint32_t word = 0; word < 128; ++word) {
        values.SetValue(word, 0xDEADBEEFU);
    }
    for (uint32_t cell = 0; cell < 8; ++cell) {
        const uint64_t address = reinterpret_cast<uint64_t>(counters) + cell * 512;
        auto pointer = reinterpret_cast<__gm__ uint32_t *>(address);
        const uint32_t initial = LoadMte(address, read.Get<uint32_t>());
        values.SetValue(cell * 8, ld_dev(pointer, 0));
        values.SetValue(cell * 8 + 1, initial);
        st_dev(initial + 17, pointer, 0);
        values.SetValue(cell * 8 + 2, ld_dev(pointer, 0));
        values.SetValue(cell * 8 + 3, LoadMte(address, read.Get<uint32_t>()));
        StoreMte(address, initial + 33, write.Get<uint32_t>());
        values.SetValue(cell * 8 + 4, ld_dev(pointer, 0));
        values.SetValue(cell * 8 + 5, LoadMte(address, read.Get<uint32_t>()));
        for (uint32_t step = 0; step < 8; ++step) {
            const uint32_t value = ld_dev(pointer, 0);
            st_dev(value + 1, pointer, 0);
        }
        values.SetValue(cell * 8 + 6, ld_dev(pointer, 0));
        values.SetValue(cell * 8 + 7, LoadMte(address, read.Get<uint32_t>()));
    }
    GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t *>(output));
    Sync<HardEvent::S_MTE3>();
    DataCopy(gm, values, 128);
    Sync<HardEvent::MTE3_S>();
}
