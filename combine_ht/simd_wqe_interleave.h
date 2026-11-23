/* Copyright (c) 2026 TileXR Project.
 * Builds a 64-row WQE tile in registers, transposes its sixteen word vectors
 * with four Interleave stages and stores contiguous WQEs in the caller's UB.
 * Reuses Send's metadata and publication fences; no scalar row walk is added.
 */
__simd_callee__ inline void PutAddressWords(MicroAPI::RegTensor<uint32_t> &low,
    MicroAPI::RegTensor<uint32_t> &high, MicroAPI::RegTensor<uint32_t> &rows,
    MicroAPI::RegTensor<uint32_t> &stride, uint64_t base, MicroAPI::MaskReg &mask)
{
    using namespace MicroAPI;
    RegTensor<uint32_t> offsetLow, offsetHigh, one, zero, carry;
    MaskReg overflow;
    Mull(offsetLow, offsetHigh, rows, stride, mask);
    Adds(low, offsetLow, static_cast<uint32_t>(base), mask);
    Compare<uint32_t, CMPMODE::LT>(overflow, low, offsetLow, mask);
    Duplicate(one, 1U);
    Duplicate(zero, 0U);
    Select(carry, one, zero, overflow);
    Adds(high, offsetHigh, static_cast<uint32_t>(base >> kPackedShift), mask);
    Add(high, high, carry, mask);
}

template <bool Signal, bool Sample = false>
__aicore__ inline void BuildPutWqes(LocalTensor<uint8_t> wqes, LocalTensor<uint8_t> header,
    LocalTensor<int64_t> records, uint32_t count, uint32_t head,
    uint64_t input, uint64_t remote, uint32_t bytes, uint32_t rowBegin, uint32_t rowEnd)
{
    constexpr uint32_t recordWords = kBackwardFields * sizeof(int64_t) / sizeof(uint32_t);
    constexpr uint32_t sendWord = kSendRow * sizeof(int64_t) / sizeof(uint32_t);
    constexpr uint32_t packedWord = kPackedRow * sizeof(int64_t) / sizeof(uint32_t);
    constexpr uint32_t lanes = VECTOR_REG_WIDTH / sizeof(uint32_t);
    const uint32_t rows = rowEnd - rowBegin;
    auto *out = reinterpret_cast<__ubuf__ uint32_t *>(wqes.GetPhyAddr()) + rowBegin * kWqeU32Words;
    auto *peerHeader = reinterpret_cast<__ubuf__ uint32_t *>(header.GetPhyAddr());
    auto *meta = reinterpret_cast<__ubuf__ uint32_t *>(records.GetPhyAddr()) + rowBegin * recordWords;
    uint32_t remaining = rows;
    const uint16_t repeats = (rows + lanes - 1U) / lanes;
    __VEC_SCOPE__ {
        using namespace MicroAPI;
        RegTensor<int32_t> lane;
        RegTensor<uint32_t> recordIndex, length, headerBits, ringMask, wrapMask, ownerScale, completion, zero;
        Arange(lane, 0);
        auto all = CreateMask<uint32_t>();
        Muls(recordIndex, reinterpret_cast<RegTensor<uint32_t> &>(lane), recordWords, all);
        Duplicate(length, bytes);
        Duplicate(zero, 0U);
        DataCopyGather(headerBits, peerHeader, zero, all);
        RegTensor<uint32_t> headerMask;
        Duplicate(headerMask, kSqHeaderMask);
        And(headerBits, headerBits, headerMask, all);
        Duplicate(ringMask, kSqDepth - 1U);
        Duplicate(wrapMask, kSqDepth);
        Duplicate(ownerScale, kSqOwnerBit / kSqDepth);
        Duplicate(completion, (Signal ? TileXR::TILEXR_UDMA_SQE_FLAG_ORDERED_COMPLETION :
            TileXR::TILEXR_UDMA_SQE_FLAG_COMPLETION) << kSqFlagShift);
        for (uint16_t repeat = 0U; repeat < repeats; ++repeat) {
            const uint32_t row = repeat * lanes;
            auto mask = UpdateMask<uint32_t>(remaining);
            RegTensor<uint32_t> v0, v1, v2, v3, v4, v5, v6, v7;
            RegTensor<uint32_t> v8, v9, v10, v11, v12, v13, v14, v15;
            RegTensor<uint32_t> absolute, owner, flag, sourceRows, remoteRows;
            Adds(absolute, reinterpret_cast<RegTensor<uint32_t> &>(lane), head + rowBegin + row, mask);
            And(v0, absolute, ringMask, mask);
            Not(owner, absolute, mask);
            And(owner, owner, wrapMask, mask);
            Mul(owner, owner, ownerScale, mask);
            Or(v0, v0, owner, mask);
            Or(v0, v0, headerBits, mask);
            if constexpr (Signal) {
                Or(v0, v0, completion, mask);
                sourceRows = zero;
                remoteRows = zero;
            } else {
                MaskReg tail;
                CompareScalar<uint32_t, CMPMODE::EQ>(tail, absolute, head + count - 1U, mask);
                Select(flag, completion, zero, tail);
                Or(v0, v0, flag, mask);
                auto *batchMeta = meta + row * recordWords;
                DataCopyGather(sourceRows, batchMeta + sendWord, recordIndex, mask);
                DataCopyGather(remoteRows, batchMeta + packedWord, recordIndex, mask);
            }
            PutAddressWords(v14, v15, sourceRows, length, input, mask);
            PutAddressWords(v10, v11, remoteRows, length, remote, mask);
            v12 = length;
            DataCopyGather(v1, peerHeader + 1U, zero, all);
            DataCopyGather(v2, peerHeader + 2U, zero, all);
            DataCopyGather(v3, peerHeader + 3U, zero, all);
            DataCopyGather(v4, peerHeader + 4U, zero, all);
            DataCopyGather(v5, peerHeader + 5U, zero, all);
            DataCopyGather(v6, peerHeader + 6U, zero, all);
            DataCopyGather(v7, peerHeader + 7U, zero, all);
            DataCopyGather(v8, peerHeader + 8U, zero, all);
            DataCopyGather(v9, peerHeader + 9U, zero, all);
            DataCopyGather(v13, peerHeader + 13U, zero, all);
            // Each stage halves the field distance. After four stages vN holds
            // four complete, consecutive WQEs, starting at row 4*N.
            Interleave(v0, v8, v0, v8);
            Interleave(v1, v9, v1, v9);
            Interleave(v2, v10, v2, v10);
            Interleave(v3, v11, v3, v11);
            Interleave(v4, v12, v4, v12);
            Interleave(v5, v13, v5, v13);
            Interleave(v6, v14, v6, v14);
            Interleave(v7, v15, v7, v15);
            Interleave(v0, v4, v0, v4);
            Interleave(v1, v5, v1, v5);
            Interleave(v2, v6, v2, v6);
            Interleave(v3, v7, v3, v7);
            Interleave(v8, v12, v8, v12);
            Interleave(v9, v13, v9, v13);
            Interleave(v10, v14, v10, v14);
            Interleave(v11, v15, v11, v15);
            Interleave(v0, v2, v0, v2);
            Interleave(v1, v3, v1, v3);
            Interleave(v4, v6, v4, v6);
            Interleave(v5, v7, v5, v7);
            Interleave(v8, v10, v8, v10);
            Interleave(v9, v11, v9, v11);
            Interleave(v12, v14, v12, v14);
            Interleave(v13, v15, v13, v15);
            Interleave(v0, v1, v0, v1);
            Interleave(v2, v3, v2, v3);
            Interleave(v4, v5, v4, v5);
            Interleave(v6, v7, v6, v7);
            Interleave(v8, v9, v8, v9);
            Interleave(v10, v11, v10, v11);
            Interleave(v12, v13, v12, v13);
            Interleave(v14, v15, v14, v15);
            uint32_t remainingWords = (rows - row) * kWqeU32Words;
            // A single post-increment pointer avoids passing sixteen separate
            // destination addresses through the vector-function interface.
            auto *destination = out + row * kWqeU32Words;
            MaskReg storeMask;
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v0, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v1, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v2, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v3, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v4, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v5, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v6, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v7, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v8, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v9, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v10, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v11, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v12, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v13, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v14, lanes, storeMask);
            storeMask = UpdateMask<uint32_t>(remainingWords);
            DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, v15, lanes, storeMask);
        }
    }
}
