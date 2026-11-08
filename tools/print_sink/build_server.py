#!/usr/bin/env python3
"""Build an isolated CUPS IPP tool; never install/replace the system scheduler."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
VERSION = '2.4.20'
SHA256 = 'ab4d9cd7f3e58060091d2b24972223d6401675f11c49b65abf4f6ef31dea22ff'
URL = f'https://github.com/OpenPrinting/cups/releases/download/v{VERSION}/cups-{VERSION}-source.tar.gz'


def reusable(binary, receipt, identity):
    try:
        saved = json.loads(receipt.read_text())
        return (all(saved.get(key)==value for key,value in identity.items())
                and saved.get('binary_sha256')==hashlib.sha256(binary.read_bytes()).hexdigest())
    except (OSError, ValueError, AttributeError):
        return False


def build():
    directory = Path(os.environ.get('PLOTLINE_IPP_BUILD_DIR',str(ROOT/'build/print-sink')))
    directory.mkdir(parents=True, exist_ok=True)
    patch = Path(__file__).with_name('pdf-format-option.patch')
    maintenance = Path(__file__).with_name('maintenance.patch')
    identity = dict(cups=VERSION, source_sha256=SHA256,
                    patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
                    maintenance_sha256=hashlib.sha256(maintenance.read_bytes()).hexdigest(),
                    builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    environment=os.environ.get('PLOTLINE_BUILD_ENVIRONMENT',''))
    binary, receipt = directory/'ippeveprinter', directory/'build.json'
    if reusable(binary, receipt, identity):
        print('Reusing IPP server:',binary)
        return
    archive = directory/f'cups-{VERSION}.tar.gz'
    if not archive.exists():
        temporary = archive.with_suffix('.download')
        with urllib.request.urlopen(URL, timeout=60) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        temporary.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError(f'Incorrect CUPS source checksum: {archive}')
    source = directory/f'cups-{VERSION}'
    if source.exists():
        shutil.rmtree(source)
    with tarfile.open(archive) as bundle:
        bundle.extractall(directory, filter='data')
    with (directory/'build.log').open('w') as log:
        for command in [
            ['patch', '-p1', '-i', str(patch)],
            ['patch', '-p1', '-i', str(maintenance)],
            ['./configure', '--disable-shared', '--disable-pam', '--disable-libusb',
             '--with-dnssd=avahi', '--with-tls=openssl'],
            ['make', '-C', 'cups', f'-j{min(os.cpu_count() or 1, int(os.environ.get("PLOTLINE_BUILD_JOBS","4")))}', 'libcups.a'],
            ['make', '-C', 'tools', 'ippeveprinter'],
        ]:
            subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True)
    temporary = binary.with_suffix('.new')
    shutil.copy2(source/'tools/ippeveprinter', temporary)
    temporary.replace(binary)
    receipt.write_text(json.dumps(dict(identity,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest()), indent=2)+'\n')
    print(binary)


if __name__ == '__main__':
    build()
