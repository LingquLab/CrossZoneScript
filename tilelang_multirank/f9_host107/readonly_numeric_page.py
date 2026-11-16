"""Return one bounded ASCII numeric page from an already validated saved projection."""
from pathlib import Path
import hashlib
import json

EXP = Path('/root/tilelang-107-f9-host107-b520293d-20261116-run01')
REV = 'b520293d5f014fcbd4897a334d50ebf9ca9c601b'
DIRECTORIES = {256: 'capture-t256-readonly-numeric-v2-20261116-01',
               16: 'capture-t16-readonly-numeric-20261116-01',
               64: 'capture-t64-readonly-numeric-20261116-01'}


def page(tokens, index, expected_sha256):
    assert type(tokens) is int and tokens in DIRECTORIES
    assert type(index) is int and index >= 0
    assert len(expected_sha256) == 64 and all(c in '0123456789abcdef' for c in expected_sha256)
    directory = EXP / DIRECTORIES[tokens]
    raw = (directory / 'numeric.json').read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == expected_sha256, 'Saved numeric payload changed'
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest['sha256'] == digest and manifest['bytes'] == len(raw) and manifest['chunk_bytes'] == 16384
    validation = json.loads((directory / 'readonly-validation.json').read_text())
    assert validation['status'] == 'PASS' and validation['revision'] == REV
    assert validation['npu_launch'] is False and validation['read_only_existing_evidence']
    assert validation['groups_reanalyzed'] == 20 and validation['rank_captures_reanalyzed'] == 80
    assert validation['manifest'] == manifest
    numeric = json.loads(raw)
    assert numeric['schema'] == 'tilelang_107_f9_t%d_numeric.v1' % tokens
    assert numeric['tokens_per_rank'] == tokens and numeric['world_size'] == 4
    assert len(numeric['ranks']) == 8 and len(numeric['identity']) == 20
    assert {(r['variant'], r['rank']) for r in numeric['ranks']} == {(v,r) for v in ('baseline','f9') for r in range(4)}
    assert all(len(r['per_core']['cores']) == 64 and len(r['captures']) == 10 for r in numeric['ranks'])
    assert 0 <= index < len(manifest['chunks'])
    chunk = raw[index * 16384:(index + 1) * 16384]
    descriptor = manifest['chunks'][index]
    assert descriptor['index'] == index and descriptor['bytes'] == len(chunk)
    assert hashlib.sha256(chunk).hexdigest() == descriptor['sha256']
    assert all(32 <= b < 127 for b in chunk), 'Only compact ASCII numeric JSON is allowed'
    # Both files are immutable input; no source, logs, device calls, or remote writes.
    assert hashlib.sha256((directory / 'numeric.json').read_bytes()).hexdigest() == digest
    result = {'schema':'tilelang_f9_numeric_page.v1', 'read_only':True, 'npu_launch':False,
              'revision':REV, 'tokens':tokens, 'index':index, 'total_chunks':len(manifest['chunks']),
              'total_bytes':len(raw), 'total_sha256':digest, 'bytes':len(chunk),
              'sha256':descriptor['sha256'], 'text':chunk.decode('ascii')}
    encoded = json.dumps(result, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
    assert len(encoded.encode()) < 26000
    return encoded
