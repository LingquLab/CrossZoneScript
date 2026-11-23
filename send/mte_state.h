/* Copyright (c) 2026 TileXR Project.
 * Read and write ordinary queue-state GM through synchronous MTE transfers.
 * The caller owns separate 64B UB read/write scratch for the entire Send call.
 * SQ/CQ doorbells retain their original st_dev publication path.
 */
namespace TileXR {
template <AscendC::HardEvent event>
__aicore__ inline void SimulatorStateEvent()
{
    const auto id = GetTPipePtr()->FetchEventID(event);
    AscendC::SetFlag<event>(id);
    AscendC::WaitFlag<event>(id);
}
__aicore__ inline uint32_t SimulatorLoad(__gm__ uint32_t *address,
    AscendC::LocalTensor<uint32_t> scratch)
{
    AscendC::GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(address, 16U);
    SimulatorStateEvent<AscendC::HardEvent::S_MTE2>();
    AscendC::DataCopyPad(scratch, gm, AscendC::DataCopyExtParams{1U, 64U, 0U, 0U, 0U},
        AscendC::DataCopyPadExtParams<uint32_t>{false, 0U, 0U, 0U});
    SimulatorStateEvent<AscendC::HardEvent::MTE2_S>();
    return scratch.GetValue(0U);
}
__aicore__ inline void SimulatorStore(uint32_t value, __gm__ uint32_t *address,
    AscendC::LocalTensor<uint32_t> scratch)
{
    scratch.SetValue(0U, value);
    AscendC::GlobalTensor<uint32_t> gm;
    gm.SetGlobalBuffer(address, 1U);
    SimulatorStateEvent<AscendC::HardEvent::S_MTE3>();
    AscendC::DataCopyPad(gm, scratch, AscendC::DataCopyExtParams{1U, 4U, 0U, 0U, 0U});
    SimulatorStateEvent<AscendC::HardEvent::MTE3_S>();
    SimulatorStateEvent<AscendC::HardEvent::MTE3_MTE2>();
}
// The pinned runner appends a copy of its baseline CQ poll function here,
// replacing only queue-state loads/stores and passing the caller's scratch.
}
