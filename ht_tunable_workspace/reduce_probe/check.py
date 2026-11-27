

if __name__ == "__main__":
    import struct
    import sys

    case = Path(sys.argv[1])
    spec = json.loads((case / "check.json").read_bytes())
    decoder = Path("/opt/sim/plugins/libDavinciSimulator.so")
    actual = {}
    for name, export in spec["exports"].items():
        paths = [case / "bin" / f"david_0_die{die}_{name}.bin" for die in (1, 2)]
        actual[name] = reconstruct_die_output(paths, export["addr"], export["size"], decoder)
        (case / f"actual-{name}.bin").write_bytes(actual[name])

    field = spec["field"]
    observed = list(struct.unpack("<" + "H" * (len(actual[field]) // 2), actual[field]))
    references = {name: (case / f"{name}-{field}.bin").read_bytes() for name in spec["references"]}

    def decode(word):
        return struct.unpack("<f", struct.pack("<I", word << 16))[0]

    comparisons = {}
    for name, data in references.items():
        expected = list(struct.unpack("<" + "H" * (len(data) // 2), data))
        differences = [(i, decode(want), decode(got))
                       for i, (want, got) in enumerate(zip(expected, observed, strict=True)) if want != got]
        comparisons[name] = dict(tested=len(expected), mismatches=len(differences), first=differences[:2])
    column = spec["column"]
    sample = {name: decode(struct.unpack_from("<H", data, column * 2)[0]) for name, data in references.items()}
    sample["probe"] = decode(observed[column])
    status = [struct.unpack_from("<I", actual["status"], offset)[0] for offset in spec["status_offsets"]]
    guards = {name: set(data) == {222} for name, data in actual.items() if name.startswith("guard_")}
    ready_ok = (actual["ready"] == (case / "golden-ready.bin").read_bytes()) if "ready" in actual else None
    trace = {}
    for name, layout in spec["trace"].items():
        expected = (case / f"golden-trace-{name}.bin").read_bytes()
        offset, size, dtype = (layout[key] for key in ("offset", "size", "dtype"))
        received = actual["trace"][offset:offset + size]
        fmt = f"<{size // struct.calcsize(dtype)}{dtype}"
        expected_values, received_values = struct.unpack(fmt, expected), struct.unpack(fmt, received)
        mismatch = sum(before != after for before, after in zip(expected_values, received_values, strict=True))
        got, want = received_values[column], expected_values[column]
        if dtype == "H":
            got, want = decode(got), decode(want)
        trace[name] = [mismatch, want, got]
    print(json.dumps(dict(compared=comparisons, sample=sample, sample_column=column,
                          status=status, guards=guards, ready_ok=ready_ok, trace=trace), separators=(",", ":")))
