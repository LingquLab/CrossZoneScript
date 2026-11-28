"""Read only this session's processes, latest run artifacts and bounded logs."""
import json
import os
import pathlib

session='8da52234-c88f-451d-95a1-fa33a978690b'
root=pathlib.Path.home()/'work/pangu_validation/ma-0'/session
runs=sorted(root.glob('gqa-smoke-*'))
result={'read_only':True,'running_task_processes':[]}
for entry in pathlib.Path('/proc').iterdir():
    if not entry.name.isdigit(): continue
    try:
        if entry.stat().st_uid != os.getuid(): continue
        raw=(entry/'cmdline').read_bytes().decode(errors='replace').split('\0')
        if not any(session in item or '/work/pangu_sophon_pytorch/ma-0/' in item for item in raw): continue
        stat=(entry/'stat').read_text().rsplit(')',1)[1].split()
        result['running_task_processes'].append({'pid':int(entry.name),'state':stat[0],'ppid':int(stat[1]),'pgid':int(stat[2]),'command_prefix':raw[:3]})
    except (FileNotFoundError,PermissionError,ProcessLookupError): pass
if runs:
    latest=runs[-1]
    result['latest_run']=str(latest)
    result['result_exists']=(latest/'result.json').is_file()
    if result['result_exists']:
        result['saved_result']=json.loads((latest/'result.json').read_text())
    result['logs']={}
    for name in ['data-generation.log','supervisor.log']:
        path=latest/name
        if path.is_file():
            with path.open('rb') as stream:
                stream.seek(max(0,path.stat().st_size-20000))
                lines=stream.read().decode(errors='replace').splitlines()[-35:]
            result['logs'][name]={'bytes':path.stat().st_size,'tail':[line[:1200] for line in lines]}
    result['data_files']=[{'path':str(p.relative_to(latest)),'bytes':p.stat().st_size} for p in latest.rglob('*') if p.is_file() and p.suffix in ('.bin','.idx')][:20]
print(json.dumps(result))
