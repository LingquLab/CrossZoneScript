"""Find the slowest replicate4 rank by per-AIV core_total."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import PurePosixPath
import shlex
import tarfile

from benchmark import HOSTS, ssh
from retest_disjoint_routes import REVISION, WORK


def read_host(index):
    host = HOSTS[index]
    paths = {f"rank{rank}/core-total-records.json": rank
             for rank in range(index * 16, (index + 1) * 16)}
    output = str(PurePosixPath(WORK) / "full-bs32-f50")
    command = shlex.join(["tar", "-cz", "-C", output, *paths])
    data = ssh(host, command, timeout=90, stage="Read replicate4 AIV timings")
    observations = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        assert sorted(archive.getnames()) == sorted(paths)
        for filename, rank in paths.items():
            rows = json.load(archive.extractfile(filename))
            assert len(rows) == 60
            for number, row in enumerate(rows[10:], start=10):
                assert (row["rank"], row["round"], row["bs"], row["revision"]) == (rank, number, 32, REVISION)
                assert row["tick_hz"] == 1000000000 and not row["warmup"]
                assert [core["core"] for core in row["cores"]] == list(range(56))
                observations.extend((core["end"] - core["start"], host, rank, number, core["core"])
                                    for core in row["cores"])
    return observations


def slowest(samples):
    maximum = max(row[0] for row in samples)
    return [dict(host=host, rank=rank, round=number, aiv=aiv, core_total_us=ticks / 1000)
            for ticks, host, rank, number, aiv in samples if ticks == maximum]


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = [row for host_rows in pool.map(read_host, range(2)) for row in host_rows]
    assert len(rows) == 89600
    print(json.dumps(dict(revision=REVISION, bs=32, samples=len(rows),
                          last_round=slowest([row for row in rows if row[3] == 59]),
                          all_formal_rounds=slowest(rows)), separators=(",", ":")))
