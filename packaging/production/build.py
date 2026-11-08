"""Fresh Ubuntu build, using pinned language tools and the committed lockfiles."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tarfile
import urllib.request
from common import atomic, run

NODE = '22.23.2'
RUST = '1.94.1'
RUSTUP = '1.28.2'
PACKAGES = '''build-essential pkg-config patch ca-certificates curl python3-dev python3-venv
python3-pil python3-numpy python3-scipy python3-skimage python3-shapely python3-opencv
python3-cairo inkscape fonts-dejavu-core mupdf-tools openssh-client libgeos-dev
libavahi-client-dev avahi-daemon avahi-utils libssl-dev zlib1g-dev cups-ipp-utils
cups-filters time'''.split()


def download(url, target, sha):
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == sha: return
    temporary = target.with_suffix('.download')
    with urllib.request.urlopen(url, timeout=60) as incoming, temporary.open('wb') as output:
        shutil.copyfileobj(incoming, output)
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != sha:
        temporary.unlink(); raise ValueError('Download checksum mismatch: ' + url)
    temporary.replace(target)


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_symlink(): raise ValueError('Exported source must not contain symlinks: ' + str(path))
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode()); digest.update(b'\0')
            digest.update(path.read_bytes())
    return digest.hexdigest()


def dependencies(c):
    release = Path('/etc/os-release').read_text()
    if 'ID=ubuntu\n' not in release or 'VERSION_ID="24.04"' not in release:
        raise ValueError('Supported production guest: Ubuntu 24.04 LTS')
    if os.uname().machine != 'x86_64': raise ValueError('This installer currently supports x86-64 guests')
    missing = [name for name in PACKAGES if subprocess.run(['dpkg-query','-W','-f=${Status}',name],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout != 'install ok installed']
    if missing:
        run('apt-get','update')
        run('apt-get','install','-y','--no-install-recommends',*missing,
            env=dict(os.environ,DEBIAN_FRONTEND='noninteractive',NEEDRESTART_MODE='l'))
    tools = Path(c['install_root'])/'toolchains'; tools.mkdir(parents=True,exist_ok=True)
    node = tools/f'node-v{NODE}-linux-x64'
    if not (node/'bin/node').exists():
        filename = node.name+'.tar.xz'; base = f'https://nodejs.org/dist/v{NODE}/'
        with urllib.request.urlopen(base+'SHASUMS256.txt', timeout=30) as response:
            rows = response.read().decode().splitlines()
        sha = next(line.split()[0] for line in rows if line.split()[-1] == filename)
        archive = tools/filename; download(base+filename,archive,sha)
        with tarfile.open(archive) as bundle: bundle.extractall(tools,filter='data')
        archive.unlink()
    cargo = tools/'cargo'; rustup = tools/'rustup'
    for directory in (cargo,rustup): directory.mkdir(exist_ok=True); shutil.chown(directory,user='plotline-build')
    env = dict(os.environ,CARGO_HOME=str(cargo),RUSTUP_HOME=str(rustup),
               PATH=f'{cargo}/bin:{node}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
               CARGO_BUILD_JOBS=str(c['build_jobs']),PYTHONDONTWRITEBYTECODE='1')
    if not (cargo/'bin/rustup').exists():
        base = f'https://static.rust-lang.org/rustup/archive/{RUSTUP}/x86_64-unknown-linux-gnu/rustup-init'
        with urllib.request.urlopen(base+'.sha256',timeout=30) as response: sha=response.read().decode().split()[0]
        binary = tools/'rustup-init'; download(base,binary,sha); binary.chmod(0o755)
        run('runuser','-u','plotline-build','--',binary,'-y','--no-modify-path','--profile','minimal','--default-toolchain',RUST,env=env)
        binary.unlink()
    run('runuser','-u','plotline-build','--','rustup','toolchain','install',RUST,'--profile','minimal',env=env)
    run('runuser','-u','plotline-build','--','rustup','default',RUST,env=env)
    env['RUSTUP_TOOLCHAIN'] = RUST
    return env


def build(source, revision, c, env):
    root = Path(c['install_root'])/'builds'/revision
    fingerprint = tree_hash(source); receipt = root/'installed-build.json'
    if receipt.exists():
        prior = json.loads(receipt.read_text())
        if prior['source_sha256'] != fingerprint: raise ValueError('The same revision has different source bytes')
        if all((root/p).exists() for p in ['controller/target/release/plotline-studios','target/release/plotline','runtime/ippeveprinter']):
            return root
    if root.exists():
        current = Path(c['install_root'])/'current'
        if current.exists() and current.resolve() == root.resolve():
            raise ValueError('Active build is incomplete; restore its files or publish a different revision')
        shutil.rmtree(root)
    shutil.copytree(source,root)
    owner = pwd.getpwnam('plotline-build')
    for directory, dirs, files in os.walk(root):
        os.chown(directory,owner.pw_uid,owner.pw_gid)
        for name in files: os.chown(Path(directory)/name,owner.pw_uid,owner.pw_gid)
    target = root/'.cargo-target'
    build_env = dict(env,CARGO_TARGET_DIR=str(target))
    commands = [
        ['npm','ci','--ignore-scripts','--no-audit','--no-fund'],
        ['python3','-m','venv','.venv'],
        ['.venv/bin/pip','install','-r','requirements-map.txt','-r','requirements-contracts.txt','-r','requirements-cartographer.txt'],
        ['.venv/bin/pip','check'],
        ['python3','-m','venv','.calibration'],
        ['.calibration/bin/pip','install','-r','requirements-calibration.txt'],
        ['.calibration/bin/pip','check'],
        ['cargo','build','--locked','--release'],
        ['cargo','build','--locked','--release','--manifest-path','controller/Cargo.toml'],
        ['.venv/bin/python','tools/build_cartographer_geometry.py'],
        ['python3','tools/print_sink/build_server.py'],
        ['python3','-c','from raster_lab.engine import check_dependencies; check_dependencies()'],
    ]
    for command in commands:
        run('runuser','-u','plotline-build','--',*command,cwd=root,env=build_env)
    for name,destination in [('plotline','target/release'),('plotline-studios','controller/target/release')]:
        (root/destination).mkdir(parents=True,exist_ok=True)
        shutil.copy2(target/'release'/name,root/destination/name)
    (root/'runtime').mkdir()
    for source_name,target_name in [('ippeveprinter','ippeveprinter'),('build.json','ipp-build.json')]:
        shutil.copy2(root/'build/print-sink'/source_name,root/'runtime'/target_name)
    shutil.rmtree(target)
    shutil.rmtree(root/'build')
    cache = Path(c['data_root'])/'cache/build'; (root/'build').symlink_to(cache)
    versions = run('dpkg-query','-W','-f=${Package}=${Version}\n',*PACKAGES,stdout=subprocess.PIPE,text=True).stdout
    atomic(receipt,dict(revision=revision,source_sha256=fingerprint,node=NODE,rust=RUST,
                        distribution_packages=versions.splitlines(),state_schema=1))
    # The runtime account can write retained state, never executable source.
    for directory, dirs, files in os.walk(root,followlinks=False):
        os.chown(directory,0,0)
        for name in files:
            path=Path(directory)/name
            if not path.is_symlink(): os.chown(path,0,0)
    return root
