"""Add diagnostic checkpoints to temporary copies of the two BF16 workers."""
from pathlib import Path

SNAPSHOT = """
namespace TileXRProbe {
constexpr uint64_t kTraceBase = 0x420000000ULL;
constexpr uint32_t kTraceStride = 1024;

template <typename T>
__aicore__ inline void Snapshot(AscendC::LocalTensor<T> data, uint32_t count, uint32_t slot)
{
    using namespace AscendC;
    GlobalTensor<T> target;
    target.SetGlobalBuffer(reinterpret_cast<__gm__ T *>(kTraceBase + uint64_t(slot) * kTraceStride));
    SetFlag<HardEvent::V_MTE3>(EVENT_ID7);
    WaitFlag<HardEvent::V_MTE3>(EVENT_ID7);
    DataCopy(target, data, count);
    SetFlag<HardEvent::MTE3_V>(EVENT_ID7);
    WaitFlag<HardEvent::MTE3_V>(EVENT_ID7);
}
}
"""


def replace(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new, 1)


def gateway(source):
    source = replace(source, "LocalTensor<float> fp32_accumulator_ub)\n{\n    GlobalTensor<bfloat16_t> input_gm;",
                     "LocalTensor<float> fp32_accumulator_ub, int traceSlot)\n{\n    GlobalTensor<bfloat16_t> input_gm;")
    source = replace(source, "    Cast(fp32_input_ub, bf16_input_ub, RoundMode::CAST_NONE, element_num);",
                     """    if (traceSlot >= 0) TileXRProbe::Snapshot(bf16_input_ub, element_num, traceSlot);
    Cast(fp32_input_ub, bf16_input_ub, RoundMode::CAST_NONE, element_num);""")
    source = replace(source, "    Add(fp32_accumulator_ub, fp32_accumulator_ub, fp32_input_ub, element_num);",
                     """    if (traceSlot >= 0) TileXRProbe::Snapshot(fp32_input_ub, element_num, traceSlot + 1);
    Add(fp32_accumulator_ub, fp32_accumulator_ub, fp32_input_ub, element_num);""")
    source = replace(source, """    Add(fp32_accumulator_ub, fp32_accumulator_ub, fp32_input_ub, element_num);
    PipeBarrier<PIPE_V>();""",
                     """    Add(fp32_accumulator_ub, fp32_accumulator_ub, fp32_input_ub, element_num);
    PipeBarrier<PIPE_V>();
    if (traceSlot >= 0) TileXRProbe::Snapshot(fp32_accumulator_ub, element_num, traceSlot + 2);""")
    source = replace(source, "                Cast(fp32_accumulator_ub, row_input_ub, RoundMode::CAST_NONE, element_num);",
                     """                const bool trace = primary_row == 0;
                if (trace) TileXRProbe::Snapshot(row_input_ub, element_num, 0);
                Cast(fp32_accumulator_ub, row_input_ub, RoundMode::CAST_NONE, element_num);
                if (trace) {
                    TileXRProbe::Snapshot(fp32_accumulator_ub, element_num, 1);
                    TileXRProbe::Snapshot(fp32_accumulator_ub, element_num, 2);
                }""")
    source = replace(source, "element_num, row_input_ub, fp32_input_ub, fp32_accumulator_ub);",
                     """element_num, row_input_ub, fp32_input_ub, fp32_accumulator_ub,
                            trace ? int(3 * (1 + contributor_task_index - task_index)) : -1);""")
    source = replace(source, "                Cast(bf16_output_slot_ub, fp32_accumulator_ub, RoundMode::CAST_RINT, element_num);",
                     """                Cast(bf16_output_slot_ub, fp32_accumulator_ub, RoundMode::CAST_RINT, element_num);
                if (trace) TileXRProbe::Snapshot(bf16_output_slot_ub, element_num, 24);""")
    return source


def final(source, mode):
    source = '#include "probe_snapshot.h"\n' + source
    source = replace(source, "__ubuf__ bfloat16_t *output, uint32_t hidden)",
                     "__ubuf__ bfloat16_t *output, uint32_t hidden, __ubuf__ float *trace, bool record)")
    source = replace(source, "        Reg::Cast<float, bfloat16_t, kToFloat>(value, payload, mask);",
                     """        Reg::Cast<float, bfloat16_t, kToFloat>(value, payload, mask);
        if (record) Reg::StoreAlign(trace + offset, value, mask);""")
    source = replace(source, "            Reg::LoadAlign(prior, sum + offset);",
                     """            Reg::LoadAlign(prior, sum + offset);
            if (record) Reg::StoreAlign(trace + hidden + offset, prior, mask);""")
    source = replace(source, "            Reg::Add(value, prior, value, mask);",
                     """            Reg::Add(value, prior, value, mask);
            if (record) Reg::StoreAlign(trace + 2 * hidden + offset, value, mask);""")
    source = replace(source, "        pipe.InitBuffer(sum_, config.hidden * sizeof(float));",
                     """        pipe.InitBuffer(sum_, config.hidden * sizeof(float));
        pipe.InitBuffer(trace_, 3 * config.hidden * sizeof(float));""")
    source = replace(source,
                     """        asc_vf_call<AccumulateRow<First, Last>>(reinterpret_cast<__ubuf__ bfloat16_t *>(input.GetPhyAddr()),
            reinterpret_cast<__ubuf__ float *>(sum.GetPhyAddr()),
            reinterpret_cast<__ubuf__ bfloat16_t *>(output.GetPhyAddr()), config_.hidden);""",
                     """        auto trace = trace_.Get<float>();
        const bool record = GetBlockIdx() == 3;
        asc_vf_call<AccumulateRow<First, Last>>(reinterpret_cast<__ubuf__ bfloat16_t *>(input.GetPhyAddr()),
            reinterpret_cast<__ubuf__ float *>(sum.GetPhyAddr()),
            reinterpret_cast<__ubuf__ bfloat16_t *>(output.GetPhyAddr()), config_.hidden,
            reinterpret_cast<__ubuf__ float *>(trace.GetPhyAddr()), record);
        if (record) {
            PipeBarrier<PIPE_V>();
            TileXRProbe::Snapshot(input, config_.hidden, First ? 0 : 3);
            TileXRProbe::Snapshot(trace, config_.hidden, First ? 1 : 4);
            if constexpr (First) {
                TileXRProbe::Snapshot(sum, config_.hidden, 2);
            } else {
                TileXRProbe::Snapshot(trace[config_.hidden], config_.hidden, 5);
                TileXRProbe::Snapshot(trace[2 * config_.hidden], config_.hidden, 6);
                TileXRProbe::Snapshot(output, config_.hidden, 7);
            }
        }""")
    source = replace(source, "    TBuf<TPosition::VECCALC> sum_;",
                     "    TBuf<TPosition::VECCALC> sum_, trace_;")
    if mode in ("isolate", "raw"):
        source = replace(source, "        Reg::Cast<float, bfloat16_t, kToFloat>(value, payload, mask);",
                         """        if (record) {
            Reg::StoreAlign<bfloat16_t, Reg::StoreDist::DIST_PACK_B32>(
                reinterpret_cast<__ubuf__ bfloat16_t *>(trace + 3 * hidden) + offset, payload, mask);
        }
        Reg::Cast<float, bfloat16_t, kToFloat>(value, payload, mask);""")
        source = replace(source, "        pipe.InitBuffer(trace_, 3 * config.hidden * sizeof(float));",
                         "        pipe.InitBuffer(trace_, 4 * config.hidden * sizeof(float));")
        source = replace(source, "            TileXRProbe::Snapshot(trace, config_.hidden, First ? 1 : 4);",
                         """            TileXRProbe::Snapshot(trace, config_.hidden, First ? 1 : 4);
            TileXRProbe::Snapshot(trace_.Get<bfloat16_t>()[6 * config_.hidden], config_.hidden, First ? 8 : 9);""")
    if mode == "raw":
        source = replace(source, "        Reg::Cast<float, bfloat16_t, kToFloat>(value, payload, mask);",
                         """        if (record) {
            auto &raw = reinterpret_cast<Reg::RegTensor<uint32_t> &>(payload);
            Reg::StoreAlign(reinterpret_cast<__ubuf__ uint32_t *>(trace + 4 * hidden) + offset, raw, mask);
        }
        Reg::Cast<float, bfloat16_t, kToFloat>(value, payload, mask);""")
        source = replace(source, "        pipe.InitBuffer(trace_, 4 * config.hidden * sizeof(float));",
                         "        pipe.InitBuffer(trace_, 5 * config.hidden * sizeof(float));")
        source = replace(source,
                         "            TileXRProbe::Snapshot(trace_.Get<bfloat16_t>()[6 * config_.hidden], config_.hidden, First ? 8 : 9);",
                         """            TileXRProbe::Snapshot(trace_.Get<bfloat16_t>()[6 * config_.hidden], config_.hidden, First ? 8 : 9);
            TileXRProbe::Snapshot(trace_.Get<uint32_t>()[4 * config_.hidden], config_.hidden, First ? 10 : 11);""")
    return source


def gateway_barrier(source, pipe):
    return replace(source, "                Cast(bf16_output_slot_ub, fp32_accumulator_ub, RoundMode::CAST_RINT, element_num);",
                   f"""                PipeBarrier<{pipe}>();
                Cast(bf16_output_slot_ub, fp32_accumulator_ub, RoundMode::CAST_RINT, element_num);""")


def write_headers(destination, root, mode):
    (destination / "probe_snapshot.h").write_text(SNAPSHOT)
    pipe = "PIPE_ALL" if mode == "isolate" else "PIPE_MTE2"
    def gateway_instrument(source):
        return gateway(source) if mode == "trace" else gateway_barrier(source, pipe)

    sources = {"combine_ht_kernel.h": (root / "src/moonep/combine_ht/kernels", gateway_instrument),
               "final_reduce_worker.h": (root / "src/ep/combine_ht", lambda source: final(source, mode))}
    for name, (directory, instrument) in sources.items():
        (destination / name).write_text(instrument((directory / name).read_text()))
