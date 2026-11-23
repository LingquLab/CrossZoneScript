"""Build unchanged saved source profiles with the documented VF-stack switch."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

FLAG = "--cce-disable-vf-stack-reserved-ubuf"
BASELINES = {
    "scalar_pointer_runtime64": ("mte-scalar_pointer_runtime64-17", "db4e0e450e240d903c4373d66a0537df217326751d6ba474cfb3eaa616be8702"),
    "scalar_store_post_runtime_gather": ("mte-scalar_store_post_runtime_gather-24", "b9cfe94b49ffef820bb5380a189d88a44e2def774730ad0287b439c2199e1468"),
    "interleave": ("mte-interleave-22", "eaa4a8f76eab02b9ee556caec8aa2f9f903ed67c5bde7157318238ec6d47f23c"),
}
BUILDERS = tuple(BASELINES)


def source_hashes(folder):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.iterdir() if path.suffix in (".h", ".cpp")}


def commands_for(commands, prior, folder):
    assert len(commands) == 2 and FLAG not in commands[0]
    prefix = str(prior) + "/"
    rewritten = [[str(folder / value[len(prefix):]) if value.startswith(prefix) else value
                  for value in command] for command in commands]
    rewritten[0].append(FLAG)
    return rewritten


def build(folder, builder, env, log, preflight=None):
    import sst
    folder = Path(folder)
    baseline_builder = "interleave" if builder == "interleave_vf_packed" else builder
    assert folder.parent == Path("/work") and baseline_builder in BASELINES
    name, digest = BASELINES[baseline_builder]
    prior = folder.parent / name
    binary = prior / "send.o"
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == digest
    before = source_hashes(prior)
    candidate = source_hashes(folder)
    identical = before == candidate
    assert identical or builder == "interleave_vf_packed", "vf_stack_source_profile_mismatch"
    original = json.loads((prior / "build.json").read_text())
    assert original[0][0] == env["compiler"] and original[0].count("-c") == original[0].count("-o") == 1
    assert Path(original[0][original[0].index("-c") + 1]) == prior / "combine_ht.cpp"
    commands = commands_for(original, prior, folder)
    assert Path(commands[0][commands[0].index("-c") + 1]) == folder / "combine_ht.cpp"
    assert Path(commands[1][commands[1].index("-o") + 1]) == folder / "send.o"
    (folder / "build.json").write_text(json.dumps(commands, indent=2))
    if preflight is not None:
        preflight()
    for command in commands:
        subprocess.run(sst.shell_command(env, "cann", command),
                       check=True, stdout=log, stderr=subprocess.STDOUT, timeout=65)
    assert (folder / "send.o").read_bytes()[:4] == b"\x7fELF"
    assert before == source_hashes(prior) and candidate == source_hashes(folder)
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == digest
    return {"baseline_case": name, "baseline_object_sha256": digest, "source_identical": identical,
            "source_profile_sha256": hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest(),
            "compiler_flag": FLAG}


def spill_detail(prior):
    text = (prior / "build.log").read_text(errors="replace")
    match = re.search(r"The total stack object size \((\d+)\) exceeded vf stack size \((\d+)\)", text)
    assert match, "expected_local_stack_failure_missing"
    tail = text[match.end():].splitlines()
    details = []
    for line in tail[1:40]:
        value = line.strip()
        if re.search(r"PLEASE|Stack dump:|clang frontend|^#\d|^\d+\.\s", value):
            break
        if value and value != "The spilled objects are:":
            details.append(value[:300])
        if len(details) == 6:
            break
    return {"case": prior.name, "reported_stack_bytes": int(match[1]),
            "configured_stack_bytes": int(match[2]), "detail_lines": details}


def build_default(folder, env, log, preflight):
    """Restore the default stack for exactly the single-parameter case32 source."""
    import sst
    folder = Path(folder)
    assert folder == Path("/work/mte-interleave_vf_packed_default-33")
    prior = folder.parent / "mte-interleave_vf_packed-32"
    previous = json.loads((prior / "ir-check.json").read_text())
    assert (previous["identified"] and previous["source_unchanged"]
            and previous["function"]["parameter_count"] == 1
            and previous["generic_pointer_parameter_count"] == 0)
    before = source_hashes(prior)
    assert before == source_hashes(folder), "packed_default_source_profile_mismatch"
    commands = json.loads((prior / "build.json").read_text())
    assert len(commands) == 2 and commands[0][0] == env["compiler"] and commands[0].count(FLAG) == 1
    assert Path(commands[0][commands[0].index("-c") + 1]) == prior / "combine_ht.cpp"
    prefix = str(prior) + "/"
    commands = [[str(folder / value[len(prefix):]) if value.startswith(prefix) else value
                 for value in command] for command in commands]
    commands[0].remove(FLAG)
    assert all(FLAG not in command for command in commands)
    assert Path(commands[0][commands[0].index("-c") + 1]) == folder / "combine_ht.cpp"
    assert Path(commands[1][commands[1].index("-o") + 1]) == folder / "send.o"
    (folder / "build.json").write_text(json.dumps(commands, indent=2))
    preflight()
    for command in commands:
        subprocess.run(sst.shell_command(env, "cann", command),
                       check=True, stdout=log, stderr=subprocess.STDOUT, timeout=65)
    assert (folder / "send.o").read_bytes()[:4] == b"\x7fELF"
    assert before == source_hashes(prior) == source_hashes(folder)
    return {"baseline_case": prior.name, "source_identical": True,
            "source_profile_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
            "removed_compiler_flag": FLAG, "vf_stack_reservation": "compiler_default"}
