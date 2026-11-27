"""Read retained Cast regression evidence from the green simulator host."""
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "alltoall"))
from ssh_transport import run

options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
if options.get("verify_local"):
    patch = Path("D:/project/仿真配置/修正cast问题")
    expected = dict(line.split(maxsplit=1)[::-1] for line in (HERE / "fix-case-sha256.txt").read_text().splitlines())
    reports = []
    for manifest in patch.rglob("cases.json"):
        folder = manifest.parent
        if not (folder / "cases").is_dir():
            continue
        missing = [name for name in expected if not (folder / name).is_file()]
        differences = [name for name, sha in expected.items() if (folder / name).is_file() and
                       hashlib.sha256((folder / name).read_bytes()).hexdigest() != sha]
        reports.append(dict(folder=folder.relative_to(patch).as_posix(), matched=len(expected)-len(missing)-len(differences),
                            missing=missing, differences=differences))
    print(json.dumps(reports, ensure_ascii=True, separators=(",", ":")))
    raise SystemExit(0)
source = r"""
from pathlib import Path
import json,re,subprocess,sys

patch = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/cast-v310-repair-0784b139')
options=json.load(sys.stdin)
run_name=options.get('run_name','regression')
assert re.fullmatch(r'regression(?:-[a-z0-9]+)?',run_name)
suite=patch/run_name
summary=dict(exists=suite.is_dir())
if (suite/'image.json').is_file():
    image=json.loads((suite/'image.json').read_text())
    summary.update(image=image['image'],tag=image['tag'],installed=len(image['paths']))
if (suite/'failure.log').is_file():
    summary['failure']=(suite/'failure.log').read_text().splitlines()[-1][:180]
reports = {}
for p in sorted(suite.glob('*/*/result.json')):
    key=str(p.parent.relative_to(suite))
    row=json.loads(p.read_bytes())
    if options.get('full'):
        reports[key]=row
    else:
        reports[key]={k:row[k] for k in ('sst_exit','check_exit','error','setup_exit') if k in row}
        for candidate in ('setup.log','check.log','failure.log'):
            log=p.parent/candidate
            if log.is_file() and log.stat().st_size and (row.get('check_exit',0) or row.get('setup_exit',0) or row.get('error')):
                lines=log.read_text(errors='replace').splitlines()
                if lines:
                    reports[key][candidate]=lines[-1][:180]
summary['cases']=reports
if options.get('image_tags'):
    result=subprocess.run(['docker','image','inspect','sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5',
                            '--format','{{json .RepoTags}}'],capture_output=True,timeout=15)
    summary['base_tags']=json.loads(result.stdout) if result.returncode==0 else []
print(json.dumps(summary,separators=(',',':')))
"""
print(json.dumps(run(source, data=options, password=True, timeout=75), separators=(",", ":")))
