"""Diagnose and render the existing profile data without launching SST."""
from collections import Counter, defaultdict
from statistics import median
import tempfile


def compact(result):
    traces = defaultdict(lambda: defaultdict(float))
    counts = Counter()
    for event in json.loads(result.read_text())["traceEvents"]:
        if event.get("ph") == "X":
            name = event["name"]
            traces[name][event["pid"], event["tid"]] += event["dur"]
            counts[name] += 1
    return {
        name: {"slices": counts[name], "tracks": len(values),
               "min_us": round(min(values.values()), 3),
               "median_us": round(median(values.values()), 3),
               "max_us": round(max(values.values()), 3)}
        for name, values in traces.items()
    }


def recover():
    case = MOUNT / "t4-sst-profile-94e104f0"
    report = {"status": "failed", "raw_dump_files": len(list((case / "bin").glob("*_profile.bin")))}
    if (case / "profile-report/perfetto_trace.json").is_file():
        return dict(report, status="rendered", stages=compact(case / "profile-report/perfetto_trace.json"))
    assert json.loads((case / "run.json").read_text())["status"] == "sst_and_checker_passed"
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        env = json.loads((case / "sst-environment.json").read_text())
        decoder = work / "decoder.so"
        docker("cp", NAME + ":" + env["decoder_library"], str(decoder), timeout=30)
        env["decoder_library"] = str(decoder)
        write_json(work / "sst-environment.json", env)
        (work / "bin").symlink_to(case / "bin", target_is_directory=True)
        shutil.copyfile(case / "workload.yml", work / "workload.yml")
        import os
        invocation_env = dict(os.environ, PYTHONPATH=str(case / "profile_sdk.zip"))
        export = subprocess.run([sys.executable, str(case / "adapter/profile_trace.py"), str(work), "1650"],
                                env=invocation_env, capture_output=True, timeout=75)
        if export.returncode:
            errors = export.stderr.decode(errors="replace").splitlines()
            message = errors[-1] if errors else ""
            match = re.fullmatch(r"ValueError: Incomplete profile stream rank=(\d+) owner=(\d+): count=(\d+), lost=(\d+), state=(\d+)", message)
            report["error_type"] = message.split(":", 1)[0]
            if match:
                report["first_stream"] = [int(value) for value in match.groups()]
            report["decoder_sha256"] = sha(decoder)
            report["missing_dump_bytes"] = "Missing dump bytes" in message
            report["status"] = "records_export_failed"
            return report
        report["streams"] = int(export.stdout)
        render = subprocess.run([sys.executable, "-m", "tilexr_profiling", "--mapping",
                                 str(work / "profile.mapping.json"), "--records",
                                 str(work / "profile.records.bin"), "--output-dir",
                                 str(work / "profile-report"), "--artifacts", "perfetto", "json"],
                                env=invocation_env, capture_output=True, timeout=45)
        if render.returncode:
            report["status"] = "render_failed"
            report["render_exit_code"] = render.returncode
            return report
        artifact = work / "profile-report/perfetto_trace.json"
        if not artifact.is_file():
            report["status"] = "trace_path_missing"
            report["rendered_json_files"] = len(list((work / "profile-report").glob("*.json")))
            return report
        output = case / "profile-recovered"
        output.mkdir(exist_ok=False)
        shutil.copyfile(artifact, output / artifact.name)
        return dict(report, status="rendered", stages=compact(artifact), sha256=sha(artifact))
