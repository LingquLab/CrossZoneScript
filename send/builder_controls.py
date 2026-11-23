"""Generate narrowly scoped experiments on VF-local mask state."""
KINDS = ("interleave_local_mask", "interleave_local_mask_runtime")


def transform(source, kind):
    assert kind in KINDS
    external = "    uint32_t remaining = rows;\n"
    scope = "    __VEC_SCOPE__ {\n        using namespace MicroAPI;\n"
    assert source.count(external) == source.count(scope) == 1
    source = source.replace(external, "")
    source = source.replace(scope, scope + "        uint32_t remaining = rows;\n")
    if kind.endswith("_runtime"):
        declaration = "    const uint16_t repeats = (rows + lanes - 1U) / lanes;\n"
        assert source.count(declaration) == 1
        source = source.replace(declaration, declaration + """    if (rows == 0U) return;
    // Seed the current tile's first UB word before its complete vector overwrite.
    auto *strideWord = reinterpret_cast<__ubuf__ volatile uint32_t *>(out);
    strideWord[0] = lanes;
    const int32_t postStride = static_cast<int32_t>(strideWord[0]);
    TileXR::UDMASyncEvent<AscendC::HardEvent::S_V>();
""")
        assert source.count(", lanes, storeMask);") == 16
        source = source.replace(", lanes, storeMask);", ", postStride, storeMask);")
    return source
