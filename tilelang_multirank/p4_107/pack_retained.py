"""Emit a verified build dependency ZIP on stdout; does not modify the source host."""
from pathlib import Path
import hashlib,io,json,sys,zipfile
BASE=Path('/root/tilelang-pre-allreduce-8fb0be87-20261106-run01/tilelang-dispatch-allreduce-8fb0be87')
CAND=Path('/root/tilelang-multirank-e28d87de-20261112-run01/TileXR')
def sha(raw):return hashlib.sha256(raw).hexdigest()
assert sha(Path('/etc/machine-id').read_bytes())=='9cf64ff862ca9cfd0e61585e85ddea3320a340593c66e365d98ab05f45d9f01a'
m=json.loads((BASE/'manifest.json').read_bytes())
assert sha(json.dumps(m,sort_keys=True,separators=(',',':')).encode())=='84e55c92211a15ff62fdef49cf6540af05e3170e6d2269beb0b7493ddccdd1bc'
assert m['source_revision']=='8fb0be8781bbf2c22bda4766f037dff8133d805d'
entries={}
for name,digest in m['files'].items():
 if not name.startswith(('TileXR/','backend/')):continue
 assert not Path(name).is_absolute() and '..' not in Path(name).parts
 raw=(BASE/name).read_bytes();assert sha(raw)==digest,name
 entries[name]=raw
module={str(f.relative_to(CAND)):sha(f.read_bytes()) for f in sorted((CAND/'examples/tilelang_dispatch').rglob('*')) if f.is_file() and '__pycache__' not in f.parts}
assert len(module)==123 and sha(json.dumps(module,sort_keys=True,separators=(',',':')).encode())=='5aaeca312be1001e335aabf20c7d838d7774161146dd690ec07fb60a0a0c410e'
for name,digest in module.items():
 raw=(CAND/name).read_bytes();assert sha(raw)==digest
 entries['TileXR/'+name]=raw
assert sha(entries['backend/src/tl_templates/ascend/common.h'])=='d6355337873e5f7368c3ec7cb1f22ef0494663e133152120fee20265f03a1780'
manifest={'schema':'tilelang_retained_dependency_transfer.v1','base_revision':m['source_revision'],'base_manifest_canonical_sha256':'84e55c92211a15ff62fdef49cf6540af05e3170e6d2269beb0b7493ddccdd1bc','revision':'e28d87deb1e141e23ace5900594218ff47ab2525','candidate_files':module,'files':{k:sha(v) for k,v in sorted(entries.items())},'npu_launch':False}
entries['dependency-manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
assert len(entries)<2000 and sum(map(len,entries.values()))<40*1024*1024
out=io.BytesIO()
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for name,raw in sorted(entries.items()):z.writestr(name,raw)
sys.stdout.buffer.write(out.getvalue())
