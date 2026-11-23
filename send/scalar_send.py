"""Run full scalar-WQE Send, then Reduce from verified Send output in CPU SST."""
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from vf_inspect import host

BASELINE_CASE = "mte-scalar_control-21"
BASELINE_SHA = "5d680c2b519779723913912adf7ee7d17aa6c98d65c2414c655045fe91a5467d"


def packet(value):
    out = {key: value[key] for key in (
        "ok", "phase", "report", "builder", "baseline_case", "baseline_object_sha256",
        "scalar_builder_unchanged", "source_changes", "build_completed", "object_sha256",
        "raw_object_bytes", "object_bytes", "route_sha256", "send_rows", "backward_record_bytes",
        "send_verified", "send_verified_ranks", "reduce_verified", "checked_status_words",
        "nonzero_status_words", "status_guard_failures", "first_nonzero_status",
        "queue_state_outputs", "error", "failure", "compile_errors") if key in value}
    out["stages"] = [{key: stage[key] for key in (
        "stage", "returncode", "wall_s", "completed", "simulated_us", "verified_ranks",
        "input_origin", "numeric_validation", "failure", "fatal") if key in stage}
        for stage in value.get("stages", [])]
    return out


def inside_container():
    import trial
    import sst
    from backward_alignment import repack
    root, folder = Path("/work"), Path("/work/scalar-send-34")
    folder.mkdir()
    out = {"ok": False, "phase": "preparing", "report": str(folder), "builder": "scalar_control",
           "baseline_case": BASELINE_CASE, "baseline_object_sha256": BASELINE_SHA,
           "backward_record_bytes": 32, "send_verified": False, "reduce_verified": False, "stages": []}

    def save():
        sst.write_json(folder / "result.json", out)
        sst.write_json(root / "scalar-send-result.json", out)

    try:
        save()
        snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
        sst.verify_environment(snapshot)
        baseline = root / BASELINE_CASE
        assert sst.checksum(baseline / "send.o") == BASELINE_SHA
        prior = json.loads((baseline / "trial-result.json").read_text())
        assert (prior["ok"] and prior["wqe_verified"] and prior["checked_wqes"] == 405
                and prior["checked_status_words"] == 64 and prior["status_mismatch_count"] == 0)
        out["source_changes"] = trial.patch(folder, builder="scalar_control", mte_status=True,
                                            backward_record_bytes=32, reference_compat=True)
        out["scalar_builder_unchanged"] = (folder / "scalar_wqe_control.h").read_bytes() == (baseline / "scalar_wqe_control.h").read_bytes()
        assert out["scalar_builder_unchanged"]
        header_path = folder / "combine_ht_kernel.h"
        header = header_path.read_text()
        marker = "sendStatus == 0U ? (0xa5000000U | (args.config.rank << 8U) | core) : sendStatus"
        assert header.count(marker) == 1
        header = header.replace(marker, "sendStatus")
        assert header.count("TileXR::UDMAPublishWqe(wqes, sq, head, count);") == 1
        assert header.count("TileXR::UDMARingDoorbell(head, sq);") == 1
        assert header.count("return TileXR::SimulatorPollCQUntilSqTail(info, peer, qp, head, *this, stateRead, stateWrite);") == 1
        assert "Diagnostic: snapshot complete" not in header and "No CQ wait" not in header
        header_path.write_text(header)
        out["source_changes"].update(sq_publication_enabled=True, native_doorbell_enabled=True,
                                     cq_poll_enabled=True, status_encoding="plain_result", status_slot_bytes=32)
        out["phase"] = "build"
        save()
        binary = folder / "send.o"
        with (folder / "build.log").open("wb") as log:
            sst.build_kernel(snapshot["settings"], log, source=folder / "combine_ht.cpp", binary=binary)
        symbols = subprocess.run(["readelf", "-sW", str(binary)], check=True,
                                 capture_output=True, text=True, timeout=15).stdout
        assert not any("BuildPutWqes" in line and ".vector.thread" in line for line in symbols.splitlines())
        assert "ProbeOperation" not in symbols
        raw = binary.read_bytes()
        assert 0 < len(raw) < 10485760
        out["raw_object_bytes"] = len(raw)
        binary.write_bytes(raw.ljust(10485760, b"\0"))
        out.update(build_completed=True, object_sha256=sst.checksum(binary), object_bytes=binary.stat().st_size)
        specs, routes = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
        prepared, _ = repack([spec.serialize() for spec in specs], 32)
        out["route_sha256"] = hashlib.sha256(json.dumps(routes, separators=(",", ":")).encode()).hexdigest()
        assert out["route_sha256"] == "f5eb786c6ca0a725607b16caef0ac8a8c0efc161576b185d9c74ffa2f4127881"
        out["send_rows"] = sum(len(spec.gather_rows) for spec in specs)
        assert out["send_rows"] == 260
        sst.KERNEL = binary
        run = folder / "send-only"
        workload = sst.create_workload(run, specs, prepared, 1)
        out["queue_state_outputs"] = 0
        for rank in range(8):
            chip = workload["workload_config"][f"chip{rank}"]
            (run / "input_bin" / f"rank{rank}" / "status.bin").write_bytes(trial.status_image(32))
            loaded = chip["kernel_param"]["load_queue_state"]
            image = Path(loaded["input_bin"])
            assert 0 < image.stat().st_size < 16 * 1024 * 1024
            chip["output_param"]["queue_state"] = dict(addr=loaded["value"], size=image.stat().st_size)
            out["queue_state_outputs"] += 1
        with (run / "model.log").open("w") as log, redirect_stdout(log):
            sst.write_model(run, snapshot, workload, 8)
        out["phase"] = "send"
        save()
        started = time.monotonic()
        env = snapshot["settings"]
        with (run / "sim.log").open("wb") as log:
            try:
                process = subprocess.run(sst.shell_command(env, "sst", [env["sst_bin"], "-n", "16", run / "test_UBusSim.py"]),
                                         cwd=run, stdout=log, stderr=subprocess.STDOUT, timeout=240)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
        with (run / "sim.log").open("rb") as stream:
            stream.seek(max(0, (run / "sim.log").stat().st_size - 2**20))
            tail = stream.read().decode(errors="replace")
        finish = [float(match[1]) * sst.TIME_FACTORS[match[2]] for match in sst.SIM_TIME.finditer(tail)]
        fatal = next((line[line.index("FATAL:"):][:450] for line in tail.splitlines() if "FATAL:" in line), None)
        stage = dict(stage="send-only", returncode=code, wall_s=time.monotonic() - started,
                     completed=code == 0 and fatal is None and "Traceback (most recent call last)" not in tail and bool(finish) and finish[-1] > 0)
        if finish:
            stage["simulated_us"] = finish[-1]
        if fatal:
            stage["fatal"] = fatal
        out["stages"].append(stage)
        save()
        assert stage["completed"], "scalar Send SST did not complete"
        out.update(checked_status_words=0, nonzero_status_words=0, status_guard_failures=0, first_nonzero_status=[])
        for rank in range(8):
            status = workload["workload_config"][f"chip{rank}"]["output_param"]["status"]
            words, guards = trial.status_output(sst.read_output(run, rank, "status", status["addr"], status["size"]), 32)
            out["checked_status_words"] += len(words)
            out["nonzero_status_words"] += sum(word != 0 for word in words)
            out["status_guard_failures"] += guards
            out["first_nonzero_status"].extend(dict(rank=rank, core=core, value=word)
                for core, word in enumerate(words) if word and len(out["first_nonzero_status"]) < 3)
        save()
        state = sst.validate_send(run, workload, prepared)
        assert len(state) == 8 and out["checked_status_words"] == 64 and out["nonzero_status_words"] == out["status_guard_failures"] == 0
        out.update(send_verified=True, send_verified_ranks=8, phase="reduce")
        stage["verified_ranks"] = 8
        save()
        sst.run_phase(folder / "reduce-only", 2, specs, prepared, snapshot,
                      {"ranks": 8, "threads": 16, "stage_timeout": 240}, out["stages"], send_state=state)
        out.update(ok=True, reduce_verified=True, phase="complete")
    except Exception as error:
        out.update(error=type(error).__name__, failure=str(error)[:250])
        log = folder / "build.log"
        if not out.get("build_completed") and log.is_file():
            out["compile_errors"] = [line[:250] for line in log.read_text(errors="replace").splitlines() if "error:" in line][:2]
    save()
    print(json.dumps(packet(out), separators=(",", ":")))
    return out["ok"]


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--container":
            sys.exit(0 if inside_container() else 1)
        args = json.loads(sys.argv[1])
        if args["action"] == "scalar-send-result":
            assert socket.gethostname().split(".")[0] == args["hostname"] == "host-94-26"
            value = json.loads((Path(__file__).resolve().parent / "scalar-send-result.json").read_text())
            print(json.dumps(packet(value), separators=(",", ":")))
        else:
            host(args, entry="scalar_send.py", directory="scalar-send-34", timeout=820)
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
