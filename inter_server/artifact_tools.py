#!/usr/bin/env python3
# Copyright (c) 2026 TileXR Project.
"""Package a blue-built AICore object and verify it before green staging.

Packing records the source commit and build evidence. Unpacking uses only Python;
it never invokes a compiler or changes the candidate object.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess


ENTRY = 'tilexr_ep_ht_inter_server_send_kernel'
SCHEMA = 'tilexr.inter_server.sst.artifact.v1'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pack(binary, candidate_commit, build_record, output):
    binary, output = Path(binary), Path(output)
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', candidate_commit):
        raise ValueError('A fixed candidate commit is required')
    raw = binary.read_bytes()
    if not raw.startswith(b'\x7fELF'):
        raise ValueError('Expected an ELF AICore object')
    symbols = subprocess.check_output(['readelf', '--wide', '--syms', str(binary)], text=True, timeout=30)
    if not any(len(parts) >= 8 and parts[-1] == ENTRY and parts[-2] != 'UND'
               for parts in (line.split() for line in symbols.splitlines())):
        raise ValueError('Native entry is not defined in this object')
    record_bytes = Path(build_record).read_bytes()
    record = json.loads(record_bytes)
    if record.get('candidate_commit', candidate_commit) != candidate_commit:
        raise ValueError('Build record names another candidate')
    output.mkdir(parents=True, exist_ok=False)
    compressed = gzip.compress(raw, mtime=0)
    (output / 'inter_server_send.o.gz').write_bytes(compressed)
    (output / 'build-record.json').write_bytes(record_bytes)
    manifest = dict(schema=SCHEMA, candidate_commit=candidate_commit, entry=ENTRY,
        binary_file='inter_server_send.o', compressed_file='inter_server_send.o.gz',
        binary_bytes=len(raw), binary_sha256=digest(raw), compressed_bytes=len(compressed),
        compressed_sha256=digest(compressed), build_record_sha256=digest(record_bytes),
        qualification='blue object packaged; not SST validated')
    (output / 'artifact.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def unpack(package, output):
    package, output = Path(package), Path(output)
    manifest = json.loads((package / 'artifact.json').read_text())
    if manifest['schema'] != SCHEMA or manifest['entry'] != ENTRY:
        raise ValueError('Unexpected native artifact schema or entry')
    if manifest['compressed_file'] != 'inter_server_send.o.gz' or manifest['binary_file'] != 'inter_server_send.o':
        raise ValueError('Unexpected artifact filenames')
    compressed = (package / manifest['compressed_file']).read_bytes()
    record = (package / 'build-record.json').read_bytes()
    if len(compressed) != manifest['compressed_bytes'] or digest(compressed) != manifest['compressed_sha256']:
        raise ValueError('Compressed object changed')
    if digest(record) != manifest['build_record_sha256']:
        raise ValueError('Build record changed')
    size = manifest['binary_bytes']
    if not isinstance(size, int) or size <= 0:
        raise ValueError('Invalid uncompressed object size')
    with gzip.open(package / manifest['compressed_file'], 'rb') as stream:
        raw = stream.read(size + 1)
    if len(raw) != size or digest(raw) != manifest['binary_sha256'] or not raw.startswith(b'\x7fELF'):
        raise ValueError('Uncompressed object changed')
    output.mkdir(parents=True, exist_ok=False)
    (output / manifest['binary_file']).write_bytes(raw)
    (output / 'artifact.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (output / 'build-record.json').write_bytes(record)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    pack_parser = commands.add_parser('pack')
    pack_parser.add_argument('--binary', type=Path, required=True)
    pack_parser.add_argument('--candidate-commit', required=True)
    pack_parser.add_argument('--build-record', type=Path, required=True)
    pack_parser.add_argument('--output', type=Path, required=True)
    unpack_parser = commands.add_parser('unpack')
    unpack_parser.add_argument('--package', type=Path, required=True)
    unpack_parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = (pack(args.binary, args.candidate_commit, args.build_record, args.output) if args.action == 'pack'
              else unpack(args.package, args.output))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
