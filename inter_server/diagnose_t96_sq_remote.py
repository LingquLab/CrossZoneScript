"""Count transport evidence by ring and slot, without exporting traces."""


def expected_slots(case, rank):
    folder = case / "fixture-data" / f"rank{rank:02d}"
    plan = json.loads((folder / "plan.json").read_text())
    ranges = (folder / "ranges.bin").read_bytes()
    forward = (folder / "forward.bin").read_bytes()
    expected = {}
    for peer, chunk, owner in plan["so"]:
        assert chunk == 3
        lanes = [0, 0]
        ordinal = 0
        for part in range(4):
            begin, end = struct.unpack_from("<ii", ranges, (peer * 4 + part) * 8)
            while begin < end:
                following, = struct.unpack_from("<i", forward, (peer * 96 * 6 + begin) * 24)
                assert begin < following <= end
                lanes[ordinal % 4 == 3] += 1
                ordinal += 1
                begin = following
        for lane, payload in enumerate(lanes):
            expected[peer * 40 + lane * 20 + owner] = payload + 1
    return expected


def read_observed(path, host):
    ring_bytes = 16384 * 64
    bases = {"RD_REQ": 0x1900000000, "WR_REQ": 0x1A00000000}
    observed = {event: collections.defaultdict(set) for event in bases}
    with path.open("rb") as trace:
        prefix = trace.read(32 * 1024 * 1024)
        truncated = bool(trace.read(1))
    for match in ADDRESS_RECORD.finditer(prefix.decode("utf-8", "replace")):
        try:
            args = json.loads(match.group())
            event = args["busPayloadType_"]
            if event not in bases:
                continue
            raw = args["oriAddr_"]
            value = int(raw, 0) if isinstance(raw, str) else int(raw)
        except (ValueError, KeyError, TypeError):
            continue
        chip, address = divmod(value, GM_BYTES)
        ring, offset = divmod(address - bases[event], ring_bytes)
        if chip == host and 0 <= ring < 64 * 40:
            observed[event][ring].add(offset // 64)
    return observed, truncated


def diagnose_t96():
    case = CASE.with_name("t96-repacked-sst-mte-drain-count-cache-38d743f7")
    assert json.loads((case / "run.json").read_text())["status"] == "timed_out"
    total = collections.Counter()
    active_missing_cq = collections.Counter()
    for host in range(64):
        expected = expected_slots(case, host)
        path = case / "OUTPUT/stat_trace" / f"Host{host}_ba_layer_trace.json"
        if not path.is_file():
            continue
        observed, truncated = read_observed(path, host)
        total["hosts_sampled"] += 1
        total["truncated"] += truncated
        for ring, count in expected.items():
            label = "payload_qp" if count > 1 else "so_only_qp"
            total[label + "_rings"] += 1
            total[label + "_expected_slots"] += count
            for name, event in (("sq", "RD_REQ"), ("cq", "WR_REQ")):
                slots = observed[event].get(ring, set())
                covered = sum(slot in slots for slot in range(count))
                total[label + "_" + name + "_observed_expected_slots"] += covered
                total[label + "_" + name + "_rings_complete"] += int(covered == count)
                if count > 1 and name == "cq":
                    active_missing_cq.update(str(slot // 8) for slot in range(count) if slot not in slots)
    return {"status": "timed_out", "trace_evidence": total,
            "active_cq_missing_slots_by_512b_group": active_missing_cq}
