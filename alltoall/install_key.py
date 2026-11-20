"""Authenticate, install only a green-local public key, verify key auth, inventory CPU simulator."""
import json
import os
from pathlib import Path
import subprocess
from ssh_transport import run


def main():
    result = {'host': '141.61.94.58', 'npu_used': False}
    result['password_login'] = run('import json,socket; print(json.dumps({"hostname":socket.gethostname()}))', password=True)
    roots = []
    for p in [Path.home(), Path(os.environ.get('USERPROFILE', str(Path.home()))),
              Path(os.environ.get('HOME', str(Path.home())))]:
        if p not in roots:
            roots.append(p)
    identity = next((r / '.ssh' / name for r in roots for name in
                     ('id_ed25519', 'id_rsa', 'id_ed25519_tilexr_sim')
                     if (r / '.ssh' / name).is_file()
                     and (r / '.ssh' / (name + '.pub')).is_file()), None)
    generated = identity is None
    if generated:
        identity = roots[0] / '.ssh' / 'id_ed25519_tilexr_sim'
        identity.parent.mkdir(mode=0o700, exist_ok=True)
        if identity.exists() or Path(str(identity) + '.pub').exists():
            raise RuntimeError('Task key path partially exists; refusing overwrite')
        subprocess.run(['ssh-keygen', '-t', 'ed25519', '-N', '', '-C', 'tilexr-cpu-simulation',
                        '-f', str(identity)], capture_output=True, check=True, timeout=15)
    public = Path(str(identity) + '.pub')
    key = public.read_text(encoding='utf-8').strip()
    fields = key.split()
    if len(fields) < 2 or fields[0] not in ('ssh-ed25519', 'ssh-rsa',
                                         'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'):
        raise RuntimeError('Unsupported public key format')
    fp = subprocess.run(['ssh-keygen', '-lf', str(public)], capture_output=True, check=True, timeout=10)
    result.update(local_public_key=str(public), key_generated=generated,
                  fingerprint=fp.stdout.decode('utf-8', 'replace').split()[1])
    install = '''import json,os
from pathlib import Path
key=json.load(__import__('sys').stdin)['public_key']
parts=key.split()
assert len(parts)>=2 and '\\n' not in key and '\\r' not in key
directory=Path.home()/'.ssh'
directory.mkdir(mode=0o700,exist_ok=True)
target=directory/'authorized_keys'
assert not directory.is_symlink() and not target.is_symlink()
existing=target.read_bytes() if target.exists() else b''
present=False
for line in existing.decode('utf-8').splitlines():
    tokens=line.split()
    present=present or any(tokens[i:i+2]==parts[:2] for i in range(len(tokens)-1))
if not present:
    prefix=b'\\n' if existing and not existing.endswith(b'\\n') else b''
    fd=os.open(str(target),os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
    with os.fdopen(fd,'ab') as stream: stream.write(prefix+key.encode('ascii')+b'\\n')
directory.chmod(0o700)
target.chmod(0o600)
print(json.dumps({'key_added':not present,'key_present':True,'authorized_keys':str(target)}))
'''
    # Only the public key crosses SSH stdin. The private key stays on Windows.
    result['installation'] = run(install, data={'public_key': key}, password=True)
    source = Path(__file__).with_name('inspect_host_remote.py').read_text(encoding='utf-8')
    result['key_auth_inventory'] = run(source, identity=identity)
    result['verified_key_auth'] = True
    Path('alltoall-key-install-result.json').write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding='ascii')
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
