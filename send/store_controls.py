"""Generate full-tile controls for stores with and without Interleave."""

BASE_KINDS = ("store_post", "interleave_post", "interleave_explicit")
POINTER_KINDS = ("pointer64", "pointer256")
COMPENSATION_KINDS = ("pointer1024", "store_post_compensated", "interleave_post_compensated")
KINDS = BASE_KINDS + POINTER_KINDS + COMPENSATION_KINDS


def pointer_strides(kind):
    return {"pointer64": (64, 64), "pointer256": (256, 256), "pointer1024": (1024, 64)}[kind]


def generate(interleave_source, kind):
    assert kind in KINDS
    if kind in POINTER_KINDS or kind == "pointer1024":
        return generate_pointer(kind)
    compensated = kind.endswith("_compensated")
    base_kind = kind[:-len("_compensated")] if compensated else kind
    begin = interleave_source.index("            Interleave(v0, v8, v0, v8);")
    end = interleave_source.index("            uint32_t remainingWords", begin)
    operations = "" if base_kind == "store_post" else interleave_source[begin:end]
    declarations = "        RegTensor<uint32_t> " + ", ".join(f"v{i}" for i in range(16)) + ";\n"
    duplicates = "".join(f"        Duplicate(v{i}, base + {i}U);\n" for i in range(16))
    if base_kind == "interleave_explicit":
        stores = "".join(
            f"        destination = output + {i}U * lanes;\n"
            f"        DataCopy<uint32_t>(destination, v{i}, mask);\n" for i in range(16))
    else:
        stride = "lanes * 16U" if compensated else "lanes"
        stores = "".join(
            f"        DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v{i}, {stride}, mask);\n"
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
    expected = "index / 64U" if base_kind == "store_post" else "index % 16U"
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


def generate_pointer(kind):
    stride, expected_stride = pointer_strides(kind)
    source = """/* Copyright (c) 2026 TileXR Project. Two-store address diagnostic. */
constexpr uint32_t kStoreProbeBase = 0x13579bdfU;
constexpr uint32_t kStoreProbeSentinel = 0xc3c3c3c3U;
constexpr uint32_t kStoreProbeWords = 1024U, kStoreProbeGuardWords = 8U;
constexpr uint32_t kStoreProbeMeta = 1040U, kStoreProbeInitWords = 1056U;
constexpr uint32_t kStoreProbeStride = REQUEST_STRIDE;
constexpr uint32_t kStoreProbeExpectedStride = EXPECTED_STRIDE;
static_assert(kBatchRows * kWqeU32Words >= kStoreProbeInitWords, "probe must fit existing scratch");
static_assert(kStoreProbeStride + 64U <= kBatchRows * kWqeU32Words, "nominal second store must fit scratch");
static_assert(kStoreProbeExpectedStride + 64U <= kStoreProbeWords, "both expected stores must fit checked tile");
__aicore__ inline void ProbeOperation(__ubuf__ uint32_t *output)
{
    const uint32_t base = output[0];
    __VEC_SCOPE__ {
        using namespace MicroAPI;
        auto mask = CreateMask<uint32_t>();
        RegTensor<uint32_t> first, second, delta;
        Duplicate(first, base);
        Duplicate(second, base + 1U);
        auto *destination = output;
        DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, first, kStoreProbeStride, mask);
        DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, second, kStoreProbeStride, mask);
        Duplicate(delta, static_cast<uint32_t>(destination - output));
        auto *metadata = output + kStoreProbeMeta;
        uint32_t metadataWords = 16U;
        auto metadataMask = UpdateMask<uint32_t>(metadataWords);
        DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(metadata, delta, 16U, metadataMask);
    }
}
""".replace("REQUEST_STRIDE", str(stride) + "U").replace("EXPECTED_STRIDE", str(expected_stride) + "U")
    setup = """        if ((wqes.GetPhyAddr() & 31U) != 0U) return 0xd8000000U;
        auto probeWqes = wqes.ReinterpretCast<uint32_t>();
        for (uint32_t index = 0U; index < kStoreProbeInitWords; ++index) {
            probeWqes.SetValue(index, kStoreProbeSentinel);
        }
        probeWqes.SetValue(0U, kStoreProbeBase);
        TileXR::UDMASyncEvent<AscendC::HardEvent::S_V>();
        ProbeOperation(reinterpret_cast<__ubuf__ uint32_t *>(wqes.GetPhyAddr()));
        TileXR::UDMASyncEvent<AscendC::HardEvent::V_S>();
        const uint32_t deltaWords = probeWqes.GetValue(kStoreProbeMeta);
        if (deltaWords != 2U * kStoreProbeExpectedStride) return 0xe0000000U | (deltaWords & 0x00ffffffU);
        for (uint32_t index = 0U; index < kStoreProbeWords; ++index) {
            const uint32_t expected = index < 64U ? kStoreProbeBase :
                (index >= kStoreProbeExpectedStride && index < kStoreProbeExpectedStride + 64U ?
                 kStoreProbeBase + 1U : kStoreProbeSentinel);
            const uint32_t actual = probeWqes.GetValue(index);
            if (actual != expected) return 0xd0000000U | (index << 16U) | (actual & 0xffffU);
        }
        for (uint32_t index = 0U; index < kStoreProbeGuardWords; ++index) {
            const uint32_t actual = probeWqes.GetValue(kStoreProbeWords + index);
            if (actual != kStoreProbeSentinel) return 0xd4000000U | (index << 16U) | (actual & 0xffffU);
        }
"""
    return source, setup


def decode(status, kind):
    prefix = status & 0xfc000000
    index = (status >> 16) & 0x3ff
    if prefix == 0xe0000000:
        _, stride = pointer_strides(kind)
        return {"boundary": "pointer_delta", "expected_delta_words": 2 * stride,
                "actual_delta_words": status & 0x00ffffff,
                "metadata_matches_sentinel": (status & 0x00ffffff) == 0x00c3c3c3}
    if prefix == 0xd0000000:
        if kind in POINTER_KINDS or kind == "pointer1024":
            _, stride = pointer_strides(kind)
            expected = (0x13579bdf if index < 64 else
                        0x13579be0 if stride <= index < stride + 64 else 0xc3c3c3c3)
        else:
            expected = 0x13579bdf + (index // 64 if kind.startswith("store_post") else index % 16)
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
            "controls": value.get("controls", "full-tile"), "simulation_only": True,
            "contract": value.get("contract"), "host_errors": value.get("host_errors", []),
            "results": [{key: row[key] for key in keys if key in row}
                        for row in value.get("results", [])]}
