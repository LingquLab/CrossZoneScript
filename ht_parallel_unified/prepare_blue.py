"""Compile and validate the actual 464 B argument and per-rank prefill data on blue."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import zipfile

import config
import fixture
import route

HERE=Path(__file__).resolve().parent
SOURCE=Path(sys.argv[1]).resolve()
PATCHED=Path(sys.argv[2]).resolve()
SUPPORT=SOURCE/'tools/unitest/simulator/experiments/inter_server_send/sst'
sys.path[:0]=[str(SUPPORT)]
import loader
import native
import runtime

fixture.EXTRA=config.EXTRA
fixture.NAMES=config.NAMES
fixture.BLOCKS=config.BLOCKS
fixture.PRODUCER_QUEUE=config.PRODUCER_QUEUE
fixture.MAGIC=config.MAGIC
fixture.ROW=config.ROW
fixture.TOKENS=config.TOKENS
fixture.HIDDEN=config.HIDDEN
fixture.build_routes=route.build_routes
local=SOURCE/'src/moonep/combine_ht'
with tempfile.TemporaryDirectory(prefix='ht-parallel-blue-') as path:
    tmp=Path(path)
    subprocess.run(['c++','-std=c++17','-O2','-I'+str(SOURCE/'include'),
                    '-I'+str(local/'common'),'-I'+str(PATCHED/'src/ep/combine_ht'),
                    '-DTILEXR_COMBINE_HT_SST_FP32=1',HERE/'prefill.cpp',
                    local/'host/combine_ht_prefill.cpp','-o',tmp/'prefill'],check=True)
    subprocess.run(['c++','-std=c++17','-O2','-I'+str(SOURCE/'include'),SUPPORT/'abi_probe.cpp',
                    '-o',tmp/'abi'],check=True)
    with zipfile.ZipFile(HERE/'plans.zip','w',zipfile.ZIP_DEFLATED) as bundle:
        for rank in range(64):
            abi_raw=subprocess.check_output([tmp/'abi',str(rank),'4','256','8','8','8','2026','--reducers','8'])
            abi=json.loads(abi_raw)
            c=native.config_values(abi)
            images=runtime.runtime_images(abi,rank,1024*1024)
            (tmp/'mem').write_bytes(fixture.fullmesh(abi,rank,runtime,images)['fullmesh_mem'])
            subprocess.run([tmp/'prefill',str(rank),str(c['workspaceBytes']+8192),tmp/'mem',tmp/'plan'],check=True)
            raw,prefill=(tmp/'plan.args').read_bytes(),(tmp/'plan.bin').read_bytes()
            binding,golden,trace=fixture.build_rank(rank,abi,raw,prefill,(native,runtime))
            packed,_=loader.pack_arguments(binding['fields'],binding['abi_size'])
            assert len(packed)==464 and struct.unpack_from('<2I',packed,448)==(0,16)
            assert len(raw)==248 and struct.unpack_from('<I',packed,156)[0]==3
            local_base=trace['workspace']+c['workspaceBytes']+8192
            offset=trace['offset_map']['gather']
            for peer in range(8):
                if peer==rank%8:
                    continue
                peer_base=((rank&~7)+peer)*runtime.GM_BYTES+runtime.BASES['workspace']
                for token in range(4):
                    payload,count,signal,notify,_=struct.unpack_from('<3Q2I',prefill,512+(peer*4+token)*32)
                    assert count==notify==1
                    # Parse the canonical WQE source/destination with the ABI struct probe on either end.
                    row_offset=offset+(rank%8*4+token)*512
                    expected_dest=struct.pack('<Q',peer_base+row_offset)
                    expected_src=struct.pack('<Q',trace['workspace']+token*512)
                    assert expected_dest in prefill[payload:payload+48]
                    assert expected_src in prefill[payload+48:payload+64]
                    assert struct.pack('<Q',peer_base+trace['offset_map']['gather_ready']+(token*8+rank%8)*512) in prefill[signal:signal+48]
                    assert struct.pack('<Q',local_base+(3*64+peer)*512) in prefill[signal+48:signal+64]
            for name,data in (('abi.json',abi_raw),('args',raw),('prefill',prefill)):
                bundle.writestr(f'{rank}/{name}',data)
    print('Validated 64 argument packs (464 B each), 1792 payload and 1792 signal WQEs.')
    print(hashlib.sha256((HERE/'plans.zip').read_bytes()).hexdigest())
