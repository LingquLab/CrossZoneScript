import json,subprocess
from pathlib import Path
r={'scope':'directory names only; retained matrix assets'}
p=subprocess.run(['timeout','55','find','/mnt/share','-maxdepth','3','-type','d','(',
 '-iname','*alltoall*','-o','-iname','*prefill*','-o','-iname','*sst*','-o','-iname','*matrix*',')'],
 capture_output=True,timeout=60)
paths=[line for line in p.stdout.decode(errors='replace').splitlines() if len(line)<500]
r['share_search_exit_code']=p.returncode
r['share_candidates']=sorted(paths)[:100]
p=Path('/home/h30059441')
r['home_candidates']=sorted(str(item) for item in p.iterdir() if item.is_dir() and any(
 word in item.name.lower() for word in ['alltoall','prefill','sst','matrix']) and 'simt' not in item.name.lower())[:50] if p.is_dir() else []
base=Path('/mnt/share/h30059441/alltoall_94_66_repro')
r['retained_root_exists']=base.is_dir()
if base.is_dir():
 r['retained_directories']=sorted(p.name for p in base.iterdir() if p.is_dir())[:100]
 p=subprocess.run(['timeout','30','find',str(base),'-maxdepth','4','-type','f','(',
  '-name','scenario.json','-o','-name','build_manifest.json',')'],capture_output=True,timeout=35)
 r['retained_case_index']=sorted(p.stdout.decode(errors='replace').splitlines())[:120]
 r['retained_index_exit_code']=p.returncode
print(json.dumps(r))
