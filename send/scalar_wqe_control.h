/* Copyright (c) 2026 TileXR Project.
 * Diagnostic control for WQE fields: copy the UB header and fill each row with
 * scalar UB accesses. The caller retains the snapshot/SQ publication path.
 * This control measures correctness only; it is not a SIMD performance variant.
 */
template <bool Signal, bool Sample = false>
__attribute__((always_inline)) inline __aicore__ void BuildPutWqes(
    LocalTensor<uint8_t> wqes, LocalTensor<uint8_t> header, LocalTensor<int64_t> records,
    uint32_t count, uint32_t head, uint64_t input, uint64_t remote, uint32_t bytes,
    uint32_t rowBegin, uint32_t rowEnd)
{
    auto output = wqes.ReinterpretCast<uint32_t>();
    auto prototype = header.ReinterpretCast<uint32_t>();
    const uint32_t headerBits = prototype.GetValue(0U) & kSqHeaderMask;
    for (uint32_t row = rowBegin; row < rowEnd; ++row) {
        const uint32_t first = row * kWqeU32Words;
        for (uint32_t word = 0U; word < kWqeU32Words; ++word) {
            output.SetValue(first + word, prototype.GetValue(word));
        }
        const uint32_t absolute = head + row;
        uint32_t control = (absolute & (kSqDepth - 1U)) | headerBits;
        control |= (absolute & kSqDepth) == 0U ? kSqOwnerBit : 0U;
        if constexpr (Signal) {
            control |= TileXR::TILEXR_UDMA_SQE_FLAG_ORDERED_COMPLETION << kSqFlagShift;
        } else if (absolute == head + count - 1U) {
            control |= TileXR::TILEXR_UDMA_SQE_FLAG_COMPLETION << kSqFlagShift;
        }
        uint32_t sourceRow = 0U, remoteRow = 0U;
        if constexpr (!Signal) {
            sourceRow = static_cast<uint32_t>(records.GetValue(row * kBackwardFields + kSendRow));
            remoteRow = static_cast<uint32_t>(records.GetValue(row * kBackwardFields + kPackedRow));
        }
        const uint64_t source = input + uint64_t(sourceRow) * bytes;
        const uint64_t destination = remote + uint64_t(remoteRow) * bytes;
        output.SetValue(first, control);
        output.SetValue(first + kSgeLengthWord, bytes);
        output.SetValue(first + kSgeAddressWord, static_cast<uint32_t>(source));
        output.SetValue(first + kSgeAddressWord + 1U, static_cast<uint32_t>(source >> 32U));
        output.SetValue(first + kRemoteWord, static_cast<uint32_t>(destination));
        output.SetValue(first + kRemoteWord + 1U, static_cast<uint32_t>(destination >> 32U));
    }
    TileXR::UDMASyncEvent<AscendC::HardEvent::S_MTE3>();
}
