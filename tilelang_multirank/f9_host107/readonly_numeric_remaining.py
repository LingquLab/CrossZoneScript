"""Verify saved BS256 evidence and reanalyze with frozen CPU analyzers; never launch NPU."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from numeric_projection_remaining import project, encode, stats

EXP = Path('/root/tilelang-107-f9-host107-b520293d-20261116-run01')
RUN = EXP / 'capture-t256-20261116-01'
OUT = EXP / 'capture-t256-readonly-numeric-v2-20261116-01'
REV = 'b520293d5f014fcbd4897a334d50ebf9ca9c601b'
WORKER_SHA = '89a6d6da5ff848a94d962e0ee167c9b867a2ad0d0d1e6061696398b740422b0c'
ORIGINAL = {'/etc/hccl_rootinfo.json':'56a0d1eb4c38422fbbdd946b34036ed6496fe36c1be95045d0519f35d69ad523',
 '/usr/local/Ascend/driver/topo/950/atlas_850_1.json':'65e257641adea221a576bab32710f759a59df80b50bc8ef613b38e10c6164db9'}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read(p):
    return json.loads(Path(p).read_text())


def verify_sources():
    assert sha(EXP/'artifact-hashes.json') == '2e30632f8e71376742c47ca4b1d7bacebee5d8aaea80ae8e78e0324fbddfcfe7'
    assert sha(EXP/'provenance.json') == 'f56caa6f4e589110eefc3a01eeec8e4d37ca4899a879e01f7a790670f155add1'
    provenance = read(EXP/'provenance.json')
    assert provenance['revision'] == REV and provenance['core_cxx11_abi'] == 0
    for name, expected in read(EXP/'artifact-hashes.json').items():
        assert sha(EXP/name) == expected, 'Built artifact changed'
    assert sha(EXP/'ab.zip') == '5a4011c99da6ffcca4c5ce743778f090aae4f4d3a2345f4b4c6191b653984ba9'
    with zipfile.ZipFile(EXP/'ab.zip') as bundle:
        for variant in ('baseline', 'f9'):
            raw = bundle.read(variant+'/source-manifest.json')
            assert (EXP/variant/'source-manifest.json').read_bytes() == raw
            for name, expected in json.loads(raw).items():
                assert sha(EXP/variant/'TileXR'/name) == expected, 'Frozen source changed'
            for name in bundle.namelist():
                if name.startswith(variant+'/generated/'):
                    assert (EXP/name).read_bytes() == bundle.read(name), 'Frozen export changed'
    assert sha(EXP/'fixture-hashes.json') == provenance['fixture_hashes_sha256']
    for name, expected in read(EXP/'fixture-hashes.json').items():
        assert sha(EXP/name) == expected, 'Fixture changed'
    dependency = Path('/root/tilelang-107-multirank-e28d87de-20261116-run01/dependencies.zip')
    assert sha(dependency) == provenance['dependency_zip_sha256']
    with zipfile.ZipFile(dependency) as bundle:
        for name, expected in json.loads(bundle.read('dependency-manifest.json'))['files'].items():
            if name.startswith('TileXR/tools/profiling/python/'):
                for variant in ('baseline','f9'):
                    assert sha(EXP/variant/name) == expected, 'Frozen parser changed'
    assert sha(RUN/'runtime_group.py') == 'b768d8a2f6209fa4a89621cd1b3927bc5cc6472080b33e11e76bf1236eaa7a2f'
    for name, expected in ORIGINAL.items():
        assert sha(name) == expected, 'Original configuration changed'


REANALYZE = '''import sys,json,importlib.util
from pathlib import Path
sys.dont_write_bytecode=True
exp,run,key,variant,tokens=sys.argv[1:]
exp,run=Path(exp),Path(run)
sys.path.insert(0,str(exp/variant/'TileXR/examples'))
spec=importlib.util.spec_from_file_location('saved_worker',run/'runtime_group.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
d=run/'groups'/key
analysis,rows=m.validate_native(d/'native',exp/('fixture/p4-e8-t'+tokens+'-balanced'),exp/variant/('generated/t'+tokens),True)
saved=json.loads((d/'analysis.json').read_text())
normalized=json.loads(json.dumps(analysis,allow_nan=False))
def diff_paths(actual,expected,path='$',limit=6):
    if actual==expected:return []
    if isinstance(actual,dict) and isinstance(expected,dict):
        paths=[]
        for key in sorted(set(actual)|set(expected)):
            child=path+'.'+str(key)
            if key not in actual or key not in expected:paths.append(child+': missing key')
            else:paths.extend(diff_paths(actual[key],expected[key],child,limit-len(paths)))
            if len(paths)>=limit:break
        return paths[:limit]
    if isinstance(actual,list) and isinstance(expected,list):
        if len(actual)!=len(expected):return [path+': length differs']
        paths=[]
        for index,(a,b) in enumerate(zip(actual,expected)):
            paths.extend(diff_paths(a,b,path+'['+str(index)+']',limit-len(paths)))
            if len(paths)>=limit:break
        return paths[:limit]
    return [path+': value/type differs']
if normalized!=saved:
    print(json.dumps({'group':key,'status':'FAIL','normalized_diff_paths':diff_paths(normalized,saved)}),flush=True)
    raise AssertionError('Normalized saved analysis differs from frozen CPU reanalysis')
def integer_keys(value):
    if isinstance(value,dict):return sum(type(k) is int for k in value)+sum(integer_keys(v) for v in value.values())
    if isinstance(value,(list,tuple)):return sum(integer_keys(v) for v in value)
    return 0
assert rows==json.loads((d/'rank-validation.json').read_text()),'Rank validation differs'
print(json.dumps({'group':key,'status':'PASS','ranks':len(rows),'capture_checked':analysis['capture_checked'],'direct_python_equal':analysis==saved,'json_normalized_equal':True,'integer_dictionary_keys':integer_keys(analysis)}))
'''


def verify_group(group):
    directory=RUN/'groups'/group['group']
    for name, field in (('group-summary.json','summary_sha256'),('analysis.json','analysis_sha256'),('capture-evidence.json','capture_evidence_sha256')):
        assert sha(directory/name)==group[field], 'Received capture identity mismatch'
    summary=read(directory/'group-summary.json')
    assert summary['status']=='PASS' and summary['revision']==REV and summary['index']==group['index']
    assert summary['devices']==[0,1,2,3] and summary['cores']==[64]*4
    assert all(v['idle'] for v in summary['occupancy_after']) and len(summary['occupancy_after'])==4
    assert all(v['ok'] for v in summary['health_after']) and len(summary['health_after'])==4
    assert not summary.get('error_type') and not summary.get('finalization_error_detail')
    assert sha(directory/'rank-validation.json')==summary['rank_validation_sha256']
    evidence=read(directory/'capture-evidence.json')
    assert len(evidence)==38, 'Unexpected capture evidence inventory'
    for name, expected in evidence.items():
        path=Path(name)
        if path.is_absolute():
            assert path.is_relative_to(EXP/group['variant']/('generated/t'+str(TOKENS)))
        else:
            assert '..' not in path.parts
            path=directory/path
        assert sha(path)==expected, 'Capture evidence changed'
    return read(directory/'analysis.json')


RECEIVED = {'16': [{'group': 'g033', 'index': 33, 'phase': 'capture-t16', 'variant': 'baseline', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 1, 'mode': 'capture', 'hccl_base_port': 62608, 'comm_port': 62648, 'status': 'PASS', 'summary_sha256': '21cc727b95308ccd46e4ed21361379251d3f32a24090d192dd874f9b291973b0', 'analysis_sha256': 'ea7a0cd948a986f3b0fa4ad90617a5a52d4a30d0e1ac1fa6ede0205c3b797489', 'capture_evidence_sha256': '37fdf554d172f61a32b50ed51ee898723653c8fed32d86961366edf80258377a'}, {'group': 'g034', 'index': 34, 'phase': 'capture-t16', 'variant': 'f9', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 1, 'mode': 'capture', 'hccl_base_port': 62656, 'comm_port': 62696, 'status': 'PASS', 'summary_sha256': '42ec3766a7025fdc9b828649c4696608bcb119023198bf18b505be2afecf09f1', 'analysis_sha256': 'da69379d1c962b60927bc3727d83c9fc7732e37cb7eb8c92ac856f90109f306d', 'capture_evidence_sha256': '89ba6003f69c1f459956aa329901690d0a41d21b21d0052aa85f61eeb61ee65d'}, {'group': 'g035', 'index': 35, 'phase': 'capture-t16', 'variant': 'f9', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 2, 'mode': 'capture', 'hccl_base_port': 62704, 'comm_port': 62744, 'status': 'PASS', 'summary_sha256': 'a0e1736ed38c05969f2c79836b50fd8a4dc4a499591337de0b68645480b4f520', 'analysis_sha256': '2fe055d7174acf43a7ce99380002ba43077cb206520aa17b9416843804b7eee4', 'capture_evidence_sha256': '8f7c6934e4347ded2244a5d51e1df5d68cff32afdb448ecfa73fff12231438f5'}, {'group': 'g036', 'index': 36, 'phase': 'capture-t16', 'variant': 'baseline', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 2, 'mode': 'capture', 'hccl_base_port': 62752, 'comm_port': 62792, 'status': 'PASS', 'summary_sha256': 'a00f87502dcbaa61ed11915dd27915d6e7f68321aba88177a17e0da444bc1495', 'analysis_sha256': '244acd0073de682325bceaff71e27e3bf96a74ad032e30741dcaefbdb7d62481', 'capture_evidence_sha256': 'c08df4059de1b99e6f10bc64d78538e608fe194f2d34f6fa0ae0c479835a7cf0'}, {'group': 'g037', 'index': 37, 'phase': 'capture-t16', 'variant': 'baseline', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 3, 'mode': 'capture', 'hccl_base_port': 62800, 'comm_port': 62840, 'status': 'PASS', 'summary_sha256': 'fe7b5d9dff0c1f1ffc965b0fbb1ea136468ed217b7521e67ff7e811846a4e1ac', 'analysis_sha256': '09554eed2db3d1a09e1664ab8e5dc8145e403e188ebfa75285e3b203992adea9', 'capture_evidence_sha256': 'fd14ca7255e4e1178845fae3b737a1972bf8eae1093cb5ec86699b83f61dcbdc'}, {'group': 'g038', 'index': 38, 'phase': 'capture-t16', 'variant': 'f9', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 3, 'mode': 'capture', 'hccl_base_port': 62848, 'comm_port': 62888, 'status': 'PASS', 'summary_sha256': '5d312094ca2f3830a41a1f2438f49a7dddaedd414151ea513d1d5218e642a849', 'analysis_sha256': 'ed466a99be2ced5b9696941eb1219a048be51ee24bd7d49d0549102a28e9b831', 'capture_evidence_sha256': '331724edb040eb4f987149b98d813e8d839538eac04be6e4bcc9f23c88ba54f9'}, {'group': 'g039', 'index': 39, 'phase': 'capture-t16', 'variant': 'f9', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 4, 'mode': 'capture', 'hccl_base_port': 62896, 'comm_port': 62936, 'status': 'PASS', 'summary_sha256': '06c729401acab2472edfee6a0d0a41c4a896824b54f36649c394f782ab26503e', 'analysis_sha256': '1cf392d86a9c44cdc7e04d6f953238cbc19fc4707f65f9e8bdafb2afe9c68ad1', 'capture_evidence_sha256': 'd673a1a556eeec2724a1f5a35af0e63541ce88d402de1a1ec2dc8058d53358a0'}, {'group': 'g040', 'index': 40, 'phase': 'capture-t16', 'variant': 'baseline', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 4, 'mode': 'capture', 'hccl_base_port': 62944, 'comm_port': 62984, 'status': 'PASS', 'summary_sha256': '771719dbfd6f4aa373adbce4575d2f78946dc8e79be11caa08bb6712a477dc13', 'analysis_sha256': '95c9bddc8725645af8aeec114b2247872bca2b678757b9e22955036c949bfab4', 'capture_evidence_sha256': 'ebe2bdc1f4b0ed75a165f912e816154b151e1e440fa4aa9165864f4d57884374'}, {'group': 'g041', 'index': 41, 'phase': 'capture-t16', 'variant': 'baseline', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 5, 'mode': 'capture', 'hccl_base_port': 62992, 'comm_port': 63032, 'status': 'PASS', 'summary_sha256': '9f33f6f78a58e8b27ce7c33b9bdd02daa217dd33f3c9fa36b63555ac0bed800f', 'analysis_sha256': '3eb1fb7dc73656c55d74f42715d9c90948927e3c570b49bb00f6780222fd4455', 'capture_evidence_sha256': '30616c10a47c81f66673c5c1aac2ecffd54e542f04828fba2171d42a6a80142d'}, {'group': 'g042', 'index': 42, 'phase': 'capture-t16', 'variant': 'f9', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 5, 'mode': 'capture', 'hccl_base_port': 63040, 'comm_port': 63080, 'status': 'PASS', 'summary_sha256': '94b7df96a877b9ca2c2f48f129582fd2e04582753945fc7f9e86fc30498acce2', 'analysis_sha256': 'a3cd672528ec71c6e6d46bbc300311c1b897122b8146f51785b05ff7a2337593', 'capture_evidence_sha256': '886b3552cfd7fc5a8fa0eb8f02976d175e4c116de04260925bfc2db49aa2b272'}, {'group': 'g043', 'index': 43, 'phase': 'capture-t16', 'variant': 'f9', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 6, 'mode': 'capture', 'hccl_base_port': 63088, 'comm_port': 63128, 'status': 'PASS', 'summary_sha256': '640a2946666f849d41b33491b36c9ec18e951098257044551e0e195ea6c4a346', 'analysis_sha256': '3b9425234f7477d108bea474a21140b12134d649d1cd5aa5cfdb328681af8c1a', 'capture_evidence_sha256': '79111c62db1b2207da16e2dbe5396ff4f4df7236fa781b5d03f667aa872b7735'}, {'group': 'g044', 'index': 44, 'phase': 'capture-t16', 'variant': 'baseline', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 6, 'mode': 'capture', 'hccl_base_port': 63136, 'comm_port': 63176, 'status': 'PASS', 'summary_sha256': '3166721c0b59c814e8d6322e76ee6ae051657872a8c016036ebd69bd6803060e', 'analysis_sha256': '380d7039f22e87e994fa3950c6768c7f1ad266e898534842bb7911186e57954d', 'capture_evidence_sha256': 'f6abd2e86f596fc3f31328ff6657e58f45d2c2dcd2c44d01d9bd1d4bb56c8e15'}, {'group': 'g045', 'index': 45, 'phase': 'capture-t16', 'variant': 'baseline', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 7, 'mode': 'capture', 'hccl_base_port': 63184, 'comm_port': 63224, 'status': 'PASS', 'summary_sha256': '7dce8cb2f2d339e72d25f2e09b7e0e45ec8fe4c1e27b1a47de6188772f422817', 'analysis_sha256': '54a89b2dd4d862f928cd8a47132edac264adbf61439c48cb37aeb2ece1caddc2', 'capture_evidence_sha256': 'dd6e0e9efab8fc571ac9b9c66bd674df2e538f491c98497bfd0aeb6ad4f020d2'}, {'group': 'g046', 'index': 46, 'phase': 'capture-t16', 'variant': 'f9', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 7, 'mode': 'capture', 'hccl_base_port': 63232, 'comm_port': 63272, 'status': 'PASS', 'summary_sha256': '3b716fb91d260b3713faaeee9d302ab0ae986604d81e4b950a8308c031ca434f', 'analysis_sha256': '867c03359061e7f8d2b3556b72045051eb9e50c0e06357dee88a412f1e26941c', 'capture_evidence_sha256': '52e365e0149a33e283c1bc957f1d30ad4e6e7520b7bc466d16d44300cac19013'}, {'group': 'g047', 'index': 47, 'phase': 'capture-t16', 'variant': 'f9', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 8, 'mode': 'capture', 'hccl_base_port': 63280, 'comm_port': 63320, 'status': 'PASS', 'summary_sha256': 'b8645970b10144ac41c6be804c0d1b4fc3f4aea9535b694366170d9780a78841', 'analysis_sha256': 'ff732156326fbcdbb8da8ec15c1b3abbb017c6f55f6ed1fc410fde67b83a1364', 'capture_evidence_sha256': 'f9a62b884fc904a5c3a373b5d252b8a71411e114c354c38c62f57d4e32aa2398'}, {'group': 'g048', 'index': 48, 'phase': 'capture-t16', 'variant': 'baseline', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 8, 'mode': 'capture', 'hccl_base_port': 63328, 'comm_port': 63368, 'status': 'PASS', 'summary_sha256': '9309ae0e61229bddaaaa82c1545bef0007ecc2a6cfb0b199fb4d8f5ca4c71269', 'analysis_sha256': '6f7445fc563bb6499ea1a2530a2789c963b8f3b2cfe3e793d59d987f8a6cd5e6', 'capture_evidence_sha256': 'f0a5b2b2027c16059d5183c36d4436aa10c3117b9a2b549eff2636fd3eb29e2e'}, {'group': 'g049', 'index': 49, 'phase': 'capture-t16', 'variant': 'baseline', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 9, 'mode': 'capture', 'hccl_base_port': 63376, 'comm_port': 63416, 'status': 'PASS', 'summary_sha256': '46aa7d03eeea2874d882d016778243ea93e4b1822527974519282f42466ad4b2', 'analysis_sha256': '7e03f085893944bc041c54ce20371a3933ffe0aed44ef90af91b89fbf2ae823f', 'capture_evidence_sha256': '8cb26f9644bb95c4393cdc944a0144facdf868d967f9f48f293b033190457252'}, {'group': 'g050', 'index': 50, 'phase': 'capture-t16', 'variant': 'f9', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 9, 'mode': 'capture', 'hccl_base_port': 63424, 'comm_port': 63464, 'status': 'PASS', 'summary_sha256': '99e723c18353e136fda8cbc2c7858df4cf4e7ddab7b14ee799dc026a00e76b76', 'analysis_sha256': '9d81af9a9159bb0fd8ea798ac6f10db5774cafac7c493ee4c416cffce1d97089', 'capture_evidence_sha256': '3dfe46377b9d2e0ca2e7b480539a9418f9f24256494eea273044645bf854519b'}, {'group': 'g051', 'index': 51, 'phase': 'capture-t16', 'variant': 'f9', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 10, 'mode': 'capture', 'hccl_base_port': 63472, 'comm_port': 63512, 'status': 'PASS', 'summary_sha256': '2d8a264b95759ccccdf70f87c34a61f2cd7d9fa1a5ad50bc70a52b61cb1b8431', 'analysis_sha256': 'a877f7b92b8f6c643b51312d31233316ad1da7ced99c8c2d8e78f9aff545e006', 'capture_evidence_sha256': 'c3e3b80f46203d5e0e4f368d01a638dbc90f0ed302c1ae64a87f3c4b0dc2f674'}, {'group': 'g052', 'index': 52, 'phase': 'capture-t16', 'variant': 'baseline', 'case': 'p4-e8-t16-balanced', 'tokens': 16, 'round': 10, 'mode': 'capture', 'hccl_base_port': 63520, 'comm_port': 63560, 'status': 'PASS', 'summary_sha256': 'd0c7b5f88739a65d7cffb63d9f7ecb701688c544673f63fc2e1e92cd9ff3443f', 'analysis_sha256': 'b435f74cd0e22e1840fbedd8e09f3f865c2cc9d594862d3b29649975f87949fb', 'capture_evidence_sha256': 'a9f9ade151e4ea80f46530b771eeb2ef0c215106dbffebed486abbf79d39115d'}], '64': [{'index': 53, 'phase': 'capture-t64', 'variant': 'baseline', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 1, 'mode': 'capture', 'hccl_base_port': 63568, 'comm_port': 63608, 'group': 'g053', 'status': 'PASS', 'summary_sha256': '4efdea79ae2e34989dafc6e908d838ed2ad3e9c241731e08c213c00e8bb39ca5', 'analysis_sha256': 'bb6f17d17f63bde6c21b5965e5b02f0395e9b415f3cb97d7acaaa1310713d968', 'capture_evidence_sha256': '44a9fa3a3b8513e544ff4d39ee7f1e8a0296afe94ee5ed4b3b567b68dbd6c939'}, {'index': 54, 'phase': 'capture-t64', 'variant': 'f9', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 1, 'mode': 'capture', 'hccl_base_port': 63616, 'comm_port': 63656, 'group': 'g054', 'status': 'PASS', 'summary_sha256': 'cc108bfbf981e692303e3a748c80713595b704a5283dfd0994253c49de69ba53', 'analysis_sha256': 'bea2690add835a9c9ded0d03901b79391a43a0e41c187087ee5d060315362ad4', 'capture_evidence_sha256': '215c11eb6e135cc384a88ae0c3d18073b2bd3b314c54230f13f4140437d9d04b'}, {'index': 55, 'phase': 'capture-t64', 'variant': 'f9', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 2, 'mode': 'capture', 'hccl_base_port': 63664, 'comm_port': 63704, 'group': 'g055', 'status': 'PASS', 'summary_sha256': 'd4bc27e40b309ccb7cf22d90b05cf3b32bb01017bb21168610bd57f04aec3c89', 'analysis_sha256': 'ccec59835fdc63e593872ce7bed45fafd5caa5bca5a049785c7a245853e3c579', 'capture_evidence_sha256': '6877e159a366a24e3d18b8a2cf208db77a0472beaa8c9a4fce16128677f4a444'}, {'index': 56, 'phase': 'capture-t64', 'variant': 'baseline', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 2, 'mode': 'capture', 'hccl_base_port': 63712, 'comm_port': 63752, 'group': 'g056', 'status': 'PASS', 'summary_sha256': '30e20b7a10bf13858cecb8c52ed78a3474378f6d8ac3ee908e0b45cad1e1efe7', 'analysis_sha256': '31e0b216f8c9a610661f7201c4d181678101620189a370f8eae1c54ad848c234', 'capture_evidence_sha256': 'ea5a310d0ae912ef3482d4033e345deb3018f58fb540c1e5e2b7654749be0b3b'}, {'index': 57, 'phase': 'capture-t64', 'variant': 'baseline', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 3, 'mode': 'capture', 'hccl_base_port': 63760, 'comm_port': 63800, 'group': 'g057', 'status': 'PASS', 'summary_sha256': 'ede4341e8f11dd523df7cca7172ddcb6a1b07d6f2960cd1f4b165043f195397d', 'analysis_sha256': 'a1c81d7d3db6b4c08c9230550423ecd680b77fdd67ee34eba3ace4e9deea21ba', 'capture_evidence_sha256': 'df8a5829e53d5a4497de5260ebe8ef137a02b4500df9697c8e72e70ec53ec88a'}, {'index': 58, 'phase': 'capture-t64', 'variant': 'f9', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 3, 'mode': 'capture', 'hccl_base_port': 63808, 'comm_port': 63848, 'group': 'g058', 'status': 'PASS', 'summary_sha256': 'bb62463bc8cfb3f86b189b8658b8ec344a504b8ce65a710c651a03854180a28c', 'analysis_sha256': '97b350d2bf3d1b426b105fb9a58dd7c184b00425b9b7353b7f5118f2ce12ce4d', 'capture_evidence_sha256': 'd7678b0e4ebcbc4c0a10d0bcbb6e9597b22e3e1f32e4b53b1b8f3d599d9441af'}, {'index': 59, 'phase': 'capture-t64', 'variant': 'f9', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 4, 'mode': 'capture', 'hccl_base_port': 63856, 'comm_port': 63896, 'group': 'g059', 'status': 'PASS', 'summary_sha256': '6afb55afb0f5cfad5121ff96bb55956983e8e5c745b990710ad83b3785dd5e5f', 'analysis_sha256': 'fb206e942b22341aa542bd4f66180b22ed4d6a8adcbdb77ad312990d4683c184', 'capture_evidence_sha256': 'ace339eac4d28b939f5d9ddf3ee24c2bfcce7f185f114c11deb2b8dc86d09f82'}, {'index': 60, 'phase': 'capture-t64', 'variant': 'baseline', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 4, 'mode': 'capture', 'hccl_base_port': 63904, 'comm_port': 63944, 'group': 'g060', 'status': 'PASS', 'summary_sha256': '3c6c23f6237d7d857726009a4cdeb7f7f7dbf77e152dd30a08189e35c6f1bfa5', 'analysis_sha256': '5fb5040b0e00cdd5c80d3f86ad630ad23f286c2f2e999c93ccaf372eb42fdd59', 'capture_evidence_sha256': '6d42f91cb5733beada6947f0bf7e801d70a26f13df0c170cba3835adf5769d7d'}, {'index': 61, 'phase': 'capture-t64', 'variant': 'baseline', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 5, 'mode': 'capture', 'hccl_base_port': 63952, 'comm_port': 63992, 'group': 'g061', 'status': 'PASS', 'summary_sha256': '261faca319239232bb4c15f67875e367a7f47933afed330b4ba619513f68cfab', 'analysis_sha256': 'a2e10fe4456b113594cd02dc5181cb19f7381b4e7ccbd98a85a8fbce6e64ee59', 'capture_evidence_sha256': 'ab376a6f857ba9dbc1eff8815e2f9c365da5becc226999394f8081d8b1f5b4f4'}, {'index': 62, 'phase': 'capture-t64', 'variant': 'f9', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 5, 'mode': 'capture', 'hccl_base_port': 64000, 'comm_port': 64040, 'group': 'g062', 'status': 'PASS', 'summary_sha256': 'a36479db263f1f3f9f590d46d983d1bacd43c65c2e2ecd1d468dc0f4a9daeb9f', 'analysis_sha256': '83f6a7b4b43b17f7e41bedf51c35a79b6cd0cf2bdfaedfd3c1e32b4fb556e106', 'capture_evidence_sha256': '388073940159f78d1ed070f383ac4b3e253fc389fa1253893fb2f0422a6f6d53'}, {'index': 63, 'phase': 'capture-t64', 'variant': 'f9', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 6, 'mode': 'capture', 'hccl_base_port': 64048, 'comm_port': 64088, 'group': 'g063', 'status': 'PASS', 'summary_sha256': '673b679dc86e82b9ccdae0584e94ca546cba3030dea2fb753f2971ca81409730', 'analysis_sha256': '2796533dd255f0de2274131ecb8684cde2a6aa3597d644892e5ad783467bd491', 'capture_evidence_sha256': '3158cc1b373ca4cc138ac91089d482012845c4fdcb547f5e71f59b3fe21deda0'}, {'index': 64, 'phase': 'capture-t64', 'variant': 'baseline', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 6, 'mode': 'capture', 'hccl_base_port': 64096, 'comm_port': 64136, 'group': 'g064', 'status': 'PASS', 'summary_sha256': 'dc861ff04493124033a48d8affb96214bdb67e3c8be975aff38e97dbcc86de82', 'analysis_sha256': '1357a0ecc009c36bdd509ba89b6937bd728a04178ac150874ca8150b508f1c21', 'capture_evidence_sha256': '3cfa5b2031f476b0c44d2442e3bcd589bb647d5e63ad85ed77eb18f3b75eb457'}, {'index': 65, 'phase': 'capture-t64', 'variant': 'baseline', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 7, 'mode': 'capture', 'hccl_base_port': 64144, 'comm_port': 64184, 'group': 'g065', 'status': 'PASS', 'summary_sha256': '4482d1c19d338c3cff7f6979fe24918636326845ce992008258316acd4d0543f', 'analysis_sha256': '33324b7fd92e048b7cea065fd04736d97a3042545d763b5d2a6ca708ddaf8371', 'capture_evidence_sha256': '1ebed0f2daf1639e436bf57c138573b1222691a34856601ca3ef9dc564cd4e2b'}, {'index': 66, 'phase': 'capture-t64', 'variant': 'f9', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 7, 'mode': 'capture', 'hccl_base_port': 64192, 'comm_port': 64232, 'group': 'g066', 'status': 'PASS', 'summary_sha256': '2682a9d6e73e92d6acc06c5260edd02b6028fdb406a8b7267b377e5422005e46', 'analysis_sha256': 'b53ab6c7030397f78c636dcfb9835fbb8ec7daeadfa765ef80510eacb4c0a02e', 'capture_evidence_sha256': '6078752540b3d592b36963182369ac5f7a0e917481ef108d1665142727643690'}, {'index': 67, 'phase': 'capture-t64', 'variant': 'f9', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 8, 'mode': 'capture', 'hccl_base_port': 64240, 'comm_port': 64280, 'group': 'g067', 'status': 'PASS', 'summary_sha256': '9c3dcfc404b03313e330adf79209d519fcdf6c8980b470c43de7aeea9b8291bf', 'analysis_sha256': 'bbe8beeeb801766ee36efdad5c45e753da7b725bb187fbfcb2d53011f62b102d', 'capture_evidence_sha256': '8e05e283a61e9896b59bf5b37a53969e65722e53f68272bbe7668829fe4ef455'}, {'index': 68, 'phase': 'capture-t64', 'variant': 'baseline', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 8, 'mode': 'capture', 'hccl_base_port': 64288, 'comm_port': 64328, 'group': 'g068', 'status': 'PASS', 'summary_sha256': 'ed52bfad3f9a892af829866e0e8ddaf6aad7e7ff428c1ea75457f5444b74b062', 'analysis_sha256': 'efbf01456d50e5d4bc098b15d1ae74afdcb131849760ffe0400d6dff5ad6b752', 'capture_evidence_sha256': '047d22ea8ef975d87fbfba7dafec0f2366eb6c9532e9a657b023cdea0e12e1a7'}, {'index': 69, 'phase': 'capture-t64', 'variant': 'baseline', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 9, 'mode': 'capture', 'hccl_base_port': 64336, 'comm_port': 64376, 'group': 'g069', 'status': 'PASS', 'summary_sha256': '19b3a7e39f6f38520274cf65754d5f4cff02689cb732b7a59027f7745214ae9f', 'analysis_sha256': 'f699cd701fbb7767a68ea4f420ee01ea3bb8c458a6a99e8e9695c8b3b9404aa8', 'capture_evidence_sha256': '05960c2417f66f7e6ec7679e392f784e0be93a656c352b18434dde0674525e22'}, {'index': 70, 'phase': 'capture-t64', 'variant': 'f9', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 9, 'mode': 'capture', 'hccl_base_port': 64384, 'comm_port': 64424, 'group': 'g070', 'status': 'PASS', 'summary_sha256': '997077df2b29a8890b2c0e05090b7e9fdda1d2033fe7ec4d6b4be11aef6fb795', 'analysis_sha256': '4a5778c886e3bedb09b41844087757219008bcddb07704688e76f7f23da32f31', 'capture_evidence_sha256': 'b073cfc3f3a24ae591fa7695cda3fd139ff9dfe3f2175fa45af8f57a6d7b0005'}, {'index': 71, 'phase': 'capture-t64', 'variant': 'f9', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 10, 'mode': 'capture', 'hccl_base_port': 64432, 'comm_port': 64472, 'group': 'g071', 'status': 'PASS', 'summary_sha256': '0e8985d86e4ab9d4c9c0278df7d8a51ce4291dacc480b5ba07e9e6f72d979e00', 'analysis_sha256': '684766fd69d61a579f9030fea57e53e3a7eceae50174295db75af33f22030403', 'capture_evidence_sha256': '15c4408347a0ea1331712a4f535bfefeaa37db4a620844b8d79ca9cae31c8530'}, {'index': 72, 'phase': 'capture-t64', 'variant': 'baseline', 'case': 'p4-e8-t64-balanced', 'tokens': 64, 'round': 10, 'mode': 'capture', 'hccl_base_port': 64480, 'comm_port': 64520, 'group': 'g072', 'status': 'PASS', 'summary_sha256': '6b56770ae68506b3f0831d7a4273a6055bf6dd622951a25f9e4b2857de347aa6', 'analysis_sha256': '0e0df9d2eeb63c59e75e91888eab45efb96c088379cfd0fc932fd8c828bc7cdc', 'capture_evidence_sha256': '006ac234865f3165a427fd353676195cb63a64eeb2e1ad55483499d4d3944419'}]}

def main_one(tokens):
    global TOKENS, RUN, OUT
    TOKENS=tokens
    RUN=EXP/('capture-t%d-20261116-01'%tokens)
    OUT=EXP/('capture-t%d-readonly-numeric-20261116-01'%tokens)
    parent_sha=sha(RUN/'summary.json')
    worker_sha=sha(RUN/'worker-summary.json')
    parent=read(RUN/'summary.json');worker=read(RUN/'worker-summary.json')
    for value in (parent,worker):
        assert value['status']=='PASS' and value['revision']==REV and value['tokens']==tokens
        assert value['fresh_rank_groups']==20 and value['rank_captures']==80
        assert value['warmup_per_group']==20 and value['measured_per_group']==1 and value['profiling']
        assert value['devices']==[0,1,2,3] and value['cores']==[64]*4
    assert parent['namespace_exit']==0 and not parent['timed_out'] and not parent['own_residual_children']
    assert parent['original_configuration_preserved'] and parent['qualification_reused']
    groups=RECEIVED[str(tokens)]
    for value in (parent,worker):
        assert len(value['groups'])==20
        for expected,saved in zip(groups,value['groups']):
            assert all(saved[k]==v for k,v in expected.items()),'Received group identity differs'
    assert [g['index'] for g in groups]==list(range({16:33,64:53}[tokens],{16:33,64:53}[tokens]+20))
    verify_sources()
    OUT.mkdir(exist_ok=False)
    pairs=[];cpu=[]
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
    for group in groups:
        analysis=verify_group(group)
        p=subprocess.run(['/usr/bin/python3','-c',REANALYZE,str(EXP),str(RUN),group['group'],group['variant'],str(tokens)],env=env,capture_output=True,timeout=40)
        (OUT/(group['group']+'.cpu.stdout.log')).write_bytes(p.stdout)
        (OUT/(group['group']+'.cpu.stderr.log')).write_bytes(p.stderr)
        assert p.returncode==0,'Frozen CPU reanalysis failed: '+group['group']
        result=json.loads(p.stdout)
        assert result['status']=='PASS' and result['ranks']==4 and result['capture_checked'] and result['json_normalized_equal']
        assert analysis==verify_group(group)
        cpu.append(result);pairs.append((group,analysis))
    numeric=project(pairs,tokens)
    raw=encode(numeric)
    (OUT/'numeric.json').write_bytes(raw)
    parts=[raw[i:i+16384] for i in range(0,len(raw),16384)]
    manifest={'schema':'tilelang_f9_numeric_chunks.v1','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'chunk_bytes':16384,
              'chunks':[{'index':i,'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for i,v in enumerate(parts)]}
    (OUT/'manifest.json').write_bytes(encode(manifest))
    ticks=[];rank_us=[];tails={}
    for row in numeric['ranks']:
        rank_us.append({'variant':row['variant'],'rank':row['rank'],'us':row['us']})
        for category in ('modules','functions','stages'):
            names=['F9.payload_clear'] if category=='stages' else sorted(row['tick_groups'][category])
            for name in names:
                value=row['tick_groups'][category][name]
                ticks.append([row['variant'],row['rank'],category,name,value['core_mean_ticks']['mean'],value['core_max_ticks']['mean'],value['core_max_ticks']['max'],sorted(set(value['participants_per_round']))])
    for variant,rows in numeric['round_tails'].items():
        tails[variant]={key:stats([r['rank_max_us'][key] for r in rows]) for key in rows[0]['rank_max_us']}
    verify_sources()
    assert sha(RUN/'summary.json')==parent_sha and sha(RUN/'worker-summary.json')==worker_sha
    result={'tokens':tokens,'status':'PASS','read_only_existing_evidence':True,'npu_launch':False,'revision':REV,
            'source_parent_summary_sha256':parent_sha,'source_worker_summary_sha256':worker_sha,
            'groups_reanalyzed':20,'rank_captures_reanalyzed':80,'json_normalized_equal':20,
            'clock':'NOT_VERIFIED','units':'ticks and ACL/msprof us separate; no conversion',
            'export_dir':str(OUT),'manifest':manifest,'cpu_comparisons':cpu,'ranks':rank_us,
            'tick_columns':['variant','rank','category','name','mean_ticks','round_core_max_mean_ticks','max_ticks','participant_counts'],
            'tick_rows':ticks,'round_rank_max_us':tails}
    (OUT/'readonly-validation.json').write_bytes(encode(result))
    compact=dict(result)
    compact.pop('cpu_comparisons');compact['numeric']={k:manifest[k] for k in ('bytes','sha256','chunk_bytes')}
    compact['numeric_chunk_count']=len(parts);compact.pop('manifest')
    # Equal-rank summaries for the first transfer; full rank/core numerical payload stays pinned in numeric.json.
    compact['rank_us_mean_columns']=list(rank_us[0]['us'])
    compact['rank_us_mean']=[[r['variant'],r['rank'],*[r['us'][k]['mean'] for k in compact['rank_us_mean_columns']]] for r in rank_us]
    compact.pop('ranks')
    aggregate=[]
    for variant in ('baseline','f9'):
        for category,name in sorted({(r[2],r[3]) for r in ticks}):
            rows=[r for r in ticks if r[0]==variant and r[2:4]==[category,name]]
            assert len(rows)==4
            aggregate.append([variant,category,name,sum(r[4] for r in rows)/4,sum(r[5] for r in rows)/4,max(r[6] for r in rows),sorted(set(n for r in rows for n in r[7]))])
    compact['tick_columns']=['variant','category','name','equal_rank_core_mean_ticks','equal_rank_round_core_max_mean_ticks','all_rank_round_max_ticks','participant_counts']
    compact['tick_rows']=aggregate
    return compact

if __name__=='__main__':
    sys.dont_write_bytecode=True
    result={'schema':'tilelang_f9_remaining_numeric_result.v1','status':'PASS','read_only':True,'npu_launch':False,
            'batches':[main_one(t) for t in (16,64)]}
    output=encode(result)
    assert len(output)<14000,'Use smaller saved projection; no runtime replay'
    print(output.decode())
