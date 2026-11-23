"""Build unchanged saved source profiles with the documented VF-stack switch."""
import hashlib
import json
from pathlib import Path
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


def build(folder, builder, env, log):
    import sst
    folder = Path(folder)
    assert folder.parent == Path("/work") and builder in BASELINES
    name, digest = BASELINES[builder]
    prior = folder.parent / name
    binary = prior / "send.o"
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == digest
    before = source_hashes(prior)
    assert before == source_hashes(folder), "vf_stack_source_profile_mismatch"
    original = json.loads((prior / "build.json").read_text())
    assert original[0][0] == env["compiler"] and original[0].count("-c") == original[0].count("-o") == 1
    assert Path(original[0][original[0].index("-c") + 1]) == prior / "combine_ht.cpp"
    commands = commands_for(original, prior, folder)
    assert Path(commands[0][commands[0].index("-c") + 1]) == folder / "combine_ht.cpp"
    assert Path(commands[1][commands[1].index("-o") + 1]) == folder / "send.o"
    (folder / "build.json").write_text(json.dumps(commands, indent=2))
    for command in commands:
        subprocess.run(sst.shell_command(env, "cann", command),
                       check=True, stdout=log, stderr=subprocess.STDOUT, timeout=65)
    assert (folder / "send.o").read_bytes()[:4] == b"\x7fELF"
    assert before == source_hashes(prior) == source_hashes(folder)
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == digest
    return {"baseline_case": name, "baseline_object_sha256": digest, "source_identical": True,
            "source_profile_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
            "compiler_flag": FLAG}
