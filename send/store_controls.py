"""Generate full-tile controls for stores with and without Interleave."""

KINDS = ("store_post", "interleave_post", "interleave_explicit")


def generate(interleave_source, kind):
    assert kind in KINDS
    begin = interleave_source.index("            Interleave(v0, v8, v0, v8);")
    end = interleave_source.index("            uint32_t remainingWords", begin)
    operations = "" if kind == "store_post" else interleave_source[begin:end]
    declarations = "        RegTensor<uint32_t> " + ", ".join(f"v{i}" for i in range(16)) + ";\n"
    duplicates = "".join(f"        Duplicate(v{i}, base + {i}U);\n" for i in range(16))
    if kind == "interleave_explicit":
        stores = "".join(
            f"        destination = output + {i}U * lanes;\n"
            f"        DataCopy<uint32_t>(destination, v{i}, mask);\n" for i in range(16))
    else:
        stores = "".join(
            f"        DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v{i}, lanes, mask);\n"
            for i in range(16))
    source = """/* Copyright (c) 2026 TileXR Project. Full-tile SIMD controls. */
constexpr uint32_t kStoreProbeBase = 0x13579bdfU;
constexpr uint32_t kStoreProbeSentinel = 0xc3c3c3c3U;
constexpr uint32_t kStoreProbeWords = 1024U, kStoreProbeGuardWords = 8U;
static_assert(kBatchRows * kWqeU32Words >= kStoreProbeWords + kStoreProbeGuardWords,
    "store probe and guard must fit the existing WQE scratch");
__aicore__ inline void ProbeOperation(__ubuf__ uint32_t *output)
{
    const uint32_t base = output[0];
    __VEC_SCOPE__ {
        using namespace MicroAPI;
        constexpr uint32_t lanes = VECTOR_REG_WIDTH / sizeof(uint32_t);
        static_assert(lanes == 64U, "this probe targets 64 uint32 lanes");
        auto mask = CreateMask<uint32_t>();
""" + declarations + duplicates + operations + "        auto *destination = output;\n" + stores + "    }\n}\n"
    expected = "index / 64U" if kind == "store_post" else "index % 16U"
    setup = """        if ((wqes.GetPhyAddr() & 31U) != 0U) return 0xd8000000U;
        auto probeWqes = wqes.ReinterpretCast<uint32_t>();
        for (uint32_t index = 0U; index < kStoreProbeWords + kStoreProbeGuardWords; ++index) {
            probeWqes.SetValue(index, kStoreProbeSentinel);
        }
        probeWqes.SetValue(0U, kStoreProbeBase);
        TileXR::UDMASyncEvent<AscendC::HardEvent::S_V>();
        ProbeOperation(reinterpret_cast<__ubuf__ uint32_t *>(wqes.GetPhyAddr()));
        TileXR::UDMASyncEvent<AscendC::HardEvent::V_S>();
        for (uint32_t index = 0U; index < kStoreProbeWords; ++index) {
            const uint32_t actual = probeWqes.GetValue(index);
            if (actual != kStoreProbeBase + EXPECTED) {
                return 0xd0000000U | (index << 16U) | (actual & 0xffffU);
            }
        }
        for (uint32_t index = 0U; index < kStoreProbeGuardWords; ++index) {
            const uint32_t actual = probeWqes.GetValue(kStoreProbeWords + index);
            if (actual != kStoreProbeSentinel) {
                return 0xd4000000U | (index << 16U) | (actual & 0xffffU);
            }
        }
""".replace("EXPECTED", expected)
    return source, setup


def decode(status, kind):
    prefix = status & 0xfc000000
    index = (status >> 16) & 0x3ff
    if prefix == 0xd0000000:
        expected = 0x13579bdf + (index // 64 if kind == "store_post" else index % 16)
        return {"boundary": "tile", "word": index, "register_slot": index // 64,
                "lane": index % 64, "expected_low16": expected & 0xffff,
                "actual_low16": status & 0xffff}
    if prefix == 0xd4000000:
        return {"boundary": "tail_guard", "word": index, "actual_low16": status & 0xffff}
    if status == 0xd8000000:
        return {"boundary": "alignment"}
    return None


def summarize_batch(value):
    keys = ("ok", "variant", "report", "object_sha256", "build_completed", "returncode",
            "wqe_verified", "checked_wqes", "mismatch_count", "checked_status_words",
            "status_mismatch_count", "status_guard_failures", "status_words",
            "backward_record_bytes", "stop_boundary", "error", "failure", "fatal",
            "probe_kind", "probe_mismatch_count", "probe_first_mismatches", "compile_errors")
    return {"ok": value["ok"], "diagnostic_only": True,
            "contract": value.get("contract"), "host_errors": value.get("host_errors", []),
            "results": [{key: row[key] for key in keys if key in row}
                        for row in value.get("results", [])]}
