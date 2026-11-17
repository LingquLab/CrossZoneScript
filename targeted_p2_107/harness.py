"""Use the candidate's native runner with explicit, gated Host ports for this experiment."""
import os,shlex,subprocess,time
from tilelang_dispatch import run_native as native

def run_round(args,directory,binaries,profiler):
    directory.mkdir();native.check_idle(args.devices,directory)
    port=args.barrier_port
    if not 1024<=port<=65533 or not args.comm_port==port+1:
        raise ValueError('Dedicated barrier and communicator ports required')
    env=os.environ.copy();env['TILEXR_COMM_ID']=f'127.0.0.1:{args.comm_port}'
    processes=[];logs=[];commands=[]
    try:
        for rank,binary in enumerate(binaries):
            command=[str(binary),'--native',str(args.devices[rank]),str(args.fixture/f'rank{rank}'),str(directory/f'rank{rank}'),str(args.warmup),str(port),args.pre_allreduce]
            if profiler:command=[profiler,'--application='+shlex.join(command),'--output='+str(directory/f'prof-rank-{rank}'),'--task-time=on']
            commands.append({'rank':rank,'variant':args.variant,'pair_round':args.pair_round,'schedule_ordinal':args.schedule_ordinal,'argv':command,'comm_id':env['TILEXR_COMM_ID'],'barrier_port':port})
            log=(directory/f'rank{rank}.log').open('xb');logs.append(log)
            processes.append(subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True))
        native.save(directory/'commands.json',commands)
        deadline=time.monotonic()+args.timeout
        while any(p.poll() is None for p in processes):
            if any(p.poll() not in (None,0) for p in processes):raise RuntimeError('Rank/application/profiler failed')
            if time.monotonic()>=deadline:raise TimeoutError('Bounded rank-group timeout')
            time.sleep(.05)
        if any(p.returncode!=0 for p in processes):raise RuntimeError('Rank/application/profiler nonzero')
    finally:
        native.finish_groups(processes,directory)
        for log in logs:log.close()
    profiles=[native.profiler_task(directory/f'prof-rank-{r}',f'tilexr_tl_dispatch_r{r}_c{c}',args.warmup) for r,c in native.core_pairs(args.cores)] if profiler else []
    result={'status':'PASS','variant':args.variant,'pair_round':args.pair_round,'schedule_ordinal':args.schedule_ordinal,'profiler':profiles,'scope':'process completion; operator/capture validation requires analyze_native'}
    native.save(directory/'execution.json',result);return result

def run(args):
    original=native.run_round
    native.run_round=run_round
    try:return native.run(args)
    finally:native.run_round=original
