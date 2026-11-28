"""Run a bounded offline variant of the original single-node GQA example."""
import datetime
import contextlib
import json
import math
import os
import pathlib
import re
import runpy
import shlex
import shutil
import signal
import subprocess
import sys

repo = pathlib.Path(sys.argv[1])
shim_source = sys.argv[2]
original = repo / 'scripts/example/llm/farescale_gqa.sh'
assert (repo / '.cross-zone-deployment.json').is_file()
root = pathlib.Path.home() / 'work/pangu_validation/ma-0/8da52234-c88f-451d-95a1-fa33a978690b'
run_dir = root / ('gqa-smoke-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
run_dir.mkdir(parents=True)
data = run_dir / 'synthetic-offline-data'
data.mkdir()
tokenizer_dir = run_dir / 'offline-tokenizer'
tokenizer_dir.mkdir()
work = pathlib.Path.home() / 'work'
shutil.copyfile(work / 'tokenizer.json',tokenizer_dir / 'tokenizer.json')
tokenizer_config = json.loads((work / 'tokenizer_config.json').read_text())
# Use the installed generic fast-tokenizer implementation with the existing vocabulary.
# The original custom Sophon Python modules are absent from the offline asset directory.
tokenizer_config['tokenizer_class'] = 'PreTrainedTokenizerFast'
tokenizer_config.pop('auto_map',None)
(tokenizer_dir / 'tokenizer_config.json').write_text(json.dumps(tokenizer_config))
# Use the repository's existing synthetic fixture and production offline writer.
sys.path.insert(0,str(repo))
with (run_dir / 'data-generation.log').open('w') as generation_log:
    with contextlib.redirect_stdout(generation_log), contextlib.redirect_stderr(generation_log):
        from PanGu.data_pipeline.tokenizer.tokenizer import _AutoTokenizer
        tokenizer_probe = _AutoTokenizer(str(tokenizer_dir),0,32768,True,local_files_only=True,trust_remote_code=False)
        assert tokenizer_probe.unique_identifiers and tokenizer_probe.eod is not None
        tokenizer_metadata = {'vocab_size':tokenizer_probe.vocab_size,'eod':tokenizer_probe.eod,
                              'implementation':'repository _AutoTokenizer / installed PreTrainedTokenizerFast'}
        fixture = runpy.run_path(str(repo / 'tests/group1/unit_test/datasets/omni_dataset/test_offline_tail_pack.py'))
        dataset = fixture['SyntheticOfflineMakingDataset'](data, lengths=[32768] * 16, seq_length=32768, packing=False)
        generated = dataset[0]
        if hasattr(generated,'__next__'):
            list(generated)
assert list(data.rglob('*.bin')) and list(data.rglob('*.idx')), 'Offline fixture writer produced no indexed shards'
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
replace_once(r'--tokenizer-model(?:=|\s+)\S+', '--tokenizer-model=' + shlex.quote(str(tokenizer_dir)))
replace_once(r'--train-samples\s+40\b', '--train-samples 8')
text, removed_count = re.subn(r'(--remove-arguments\s+)(\S+)',
    lambda m:m.group(1) + m.group(2) + ',inject-uce-error-plan,enable-high-availability,enable-hbmfault-repair,stress-detect,enable-monitor',text,count=1)
assert removed_count == 1, 'Expected removable-argument list was not found'
replace_once(r'2>&1\s*\|\s*tee\s+log_farescale_gqa.txt',
             '--tokenizer-type PretrainedFromHF --num-distributed-optimizer-instances 1 --log-path ' + shlex.quote(str(run_dir / 'rank-logs'))
             + ' 2>&1 | tee ' + shlex.quote(str(run_dir / 'console.log')))
wrapper = run_dir / 'run_farescale_gqa_offline.sh'
wrapper.write_text('set -eo pipefail\n' + text)
check = subprocess.run(['bash','-n',str(wrapper)],capture_output=True,text=True,timeout=10)
assert check.returncode == 0, 'Generated wrapper failed bash syntax validation'
env = os.environ.copy()
env.update({'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_DATASETS_OFFLINE':'1',
            'WANDB_MODE':'offline','TOKENIZERS_PARALLELISM':'false','BASE_DATA_PATH':str(data)})
runtime_dir = run_dir / 'runtime'
runtime_dir.mkdir()
(runtime_dir / 'sitecustomize.py').write_text(shim_source)
env['PYTHONPATH'] = str(runtime_dir) + os.pathsep + str(repo) + os.pathsep + env.get('PYTHONPATH','')
real_torchrun = shutil.which('torchrun')
assert real_torchrun, 'torchrun is missing'
guard_dir = run_dir / 'guard-bin'
guard_dir.mkdir()
guard = guard_dir / 'torchrun'
guard.write_text('#!' + sys.executable + '\nimport os,sys\n'
    + 'forbidden={"--inject-uce-error-plan","--enable-high-availability","--enable-hbmfault-repair","--stress-detect"}\n'
    + 'blocked=sorted(forbidden.intersection(a.split("=",1)[0] for a in sys.argv[1:]))\n'
    + 'if blocked:\n print("SMOKE_GUARD_BLOCKED: " + ",".join(blocked),flush=True)\n sys.exit(97)\n'
    + 'os.execv(' + repr(real_torchrun) + ',[' + repr(real_torchrun) + ']+sys.argv[1:])\n')
guard.chmod(0o700)
env['PATH'] = str(guard_dir) + os.pathsep + env.get('PATH','')
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
           'overrides':{'offline':True,'mock_data':False,'synthetic_offline_records':16,'synthetic_record_tokens':32768,
                        'dataset_writer':'repository SyntheticOfflineMakingDataset / production OfflineMakingDataset',
                        'runtime_compatibility':'process-local NPU get_device_capability fallback; two-rank HCCL probe passed',
                        'tokenizer_type':'PretrainedFromHF','tokenizer_metadata':tokenizer_metadata,'train_samples':8,'global_batch_size':4,
                        'num_distributed_optimizer_instances':1,'removed_ci_features':['inject-uce-error-plan','enable-high-availability','enable-hbmfault-repair','stress-detect','enable-monitor']},
           'preserved':{'devices':list(range(8)),'sequence_length':32768,'layers':6,'tensor_parallel':2,'context_parallel':2,'pipeline_parallel':2},
           'loss_lines':loss_lines[-6:],'iteration_lines':iteration_lines[-6:],'error_lines':errors[-10:],'console_tail':lines[-65:]}
(run_dir / 'result.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary))
if code != 0 or timed_out or not termination_confirmed or not loss_lines:
    sys.exit(1)
