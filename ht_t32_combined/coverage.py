"""Validate the fixed T32 routing and count rows per sender/peer/lane/chunk."""
import collections
import json
import struct


def coverage(archive, expected_lane_rows=(2715, 795), *, payload_groups=None):
    def read(name):
        return archive.extractfile(name).read()

    manifest = json.loads(read("fixture-data/manifest.json"))
    assert (manifest["world"], manifest["tokens"], manifest["hidden"], manifest["block_dim"],
            manifest["chunks"], manifest["row_bytes"]) == (64, 32, 256, 16, 4, 512)
    workload = json.loads(read("workload.yml"))["workload_config"]
    assert len(workload) == 64
    counts_by_lane = [collections.Counter(), collections.Counter()]
    lane_rows = [0, 0]
    across_stripe = 0
    expected_rings = {}
    for rank in range(64):
        chip = workload[f"chip{rank}"]
        assert chip["block_dim"] == 16 and chip["vec_func"] == manifest["entry"]
        params = chip["kernel_param"]
        field = lambda key: params["config." + key]["value"]
        assert (field("tokens"), field("hidden"), field("chunks"), field("rank")) == (32, 256, 4, rank)
        assert all(field(key) % 512 == 0 for key in ("inputOffset", "partialOffset", "returnOffset"))
        prefix = f"fixture-data/rank{rank:02d}/"
        plan = json.loads(read(prefix + "plan.json"))
        owners = {peer: owner for peer, chunk, owner in plan["so"] if chunk == 3}
        assert len(owners) == len(plan["so"]) == 56
        packed = read(prefix + "forward.bin")
        image = read(f"input_bin/rank{rank:02d}/forward.bin")
        capacity = field("forwardCapacity")
        assert len(packed) == 64 * capacity * 24 and len(image) == 64 * capacity * 32
        for offset in range(0, 24, 4):
            for byte in range(4):
                assert packed[offset + byte::24] == image[offset + byte::32]
        ranges = read(prefix + "ranges.bin")
        counts = read(prefix + "counts.bin")
        assert len(ranges) == 64 * 4 * 8 and len(counts) == 64 * 4
        rank_rings = {}
        if payload_groups is not None:
            payload_groups[rank] = {}
        pending_per_owner = collections.Counter()
        for peer, owner in owners.items():
            ordinal = 0
            lane_tail = [0, 0]
            valid_rows, = struct.unpack_from("<i", counts, peer * 4)
            assert 0 <= valid_rows <= capacity
            for chunk in range(4):
                begin, end = struct.unpack_from("<ii", ranges, (peer * 4 + chunk) * 8)
                rows = [0, 0]
                assert 0 <= begin <= end <= valid_rows
                while begin < end:
                    following, token, primary, _, _, contributor = struct.unpack_from(
                        "<6i", packed, (peer * capacity + begin) * 24
                    )
                    assert begin < following <= end and chunk * 8 <= token < (chunk + 1) * 8
                    assert 0 <= primary < field("inputRows" if contributor < 0 else "partialRows")
                    rows[int(ordinal % 4 == 3)] += 1
                    ordinal += 1
                    begin = following
                for lane, count in enumerate(rows):
                    if count:
                        counts_by_lane[lane][count] += 1
                        lane_rows[lane] += count
                        pending_per_owner[owner, lane, chunk] += count
                        assert count <= 8
                        across_stripe += (lane_tail[lane] % 8 + count > 8)
                        if payload_groups is not None:
                            ring = peer * 40 + lane * 20 + owner
                            payload_groups[rank].setdefault(ring, []).append((lane_tail[lane], count))
                        lane_tail[lane] += count
            for lane, count in enumerate(lane_tail):
                rank_rings[peer * 40 + lane * 20 + owner] = count + 1
        assert max(pending_per_owner.values(), default=0) < 32
        expected_rings[rank] = rank_rings
    assert tuple(lane_rows) == tuple(expected_lane_rows)
    stats = {
        "payload_rows_by_lane": lane_rows,
        "rows_per_sender_peer_lane_chunk": [dict(sorted(counts.items())) for counts in counts_by_lane],
        "multi_row_groups": [sum(n for size, n in counts.items() if size > 1) for counts in counts_by_lane],
        "payload_groups_crossing_512b_sq_stripe": across_stripe,
    }
    return stats, expected_rings
