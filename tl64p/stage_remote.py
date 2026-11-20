from copy import deepcopy
from pathlib import Path
import hashlib
import json
import shutil
import struct
import sys


NATIVE = Path('/opt/sim/simulator/Testcases/dispatch_memory_64p_top6_profile_bridge_20261120_native_clean01')
BUILD = Path('/work/src/dispatch_tilelang_64p_68d32608_build01')
CASES = NATIVE.parent
CASE = CASES/'dispatch_tilelang_64p_top6_68d32608_run01'
GENERATOR = Path('/work/src/dispatch_memory_64p_handoff/generator')

sys.path[:0] = [str(BUILD), str(GENERATOR)]
from contract.runtime_contract import RuntimeConfig, RuntimeLimits
from gen_testcase import (adapt_yaml, named_rows, numbered_chips, number, number_like,
                          owner, parameter_rows)
import yaml


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare():
    metadata = json.loads((BUILD/'runtime-profile.json').read_text())
    build = json.loads((BUILD/'build-receipt.json').read_text())
    assert build['status']=='PASS' and build['elf_sha']==digest(BUILD/'dispatch.o')
    limits = RuntimeLimits(**metadata['capacity_limits'])
    manifest = json.loads((NATIVE/'manifest.json').read_text())
    scenario = deepcopy(manifest['scenario'])
    scenario['kernel']['entry'] = metadata['entry']
    gm = scenario['gm_layout']
    aperture = scenario['gm_size_per_rank_bytes']
    scalar = scenario['kernel']['scalars']
    assert (scenario['world_size'], scalar['moeExpertNum'], scalar['bs'], scalar['h'], scalar['topK'])==(64,384,128,7168,6)
    assert all((NATIVE/row['path']).is_file() and digest(NATIVE/row['path'])==row['sha256']
               for row in manifest['files'] if Path(row['path']).name!='comm_args.bin')
    assert shutil.disk_usage(CASES).free >= 8<<30
    CASE.mkdir()
    for name in ('UBusSim_config','v310_config'):
        shutil.copytree(NATIVE/name, CASE/name)
    for name in ('test_UBusSim.py','audited_launcher.py','expected-v310-runtime.json','output_generator.py'):
        shutil.copy2(NATIVE/name, CASE/name)
    for name in ('bin','OUTPUT','dumps'):
        (CASE/name).mkdir()
    binary = CASE/'dispatch.o'
    shutil.copy2(BUILD/'dispatch.o',binary)
    (CASE/'input').mkdir()
    (CASE/'expected').mkdir()
    (CASE/'input/zeros-state.bin').write_bytes(bytes(1<<20))
    (CASE/'input/mask.bin').write_bytes(bytes([1])*128)
    (CASE/'input/diagnostics.bin').write_bytes(bytes([0xA5])*4096)
    plans=[]
    inputs=[]
    expected_by_rank={}
    layout = RuntimeConfig(tokens=128,hidden=7168,top_k=6,experts=384,world_size=64).layout(limits)
    assert layout['record_words']==3840
    peers=[]
    for rank in range(64):
        # Same peer aperture and bank choice as RuntimeSession.
        state = rank*aperture + gm['peer_base_offset_bytes'] + (1<<30) + (2<<20)
        payload = state + (1<<20) + 128*6*((7168*2+479)//480)*512
        assert payload + limits.payload_words*4 <= rank*aperture + gm['peer_base_offset_bytes'] + (2<<30)
        peers.extend((state,payload))
    names={'output':'expandXOutGM','assist':'assistInfoForCombineOutGM',
           'counts':'sendCountsOutGM','expert_nums':'expertTokenNumsOutGM'}
    native_files={row['path']:row for row in manifest['files']}
    profile_offsets=(128,16512,24704,28800,32896)
    profile_size=36992
    for rank in range(64):
        base=rank*aperture
        cfg=RuntimeConfig(tokens=128,hidden=7168,top_k=6,experts=384,world_size=64,rank=rank,cores=64)
        path=CASE/'input'/f'rank{rank}'
        path.mkdir()
        (CASE/'expected'/f'rank{rank}').mkdir()
        for member in ('x.bin','ids.bin'):
            rel=f'input/rank{rank}/{member}'
            shutil.copyfile(NATIVE/rel,CASE/rel)
            inputs.append({'name':rel,'sha256':native_files[rel]['sha256']})
        for member in names:
            rel=f'expected/rank{rank}/{member}.bin'
            shutil.copyfile(NATIVE/rel,CASE/rel)
        (path/'params.bin').write_bytes(struct.pack('<32Q',*cfg.words(limits)))
        (path/'peers.bin').write_bytes(struct.pack('<128Q',*peers))
        descriptors={}
        buffers={}
        cursor=base+gm['buffer_base_offset_bytes']
        allocations={'params':256,'x':1835008,'ids':3072,'mask':128,'peers':1024,
                     'output':layout['output_capacity_rows']*14336,'assist':layout['output_capacity_rows']*12,
                     'counts':1536,'expert_nums':48,'diagnostics':4096,'profileGM':profile_size}
        for name,size in allocations.items():
            cursor=(cursor+4095)//4096*4096
            descriptors[name]=cursor
            cursor+=size
        assert cursor < peers[rank*2]
        descriptors['state'],descriptors['payload']=peers[rank*2:rank*2+2]
        addresses=[descriptors['profileGM']+ofs for ofs in profile_offsets]
        # DeviceView version 1. Derive offsets from 64 lanes, 32 records per lane and
        # a 64-byte stride for each lane's count, dropped count and completion state.
        assert profile_offsets==(128,128+64*32*8,128+64*32*12,128+64*32*12+64*64,128+64*32*12+2*64*64)
        descriptor=struct.pack('<8I5Q',1,72,1,64,32,32,16,0,*addresses)
        (path/'profileGM.bin').write_bytes(descriptor.ljust(profile_size,b'\0'))
        for name in ['params','x','ids','peers','profileGM']:
            buffers[name]={'input':f'input/rank{rank}/{name}.bin'}
        for name,file in [('state','zeros-state'),('mask','mask'),('diagnostics','diagnostics')]:
            buffers[name]={'input':f'input/{file}.bin'}
        for out,native_ptr in names.items():
            buffers[native_ptr]={'addr':descriptors[out],
                'readback_bytes':(CASE/'expected'/f'rank{rank}'/(out+'.bin')).stat().st_size}
        abi=[(name,'scalar',0) if name=='fftsAddr_unused_u64' else (name,'pointer',descriptors[name])
             for name in metadata['args']]
        outputs={'profile':{'addr':descriptors['profileGM'],'readback_bytes':profile_size},
                 'diagnostics':{'addr':descriptors['diagnostics'],'readback_bytes':4096},
                 'state_count_ready':{'addr':descriptors['state'],'readback_bytes':384*32},
                 'state_consumer_gate':{'addr':descriptors['state']+876*1024,'readback_bytes':64*8*32},
                 'state_sender_gate':{'addr':descriptors['state']+64*1024+128*8*4,'readback_bytes':56*32}}
        plans.append({'rank':rank,'gm_base':base,'parameter_base':base+gm['parameter_base_offset_bytes'],
                      'block_dim':64,'abi':abi,'buffers':buffers,'profile_outputs':outputs})
        expected_by_rank[str(rank)]={name:(buffers[native_ptr]['readback_bytes']) for name,native_ptr in names.items()}
    template=yaml.safe_load((NATIVE/'workload.yml').read_bytes())
    rendered,_=adapt_yaml(template,plans,scenario,binary,CASE,{'pointer':11,'scalar':7},True)
    chips=numbered_chips(yaml.safe_load(rendered),64)
    for plan in plans:
        chip=chips[plan['rank']]
        actual=named_rows(owner(chip,'kernel_param')['kernel_param'])
        assert [(name,number(row['param_offset']),number(row['param_type']),number(row['value']))
                for name,row in actual]==[(name,idx*8,11 if role=='pointer' else 7,value)
                  for idx,(name,role,value) in enumerate(plan['abi'])]
        assert len(actual)==14
        assert all(Path(row['input_bin']).is_file() for _,row in actual if 'input_bin' in row)
        assert owner(chip,'vec_func')['vec_func']==metadata['entry']
    for name in ('workload.yml','workload_config.yml','v310_config/workload_config.yml'):
        (CASE/name).write_bytes(rendered)
    summary={'status':'PASS','case':str(CASE),'build_path':str(BUILD),'revision':build['revision'],
             'elf_sha':build['elf_sha'],'rank_count':64,'input_sha_count':len(inputs),
             'workload_sha':hashlib.sha256(rendered).hexdigest(),'profile_offsets':profile_offsets,
             'profile_size':profile_size,'plans':plans,'expected_sizes':expected_by_rank}
    (CASE/'tilelang-scenario.json').write_text(json.dumps(summary)+'\n')
    return {k:v for k,v in summary.items() if k not in ('plans','expected_sizes')}


try:
    print(json.dumps(prepare(),separators=(',',':')))
except Exception as error:
    import traceback
    if CASE.is_dir(): (CASE/'stage-error.log').write_text(traceback.format_exc())
    print(json.dumps({'status':'FAIL','stage':'stage','reason':type(error).__name__ + ': ' + str(error)[:220]}))
