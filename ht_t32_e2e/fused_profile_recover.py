import hashlib
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")
CASE = ROOT / "t4-combine-fp32-ea1ca889-profile"
DECODER = "6d4cd3b729177646ca1c43d57971e5266a946405b6f820f3d0d914114b7ccfce"
DUMP_COUNT = 128
samples = []
observed_sectors = 0
missing_sectors = 0


def reconstruct(paths, address, size, decoder_library):
    global observed_sectors, missing_sectors
    sectors = {}
    assert address % 512 == size % 512 == 0
    for index, path in enumerate(paths):
        with path.open("rb") as stream:
            while block := stream.read(520):
                assert len(block) == 520, "Truncated profile dump"
                sector, = struct.unpack_from("<Q", block)
                die = ((sector >> 9) ^ (sector >> 16) ^ (sector >> 23)) & 1
                if die == index and address <= sector < address + size:
                    before = sectors.setdefault(sector, block[8:])
                    assert before == block[8:], "Conflicting profile sector"
    data = b"".join(sectors.get(sector, bytes(512)) for sector in range(address, address + size, 512))
    observed_sectors += len(sectors)
    missing_sectors += (size // 512) - len(sectors)
    if len(samples) < 2:
        samples.append([list(struct.unpack_from("<6Q", data, owner * 16384)) for owner in range(2)])
    return data


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recover():
    run = json.loads((CASE / "run.json").read_text())
    assert run["status"] == "sst_and_checker_passed"
    check = json.loads((CASE / "actual" / "export-report.json").read_text())
    assert check["decoder_sha256"] == DECODER
    report = {"dumps": len(list((CASE / "bin").glob("*_profile.bin")))}
    prior = (CASE / "profile-render.log").read_text(errors="replace").splitlines()
    if prior:
        classification = re.match(r"(\w+(?:Error|Exception)):", prior[-1])
        report["initial_error"] = classification.group(1) if classification else "unclassified"
        for message in ("Missing dump bytes", "No valid AIV timelines", "Conflicting dump records", "Truncated addressed-512 dump"):
            if message in prior[-1]:
                report["initial_reason"] = message
    if report["dumps"] != DUMP_COUNT:
        return dict(report, status="missing_profile_dumps")
    try:
        render(CASE, reconstruct)
    except (ValueError, OSError, AssertionError) as error:
        return dict(report, status="recovery_failed", error=type(error).__name__, headers=samples,
                    observed_sectors=observed_sectors, missing_sectors=missing_sectors)
    summary = json.loads((CASE / "profile-summary.json").read_text())
    report.update(summary, status="rendered", case=CASE.name, trace_sha256=hash_file(CASE/"perfetto_trace.json"),
                  summary_sha256=hash_file(CASE/"profile-summary.json"), missing_sectors=missing_sectors)
    return report
