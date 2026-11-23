/* Copyright (c) 2026 TileXR Project.
 * Test one SIMD register store in the caller's completed queue-read scratch.
 * Flush checks all sixteen words after V->S completion, keeping the store live.
 * The probe does not modify WQE fields or change the SQ publication boundary.
 */
constexpr uint32_t kMinimalSimdProbeValue = 0x13579bdfU;

__aicore__ inline void ProbeMinimalSimd(__ubuf__ uint32_t *output)
{
    __VEC_SCOPE__ {
        using namespace MicroAPI;
        uint32_t active = 16U;
        auto mask = UpdateMask<uint32_t>(active);
        RegTensor<uint32_t> value;
        Duplicate(value, kMinimalSimdProbeValue);
        auto *destination = output;
        DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, value, 16U, mask);
    }
}
