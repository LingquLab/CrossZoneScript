# Copyright (c) 2026 TileXR Project.
"""Generate checked gather and transpose controls before scalar WQE assembly.

Gather uses the completed 64B queue-read scratch. The transpose control writes
4096 bytes within the 8192B WQE allocation, checks the first/last rows, then lets
the passing scalar builder construct all descriptors consumed by the snapshot.
"""

GATHER_SOURCE = """constexpr uint32_t kOperationProbeValue = 0x13579bdfU;
__aicore__ inline void ProbeOperation(__ubuf__ uint32_t *output)
{
    __VEC_SCOPE__ {
        using namespace MicroAPI;
        uint32_t active = 16U;
        auto mask = UpdateMask<uint32_t>(active);
        RegTensor<uint32_t> indices, gathered;
        Duplicate(indices, 0U);
        DataCopyGather(gathered, output, indices, mask);
        auto *destination = output;
        DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, gathered, 16U, mask);
    }
}
"""


def install(header, interleave_source, kind):
    assert kind in ("gather", "interleave")
    if kind == "gather":
        source = GATHER_SOURCE
        setup = """        if ((stateRead.GetPhyAddr() & 31U) != 0U) return 0xd0ffU;
        for (uint32_t word = 0U; word < 16U; ++word) stateRead.SetValue(word, kOperationProbeValue + word);
        TileXR::UDMASyncEvent<AscendC::HardEvent::S_V>();
        ProbeOperation(reinterpret_cast<__ubuf__ uint32_t *>(stateRead.GetPhyAddr()));
        TileXR::UDMASyncEvent<AscendC::HardEvent::V_S>();
        for (uint32_t word = 0U; word < 16U; ++word) {
            if (stateRead.GetValue(word) != kOperationProbeValue) return 0xd000U + word;
        }
"""
    else:
        begin = interleave_source.index("            Interleave(v0, v8, v0, v8);")
        end = interleave_source.index("            uint32_t remainingWords", begin)
        operations = interleave_source[begin:end]
        prefix = """constexpr uint32_t kOperationProbeValue = 0x13579bdfU;
static_assert(kBatchRows * kWqeU32Words >= 16U * (VECTOR_REG_WIDTH / sizeof(uint32_t)), "transpose probe must fit WQE scratch");
__aicore__ inline void ProbeOperation(__ubuf__ uint32_t *output)
{
    __VEC_SCOPE__ {
        using namespace MicroAPI;
        constexpr uint32_t lanes = VECTOR_REG_WIDTH / sizeof(uint32_t);
        static_assert(lanes == 64U, "this diagnostic checks the C310 64-lane transpose");
        auto mask = CreateMask<uint32_t>();
"""
        declarations = "        RegTensor<uint32_t> " + ", ".join("v" + str(i) for i in range(16)) + ";\n"
        duplicates = "".join("        Duplicate(v" + str(i) + ", base + " + str(i) + "U);\n" for i in range(16))
        stores = "".join("        DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v" + str(i) + ", lanes, mask);\n" for i in range(16))
        source = prefix + declarations + duplicates + operations + "        auto *destination = output;\n" + stores + "    }\n}\n"
        source = source.replace("{\n    __VEC_SCOPE__", "{\n    const uint32_t base = output[0];\n    __VEC_SCOPE__", 1)
        setup = """        if ((wqes.GetPhyAddr() & 31U) != 0U) return 0xd1ffU;
        wqes.ReinterpretCast<uint32_t>().SetValue(0U, kOperationProbeValue);
        TileXR::UDMASyncEvent<AscendC::HardEvent::S_V>();
        ProbeOperation(reinterpret_cast<__ubuf__ uint32_t *>(wqes.GetPhyAddr()));
        TileXR::UDMASyncEvent<AscendC::HardEvent::V_S>();
        auto probeWqes = wqes.ReinterpretCast<uint32_t>();
        for (uint32_t word = 0U; word < 16U; ++word) {
            if (probeWqes.GetValue(word) != kOperationProbeValue + word) return 0xd100U + word;
            if (probeWqes.GetValue(63U * kWqeU32Words + word) != kOperationProbeValue + word) return 0xd200U + word;
        }
"""
    source = "/* Copyright (c) 2026 TileXR Project. Checked SIMD operation control. */\n" + source
    include = '#include "scalar_wqe_control.h"'
    assert header.count(include) == 1
    header = header.replace(include, include + '\n#include "simd_operation_probe.h"')
    begin = header.index("struct LocalPutBatch")
    end = header.index("struct LocalUrmaSend", begin)
    body = header[begin:end]
    marker = "        uint32_t row_begin = 0;"
    assert body.count(marker) == 1
    header = header[:begin] + body.replace(marker, setup + marker) + header[end:]
    return header, source
