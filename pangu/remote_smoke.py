"""Run a bounded offline variant of the original single-node GQA example."""
import datetime
import json
import math
import os
import pathlib
import re
import shlex
import signal
import subprocess
import sys

repo = pathlib.Path(sys.argv[1])
original = repo / 'scripts/example/llm/farescale_gqa.sh'
assert (repo / '.cross-zone-deployment.json').is_file()
root = pathlib.Path.home() / 'work/pangu_validation/ma-0/8da52234-c88f-451d-95a1-fa33a978690b'
run_dir = root / ('gqa-smoke-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
run_dir.mkdir(parents=True)
data = run_dir / 'mock-data'
data.mkdir()
text = original.read_text()
def replace_once(pattern, replacement):
    global text
    text, count = re.subn(pattern, lambda m:replacement, text, count=1, flags=re.M)
    assert count == 1, 'Expected example-script structure was not found: ' + pattern
# Keep the original script's directory calculations despite storing this wrapper elsewhere.
anchors = ('${BASH_SOURCE[0]}', '${0}', '$0')
assert any(anchor in text for anchor in anchors), 'Unknown script self-location convention'
for anchor in anchors:
    text = text.replace(anchor, str(original))
replace_once(r'--data-url(?:=|\s+)\S+', '--data-url=' + shlex.quote(str(data)))
replace_once(r'--tokenizer-model(?:=|\s+)\S+', '--tokenizer-model=' + shlex.quote(str(pathlib.Path.home() / 'work')))
replace_once(r'--train-samples\s+40\b', '--train-samples 8')
text, removed_count = re.subn(r'(--remove-arguments\s+)(\S+)',
    lambda m:m.group(1) + m.group(2) + ',inject-uce-error-plan,enable-high-availability,enable-hbmfault-repair,stress-detect',text,count=1)
assert removed_count == 1, 'Expected removable-argument list was not found'
replace_once(r'2>&1\s*\|\s*tee\s+log_farescale_gqa.txt',
             '--mock-data --tokenizer-type NullTokenizer --vocab-size 150016 --num-distributed-optimizer-instances 1 --log-path ' + shlex.quote(str(run_dir / 'rank-logs'))
             + ' 2>&1 | tee ' + shlex.quote(str(run_dir / 'console.log')))
wrapper = run_dir / 'run_farescale_gqa_offline.sh'
wrapper.write_text('set -eo pipefail\n' + text)
check = subprocess.run(['bash','-n',str(wrapper)],capture_output=True,text=True,timeout=10)
assert check.returncode == 0, 'Generated wrapper failed bash syntax validation'
env = os.environ.copy()
env.update({'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_DATASETS_OFFLINE':'1',
            'WANDB_MODE':'offline','TOKENIZERS_PARALLELISM':'false','BASE_DATA_PATH':str(data)})
env['PYTHONPATH'] = str(repo) + os.pathsep + env.get('PYTHONPATH','')
supervisor_log = run_dir / 'supervisor.log'
process = None
timed_out = False
termination_confirmed = True
with supervisor_log.open('wb') as out:
    process = subprocess.Popen(['bash',str(wrapper)],cwd=repo,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        code = process.wait(timeout=600)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid,signal.SIGTERM)
        try:
            code = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL)
            code = process.wait(timeout=15)
    try:
        os.killpg(process.pid,0)
        os.killpg(process.pid,signal.SIGTERM)
        termination_confirmed = False
    except ProcessLookupError:
        pass

console = supervisor_log.read_text(errors='replace')
lines = console.splitlines()
# Keep full logs locally while omitting repeated expanded torchrun command dumps from transport.
lines = [line if len(line) <= 1500 else line[:800] + ' ... [full line retained locally]' for line in lines]
loss_lines = [line for line in lines if re.search(r'(?:lm loss|language.model.loss|train.loss|training.loss|loss\s*[:=])',line,re.I)]
iteration_lines = [line for line in lines if re.search(r'iteration\s+\d+|global.step\s*[:=]\s*\d+|step\s+\d+\s*/',line,re.I)]
errors = [line for line in lines if re.search(r'Traceback|Error:|ModuleNotFoundError|AssertionError|RuntimeError|unrecognized arguments|FAILED|killed',line)]
summary = {'test':'scripts/example/llm/farescale_gqa.sh','source_commit':json.loads((repo / '.cross-zone-deployment.json').read_text())['commit'],
           'run_directory':str(run_dir),'wrapper':str(wrapper),'exit_code':code,'timed_out':timed_out,'termination_confirmed':termination_confirmed,
           'overrides':{'offline':True,'mock_data':True,'tokenizer_type':'NullTokenizer','vocab_size':150016,'train_samples':8,'global_batch_size':4,
                        'num_distributed_optimizer_instances':1,'removed_ci_features':['inject-uce-error-plan','enable-high-availability','enable-hbmfault-repair','stress-detect']},
           'preserved':{'devices':list(range(8)),'sequence_length':32768,'layers':6,'tensor_parallel':2,'context_parallel':2,'pipeline_parallel':2},
           'loss_lines':loss_lines[-6:],'iteration_lines':iteration_lines[-6:],'error_lines':errors[-10:],'console_tail':lines[-65:]}
(run_dir / 'result.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary))
if code != 0 or timed_out or not termination_confirmed or not loss_lines:
    sys.exit(1)
