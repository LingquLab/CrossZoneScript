# Copyright (c) 2026 TileXR Project.
"""Move inactive generated SST cases out of their working tree on the simulator host."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path('/mnt/share/tilexr-sim-jobs')
ORIGIN = ROOT / 'b4f4660a-6144-453c-99f7-339b40f56c54'
ARCHIVE = ROOT / '.archived-sim-cases' / '033634e3-7471-4660-b1c3-784cea442874'
CUTOFF = datetime.datetime(2026, 11, 24, tzinfo=datetime.timezone.utc).timestamp()
CASE = re.compile(r'(?:combine-memory-cast-c266-|final-reduce-|ht-t[0-9]+-reduce-)[a-z0-9-]+')
TEMPLATE = ORIGIN / 'inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549/case-manifest.json'


def active_paths():
    ids = subprocess.check_output(['docker', 'ps', '-q'], text=True, timeout=10).split()
    if ids:
        containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids], timeout=12))
        for container in containers:
            for mount in container.get('Mounts', []):
                if mount.get('Type') == 'bind':
                    source = Path(mount['Source']).resolve()
                    if ORIGIN in source.parents:
                        yield source
    # A general mount such as /mnt can contain unrelated work. Check live
    # processes for concrete references into the old case directories.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for path in (proc / 'cwd', proc / 'exe', *list((proc / 'fd').iterdir())):
                try:
                    target = os.readlink(path)
                except OSError:
                    continue
                if target.startswith(str(ORIGIN) + '/'):
                    yield Path(target[:-10] if target.endswith(' (deleted)') else target)
        except OSError:
            continue


def unchanged_since_cutoff(case):
    for directory, dirs, files in os.walk(case):
        if any((Path(directory) / name).lstat().st_mtime >= CUTOFF for name in dirs + files):
            return False
    return case.stat().st_mtime < CUTOFF


def archive(names):
    assert os.uname().nodename.split('.')[0] == 'host-94-26'
    assert ROOT.resolve() == ROOT and ORIGIN.resolve() == ORIGIN and TEMPLATE.is_file()
    assert len(names) == len(set(names)) and all(CASE.fullmatch(name) for name in names)
    candidates = []
    skipped = {}
    active = set(active_paths())
    for name in names:
        path = ORIGIN / name
        if not path.is_dir() or path.is_symlink() or not unchanged_since_cutoff(path):
            skipped[name] = 'missing_or_updated'
        elif any(path == other or path in other.parents or other in path.parents for other in active):
            skipped[name] = 'active'
        else:
            candidates.append(path)
    ARCHIVE.mkdir(parents=True, exist_ok=False)
    manifest = ARCHIVE / 'moves.jsonl'
    with manifest.open('x') as stream:
        for path in candidates:
            target = ARCHIVE / path.name
            path.rename(target)
            stream.write(json.dumps({'source': str(path), 'archive': str(target)}) + '\n')
            stream.flush()
            assert target.is_dir() and not path.exists()
    assert TEMPLATE.is_file()
    print(json.dumps({'moved': [path.name for path in candidates], 'skipped': skipped,
                      'manifest': str(manifest)}, separators=(',', ':')))
