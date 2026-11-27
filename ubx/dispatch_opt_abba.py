# Run counterbalanced full-profile A/B samples after both smoke verifiers pass.
import json
from pathlib import Path
source=Path(__file__).with_name('dispatch_opt_controller.py').read_text()
for index,variant in enumerate(['control','global','global','control']):
 code=source.replace('settings = json.loads(argument_file.read_text(encoding=\'utf-8-sig\'))',"settings = "+repr({'variant':variant,'profile':'on','batch':32,'smoke':False,'diagnostic':False}))
 print(json.dumps({'event':'abba_phase','index':index,'variant':variant}),flush=True)
 try:exec(compile(code,'dispatch-opt-abba-phase','exec'),{'__name__':'__main__','__file__':str(Path(__file__).with_name('dispatch_opt_controller.py'))})
 except SystemExit as e:
  if e.code not in [0,None]:raise
print(json.dumps({'event':'ABBA_DONE','passed':True,'warmup':10,'measured_per_run':20,'runs':4}),flush=True)
