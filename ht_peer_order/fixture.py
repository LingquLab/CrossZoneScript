"""Generate fixed random TopK routes and expected HT transport buffers."""
from collections import defaultdict
import hashlib
import json
import random
import struct

WORLD, LOCAL, TOPK, EXPERTS, ROW_BYTES = 64, 8, 6, 256, 512
BASE = 20261124
MAGIC = 2026


def make_routes(tokens, seed):
    rng = random.Random(seed)
    rows = [defaultdict(list) for _ in range(WORLD)]
    destinations = []
    for source in range(WORLD):
        route = []
        for token in range(tokens):
            experts = rng.sample(range(EXPERTS), TOPK)
            route.append(experts)
            by_server = defaultdict(list)
            for expert in experts:
                rank = expert // (EXPERTS // WORLD)
                by_server[rank // LOCAL].append(rank)
            for server, contributors in by_server.items():
                if server != source // LOCAL:
                    rows[contributors[0]][source].append((token, len(contributors)))
        destinations.append(route)
    route_blob = json.dumps(destinations, separators=(',', ':')).encode()
    return rows, destinations, hashlib.sha256(route_blob).hexdigest()


def payload(source, token, server):
    # The transport treats the row as bytes. Exact FP32 values aid inspection.
    return struct.pack('<128f', *((source * 8 + token + server / 8 + h / 128) for h in range(128)))


def generate_inputs(tokens, seed, abis, modules):
    native, runtime = modules
    rows, routes, route_sha = make_routes(tokens, seed)
    configs = [native.config_values(abi) for abi in abis]
    expected = [bytearray(tokens * 8 * ROW_BYTES) for _ in range(WORLD)]
    covered = set()
    built = []
    lane_rows = [0, 0]
    for gateway, c in enumerate(configs):
        assert (c['rank'], c['tokens'], c['chunks'], c['hidden'], c['magic']) == (gateway, tokens, 4, 256, MAGIC)
        workspace = bytearray(c['workspaceBytes'])
        records = bytearray(WORLD * c['forwardCapacity'] * 32)
        ranges, counts = [0] * (WORLD * 8), [0] * WORLD
        primary = 0
        for source, values in sorted(rows[gateway].items()):
            counts[source] = len(values)
            assert len(values) <= c['forwardCapacity']
            cursor = 0
            for chunk in range(4):
                begin = cursor
                for token, contributors in values:
                    if token // (tokens // 4) != chunk:
                        continue
                    row = payload(source, token, gateway // LOCAL)
                    direct = contributors == 1
                    assert primary < c['inputRows'] and primary < c['partialRows']
                    slab = c['inputOffset'] if direct else c['partialOffset']
                    offset = slab + primary * ROW_BYTES
                    workspace[offset:offset + ROW_BYTES] = row
                    entry = (source * c['forwardCapacity'] + cursor) * 32
                    struct.pack_into('<6i', records, entry, cursor + 1, token, primary, -1 if direct else 0, primary, 0)
                    key = (source, token, gateway // LOCAL)
                    assert key not in covered
                    covered.add(key)
                    lane_rows[cursor % 4 == 3] += 1
                    cursor += 1
                    primary += 1
                offset = (source * 4 + chunk) * 2
                ranges[offset:offset+2] = begin, cursor
            assert cursor == counts[source]
        for reducer in range(c['reducerCount']):
            struct.pack_into('<qq', workspace, c['readyOffset'] + reducer * ROW_BYTES, MAGIC, c['chunks'])
        images = runtime.runtime_images(abis[gateway], gateway, c['workspaceBytes'])
        images.update(workspace=workspace, forward=records, counts=struct.pack('<64i', *counts),
                      ranges=struct.pack('<512i', *ranges), status=native.status_image(), output=bytes(512))
        built.append(images)
    # Build expected return occupancy independently from source TopK.
    source_keys = set()
    for source, route in enumerate(routes):
        for token, experts in enumerate(route):
            for server in {expert // (EXPERTS // (WORLD // LOCAL)) for expert in experts}:
                if server != source // LOCAL:
                    offset = (token * 8 + server) * ROW_BYTES
                    expected[source][offset:offset + ROW_BYTES] = payload(source, token, server)
                    source_keys.add((source, token, server))
    assert covered == source_keys
    return built, expected, routes, route_sha, lane_rows
