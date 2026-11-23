# Copyright (c) 2026 TileXR Project.
"""Generate a one-pointer interface for the existing contiguous SIMD builder.

Flush reuses the first 48 bytes of its completed queue-read scratch. The builder
reads those values before vector work; the following queue load refills scratch.
The vector instruction body and the snapshot/publication path are preserved.
"""


def call_block(template, row_begin, row_end):
    values = ("static_cast<uint32_t>(wqes.GetPhyAddr())", "static_cast<uint32_t>(header.GetPhyAddr())",
              "static_cast<uint32_t>(records.GetPhyAddr())", "count", "head",
              "static_cast<uint32_t>(input)", "static_cast<uint32_t>(input >> 32U)",
              "static_cast<uint32_t>(remote)", "static_cast<uint32_t>(remote >> 32U)",
              "bytes", row_begin, row_end)
    lines = ["            {", "                static_assert(12U * sizeof(uint32_t) <= 64U, \"queue read scratch must fit arguments\");"]
    lines += ["                stateRead.SetValue(" + str(index) + "U, " + value + ");"
              for index, value in enumerate(values)]
    lines += ["                TileXR::UDMASyncEvent<AscendC::HardEvent::S_V>();",
              "                BuildPutWqesPacked<" + template + ">(",
              "                    reinterpret_cast<__ubuf__ uint32_t *>(stateRead.GetPhyAddr()));", "            }"]
    return "\n".join(lines)


def transform(header, builder):
    begin = builder.index("template <bool Signal, bool Sample = false>")
    body = builder.index("{", begin)
    signature = """template <bool Signal, bool Sample = false>
__aicore__ inline void BuildPutWqesPacked(__ubuf__ const uint32_t *parameters)
"""
    decoded = """
    const uint32_t count = parameters[3], head = parameters[4], bytes = parameters[9];
    const uint32_t rowBegin = parameters[10], rowEnd = parameters[11];
    const uint64_t input = uint64_t(parameters[5]) | (uint64_t(parameters[6]) << 32U);
    const uint64_t remote = uint64_t(parameters[7]) | (uint64_t(parameters[8]) << 32U);
"""
    builder = builder[:begin] + signature + "{" + decoded + builder[body + 1:]
    pointers = (("wqes", 0), ("header", 1), ("records", 2))
    for name, index in pointers:
        marker = name + ".GetPhyAddr()"
        assert builder.count(marker) == 1
        builder = builder.replace(marker, "uint64_t(parameters[" + str(index) + "])")
    for marker, replacement in (
        ("            BuildPutWqes<Signal, true>(wqes, header, records, count, head, input, remote, bytes, 0, 1);",
         call_block("Signal, true", "0U", "1U")),
        ("            BuildPutWqes<Signal>(wqes, header, records, count, head, input, remote, bytes, row_begin, count);",
         call_block("Signal", "row_begin", "count")),
    ):
        assert header.count(marker) == 1
        header = header.replace(marker, replacement)
    return header, builder
