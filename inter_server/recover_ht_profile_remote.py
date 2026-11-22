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
        adapter = work / "profile_trace.py"
        code = (case / "adapter/profile_trace.py").read_text()
        load_stream = "        streams.extend(pack_stream(rank, owner, data) for owner in range(OWNERS))"
        single_stream = """        for owner in range(OWNERS):
            try:
                streams.append(pack_stream(rank, owner, data))
            except ValueError:
                invalid.append([rank, owner])
    (case / "invalid-streams.json").write_text(json.dumps(invalid))
    if not streams:
        raise ValueError("No complete owner streams")
"""
        assert code.count(load_stream) == 1 and code.count("    streams = []") == 1
        code = code.replace("    streams = []", "    streams = []\n    invalid = []")
        code = code.replace(load_stream, single_stream)
        before = '    if pairs[0][1] != 1 or pairs[-1][1] != 2:'
        checks = ('    if any(event not in {edge for stage in STAGES for edge in stage[2:]} for _, event in pairs):\n'
                  '        raise ValueError("Unknown profile event")\n'
                  '    if any(a[0] > b[0] for a, b in zip(pairs, pairs[1:])):\n'
                  '        raise ValueError("Timestamps misordered")\n')
        assert code.count(before) == 1
        adapter.write_text(code.replace(before, checks + before))
        invocation_env["PYTHONPATH"] = str(case / "adapter") + os.pathsep + str(case / "profile_sdk.zip")
        export = subprocess.run([sys.executable, str(adapter), str(work), "1650"],
                                env=invocation_env, capture_output=True, timeout=75)
        if export.returncode:
            errors = export.stderr.decode(errors="replace").splitlines()
            report["error_type"] = errors[-1].split(":", 1)[0] if errors else "Unknown"
            report["status"] = "records_export_failed"
            return report
        report["streams"] = int(export.stdout)
        invalid = json.loads((work / "invalid-streams.json").read_text())
        report["invalid_stream_count"] = len(invalid)
        report["invalid_stream_sample"] = invalid[:8]
        assert report["streams"] + len(invalid) == 1024
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
        output.mkdir(exist_ok=True)
        shutil.copyfile(artifact, output / artifact.name)
        shutil.copyfile(work / "invalid-streams.json", output / "invalid-streams.json")
        return dict(report, status="rendered", stages=compact(artifact), sha256=sha(artifact))
