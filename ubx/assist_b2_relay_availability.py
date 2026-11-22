"""Measure peer RELAY work while a selected tail packet waits on its owner."""

import os
from pathlib import Path
from zipfile import ZipFile

from assist_b2_relay import one_tick, packet_edges, streams
from assist_offline import emit, load


def inspect(capture, trace, tail):
    rank, owner = tail["producer"]
    start, count = tail["relay_first_hop_packet"]
    packet = tail["route"][0], rank, start, count
    events = {core: packet_edges(capture[112 + core]) for core in range(32, 48)}
    flag = one_tick(events[owner], 12, packet)
    begin = one_tick(events[owner], 13, packet)
    peers = []
    for core, edges in events.items():
        if core == owner:
            continue
        submitted = [tick for tick, kind, _ in edges if kind == 18]
        drained = [tick for tick, kind, _ in edges if kind == 30]
        peers.append([core, sum(flag <= tick < begin for tick in submitted),
                      bool(drained and drained[-1] < flag), bool(drained and drained[-1] <= begin)])
    return {"round": trace["round"], "tail_owner": tail["producer"], "peers": len(peers),
            "peer_cores_submitting_during_wait": sum(row[1] > 0 for row in peers),
            "peers_already_drained_at_flag": sum(row[2] for row in peers),
            "peers_drained_at_begin": sum(row[3] for row in peers),
            "peer_submits_during_wait": sum(row[1] for row in peers)}


def main():
    folder = Path(os.environ["USERPROFILE"]) / "Downloads"
    with ZipFile(folder / "TileXR-UBX-EP32-Dispatch-assist-b2dea4e8.zip") as report, ZipFile(
        folder / "TileXR-b2dea4e8-profiling-on-184-complete-f3f4de52.zip"
    ) as first, ZipFile(folder / "TileXR-b2dea4e8-profiling-on-84-complete-f3f4de52.zip") as second:
        for bs in (64, 128):
            case = f"runs/tail-reviewed-dispatch-bs{bs}-on"
            timing = load(report, case + "/timing.json")
            for label, summary in timing["packet_diagnostics"].items():
                step = summary["round"]
                name = f"{case}/tail-report/{label}-round-{step}.json"
                source = next(archive for archive in (report, first, second) if name in archive.namelist())
                trace = load(source, name)
                tail = trace["last_copyout_on_slowest_rank"]
                if not tail["relay_first_hop_packet"]:
                    continue
                rank = tail["producer"][0]
                name = f"{case}/rank-{rank:02d}/rank-{rank}-round-{step}.records.bin"
                source = next(archive for archive in (first, second) if name in archive.namelist())
                emit(f"bs{bs}_{label}", inspect(streams(source.read(name)), trace, tail))


if __name__ == "__main__":
    main()
