"""Verify retained TileXR artifacts, install an immutable release, and activate its symlink.

Only ELF inspection and dynamic link checks run; no TileXR executable is launched.
"""

import base64
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import uuid


ARTIFACT_PATHS = {
    "install/lib/libtile-comm.so": "lib/libtile-comm.so",
    "ccu-build/ccu_tilexr_direct_smoke_probe": "bin/ccu_tilexr_direct_smoke_probe",
    "ccu-build/ccu_tilexr_basic_info_probe": "bin/ccu_tilexr_basic_info_probe",
    "ccu-build/libtilexr_ccu_aiv_fanout_kernel.so": "lib/libtilexr_ccu_aiv_fanout_kernel.so",
    "ccu-build/tilexr/kernels/ccu_tilexr_aiv_fanout_kernel.o": "lib/tilexr/kernels/ccu_tilexr_aiv_fanout_kernel.o",
}


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root):
    hashes = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "release_contains_symlink")
        if path.is_file() and path != root / "deployment.json":
            hashes[str(path.relative_to(root))] = digest(path)
        else:
            require(path.is_dir() or path == root / "deployment.json", "unexpected_release_entry")
    return hashes


def inspect_elf(path, environment):
    require(path.read_bytes()[:4] == b"\x7fELF", "invalid_elf")
    subprocess.run(["readelf", "-h", str(path)], check=True, capture_output=True, timeout=15)
    if path.suffix == ".o":
        return
    linked = subprocess.run(["ldd", "-r", str(path)], check=True, capture_output=True,
                            text=True, timeout=25, env=environment)
    text = linked.stdout + linked.stderr
    require("not found" not in text and "undefined symbol:" not in text, "unresolved_dependency")
    dynamic = subprocess.run(["readelf", "-d", str(path)], check=True, capture_output=True,
                             text=True, timeout=15).stdout
    require(not any("devlib" in line for line in dynamic.splitlines()
                    if "RPATH" in line or "RUNPATH" in line), "driver_stub_rpath")
    if path.name == "libtile-comm.so":
        needed = "\n".join(line.lower() for line in dynamic.splitlines() if "NEEDED" in line)
        require(not any(name in needed for name in ("hccl", "hcomm", "shmem")), "forbidden_core_dependency")


def verify_release(release, args, artifact_paths):
    manifest = json.loads((release / "deployment.json").read_text())
    require(manifest["revision"] == args["revision"] and manifest["pr"] == args["pr"],
            "existing_release_revision_mismatch")
    require(manifest["source_archive_sha256"] == args["source_archive_sha256"], "source_archive_mismatch")
    require(manifest["files"] == tree_hashes(release), "release_manifest_mismatch")
    require((release / "include/tilexr_api.h").is_file(), "public_headers_missing")
    require((release / "REVISION").read_text().strip() == args["revision"], "release_revision_marker_mismatch")
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = str(release / "lib") + ":" + environment.get("LD_LIBRARY_PATH", "")
    for source, destination in artifact_paths.items():
        require(digest(release / destination) == args["expected_artifacts"][source], "release_artifact_hash_mismatch")
        inspect_elf(release / destination, environment)
    # Source the shipped environment in a clean child, without running device code.
    check = subprocess.run(
        ["bash", "-c", 'source "$1" >/dev/null && test "$TILEXR_INSTALL" = "$2" && '
         'test "${PATH%%:*}" = "$2/bin" && test "${LD_LIBRARY_PATH%%:*}" = "$2/lib"',
         "tilexr-env-check", str(release / "set_env.sh"), str(release)],
        capture_output=True, timeout=20,
    )
    require(check.returncode == 0, "deployment_environment_invalid")
    return manifest


def deploy(args):
    revision = args["revision"]
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "invalid_revision")
    require(isinstance(args["pr"], int) and args["pr"] > 0, "invalid_pr")
    session = str(uuid.UUID(args["session"]))
    require(os.geteuid() == 0 and platform.machine() == "aarch64", "unexpected_remote_identity")
    root = Path.home() / "works/code/ai/crosszone"
    build = root / ("TileXR-pr%d-%s-%s" % (args["pr"], session[:8], revision[:8]))
    require(build.is_dir() and build.resolve().parent == root.resolve(), "build_directory_unavailable")
    require((build / "REVISION").read_text().strip() == revision, "build_revision_mismatch")
    require((build / "SOURCE_ARCHIVE_SHA256").read_text().strip() == args["source_archive_sha256"],
            "build_source_archive_mismatch")
    core = json.loads((build / "core-build-result.json").read_text())
    ccu = json.loads((build / "ccu-build-result.json").read_text())
    require(core["status"] == ccu["status"] == "PASS" and core["revision"] == ccu["revision"] == revision,
            "retained_build_not_passing")
    require(core["source_archive_sha256"] == args["source_archive_sha256"], "build_provenance_mismatch")
    artifact_paths = ARTIFACT_PATHS
    require(set(args["expected_artifacts"]) == set(artifact_paths), "unexpected_artifact_set")
    recorded = {item["path"]: item["sha256"] for item in ccu["artifacts"]}
    for source in artifact_paths:
        require(recorded[source] == args["expected_artifacts"][source] == digest(build / source),
                "retained_artifact_hash_mismatch")

    base = root / "TileXR-deploy"
    require(not base.is_symlink(), "deployment_base_is_symlink")
    base.mkdir(exist_ok=True)
    releases = base / "releases"
    require(not releases.is_symlink(), "release_parent_is_symlink")
    releases.mkdir(exist_ok=True)
    with (base / ".deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = base / "current"
        require(not current.exists() or current.is_symlink(), "current_is_not_symlink")
        previous = os.readlink(str(current)) if current.is_symlink() else None
        if current.is_symlink():
            require(current.resolve().parent == releases.resolve(), "current_points_outside_release_parent")
        release = releases / ("pr%d-%s" % (args["pr"], revision[:12]))
        require(not release.is_symlink(), "release_is_symlink")
        reused = release.exists()
        if not reused:
            stage = Path(tempfile.mkdtemp(prefix=".staging-pr%d-" % args["pr"], dir=str(releases)))
            # Preserve the installed public headers and their relative layout.
            shutil.copytree(str(build / "install"), str(stage), dirs_exist_ok=True, symlinks=True)
            for source, destination in artifact_paths.items():
                output = stage / destination
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(build / source), str(output))
            (stage / "REVISION").write_text(revision + "\n")
            (stage / "set_env.sh").write_text(
                '#!/usr/bin/env bash\n'
                '# Select this TileXR release and its installed CANN dependencies.\n'
                'source /usr/local/Ascend/cann-9.1.0/set_env.sh || return $?\n'
                'export TILEXR_INSTALL="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"\n'
                'export TILEXR_CCU_AIV_FANOUT_KERNEL_SO="$TILEXR_INSTALL/lib/libtilexr_ccu_aiv_fanout_kernel.so"\n'
                'export PATH="$TILEXR_INSTALL/bin:$PATH"\n'
                'export LD_LIBRARY_PATH="$TILEXR_INSTALL/lib:/usr/local/Ascend/driver/lib64/driver:'
                '/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64:'
                '/usr/local/Ascend/cann-9.1.0/aarch64-linux/lib64:${LD_LIBRARY_PATH:-}"\n'
            )
            manifest = {"pr": args["pr"], "revision": revision,
                        "source_archive_sha256": args["source_archive_sha256"],
                        "source_directory": str(build / "source"), "build_directory": str(build),
                        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "npu_tests_run": False, "files": tree_hashes(stage)}
            (stage / "deployment.json").write_text(json.dumps(manifest, indent=2) + "\n")
            verify_release(stage, args, artifact_paths)
            os.rename(str(stage), str(release))
        manifest = verify_release(release, args, artifact_paths)
        temporary_link = base / (".current-" + uuid.uuid4().hex)
        temporary_link.symlink_to(release.relative_to(base), target_is_directory=True)
        os.replace(str(temporary_link), str(current))
        require(current.resolve() == release.resolve(), "current_activation_failed")
        record = {"status": "PASS", "revision": revision, "pr": args["pr"],
                  "source_directory": str(build / "source"), "build_directory": str(build),
                  "deployment_directory": str(release), "current": str(current),
                  "environment_script": str(current / "set_env.sh"), "previous_current": previous,
                  "reused_existing_release": reused, "current_verified": True,
                  "artifact_hashes_verified": len(artifact_paths), "installed_files": len(manifest["files"]),
                  "elf_and_link_dependencies": "PASS", "environment_check": "PASS", "npu_tests_run": False}
        (base / "last-deployment.json").write_text(json.dumps(record, indent=2) + "\n")
        return record


def verify_as_owner(base, release, uid, gid):
    script = (
        'set -e\n'
        'source "$1/current/set_env.sh" >/dev/null\n'
        'test "$TILEXR_INSTALL" = "$2"\n'
        'test -r "$TILEXR_CCU_AIV_FANOUT_KERNEL_SO"\n'
        'test "${PATH%%:*}" = "$2/bin"\n'
        'test "${LD_LIBRARY_PATH%%:*}" = "$2/lib"\n'
        'test "$(id -u)" = "$3"\n'
        'test "$(id -g)" = "$4"\n'
        'shift 4\n'
        'for relative in "$@"; do\n'
        '  dependencies=$(ldd -r "$TILEXR_INSTALL/$relative" 2>&1)\n'
        '  case "$dependencies" in *"not found"*|*"undefined symbol:"*) exit 21 ;; esac\n'
        'done\n'
    )
    paths = [path for path in ARTIFACT_PATHS.values() if not path.endswith(".o")]
    try:
        account = pwd.getpwuid(uid)
        groups = os.getgrouplist(account.pw_name, gid)
    except KeyError:
        groups = []
    result = subprocess.run(["bash", "--noprofile", "--norc",
                             "-c", script, "tilexr-user-check", str(base), str(release), str(uid), str(gid), *paths],
                            capture_output=True, timeout=60, cwd=str(base),
                            user=uid, group=gid, extra_groups=groups)
    require(result.returncode == 0, "user_environment_or_link_check_failed")


def relocate(args):
    """Move only the deployment tree; retained source/build paths remain provenance."""
    require(os.geteuid() == 0 and platform.machine() == "aarch64", "unexpected_remote_identity")
    require(re.fullmatch(r"[0-9a-f]{40}", args["revision"]), "invalid_revision")
    require(isinstance(args["pr"], int) and args["pr"] > 0, "invalid_pr")
    require(set(args["expected_artifacts"]) == set(ARTIFACT_PATHS), "unexpected_artifact_set")
    parent = Path(args["destination_parent"])
    require(parent == Path("/home/c00996840/ai"), "unexpected_destination_parent")
    require(parent.is_dir() and parent.resolve() == parent, "destination_parent_not_physical")
    owner = parent.stat()
    uid, gid = owner.st_uid, owner.st_gid
    try:
        owner_name = pwd.getpwuid(uid).pw_name
    except KeyError:
        owner_name = None
    old = Path("/root/works/code/ai/crosszone/TileXR-deploy")
    require(old.is_dir() and not old.is_symlink() and old.resolve() == old, "unexpected_original_deployment")
    name = "pr%d-%s" % (args["pr"], args["revision"][:12])
    old_release = old / "releases" / name
    target = parent / "TileXR-deploy"
    require(not target.exists() and not target.is_symlink(), "destination_already_exists")
    require({p.name for p in old.iterdir()} == {"releases", "current", "last-deployment.json", ".deploy.lock"},
            "original_contains_unrelated_entries")
    require(not (old / "releases").is_symlink() and not old_release.is_symlink(), "unexpected_release_symlink")
    require({p.name for p in (old / "releases").iterdir()} == {name}, "original_contains_other_releases")
    require((old / "current").is_symlink() and os.readlink(str(old / "current")) == "releases/" + name,
            "unexpected_current_link")
    require(not (old / ".deploy.lock").is_symlink() and not (old / "last-deployment.json").is_symlink(),
            "unexpected_deployment_metadata_symlink")
    with (old / ".deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original_manifest = verify_release(old_release, args, ARTIFACT_PATHS)
        previous = json.loads((old / "last-deployment.json").read_text())
        require(previous["status"] == "PASS" and previous["revision"] == args["revision"],
                "original_receipt_mismatch")
        stage = Path(tempfile.mkdtemp(prefix=".TileXR-deploy-relocate-", dir=str(parent)))
        shutil.copytree(str(old), str(stage), symlinks=True, dirs_exist_ok=True)
        release = target / "releases" / name
        record = dict(previous)
        record.update({"operation": "relocate", "deployment_directory": str(release),
                       "current": str(target / "current"), "environment_script": str(target / "current/set_env.sh"),
                       "relocated_from": str(old), "relocated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "verification_user": owner_name, "verification_uid": uid, "verification_gid": gid,
                       "destination_owner_uid": uid, "destination_owner_gid": gid,
                       "original_deployment_removed": False,
                       "current_verified": False})
        (stage / "last-deployment.json").write_text(json.dumps(record, indent=2) + "\n")
        # lchown never follows current, which must remain a release-relative link.
        for directory, directories, files in os.walk(str(stage), followlinks=False):
            os.chown(directory, uid, gid)
            for entry in directories + files:
                os.lchown(os.path.join(directory, entry), uid, gid)
        copied_manifest = verify_release(stage / "releases" / name, args, ARTIFACT_PATHS)
        require(copied_manifest == original_manifest, "relocation_changed_release_manifest")
        verify_as_owner(stage, stage / "releases" / name, uid, gid)
        os.rename(str(stage), str(target))
        require((target / "current").resolve() == release.resolve(), "relocated_current_invalid")
        verify_as_owner(target, release, uid, gid)
        require(tree_hashes(release) == original_manifest["files"], "relocated_files_changed")
        owned_paths = [target, *target.rglob("*")]
        require(all(path.lstat().st_uid == uid and path.lstat().st_gid == gid for path in owned_paths),
                "relocated_ownership_mismatch")
        # Recheck the exact source path and its scope before completing the move.
        require(old.resolve() == Path("/root/works/code/ai/crosszone/TileXR-deploy"), "original_path_changed")
        require({p.name for p in old.iterdir()} == {"releases", "current", "last-deployment.json", ".deploy.lock"},
                "original_scope_changed")
        require({p.name for p in (old / "releases").iterdir()} == {name}, "original_release_scope_changed")
        require(tree_hashes(old_release) == original_manifest["files"], "original_release_changed")
        shutil.rmtree(str(old))
        require(not old.exists(), "original_deployment_still_present")
        record.update(original_deployment_removed=True, current_verified=True,
                      ownership_matches_parent=True,
                      artifact_hashes_verified=len(ARTIFACT_PATHS), installed_files=len(original_manifest["files"]),
                      elf_and_link_dependencies="PASS", environment_check="PASS", npu_tests_run=False)
        receipt = target / "last-deployment.json"
        receipt.write_text(json.dumps(record, indent=2) + "\n")
        require(receipt.stat().st_uid == uid and receipt.stat().st_gid == gid, "receipt_owner_mismatch")
        return record


if __name__ == "__main__":
    try:
        settings = json.loads(base64.urlsafe_b64decode(sys.argv[1]).decode("utf-8"))
        result = relocate(settings) if settings.get("operation") == "relocate" else deploy(settings)
        print("CROSSZONE_RESULT " + json.dumps(result))
    except Exception as error:
        # The orchestrator retains this output locally even on a nonzero SSH exit.
        traceback.print_exc()
        print("CROSSZONE_RESULT " + json.dumps({"status": "FAIL", "error_type": type(error).__name__,
                                               "reason": str(error)[:200], "npu_tests_run": False}))
        sys.exit(1)
