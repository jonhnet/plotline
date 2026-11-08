#!/usr/bin/env python3
"""Read-only serial bridge status diagnostic; plot controls live in Plotline Studios."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('operation',choices=['status'])
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    destination = config['destination']
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.@:-]+',destination):
        raise ValueError('Invalid SSH destination')
    result = subprocess.run(['ssh','-T','-i',config['identity_file'],'-o','IdentitiesOnly=yes',
        '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
        '-o','UserKnownHostsFile='+config['known_hosts_file'],destination],
        input='{"protocol":2,"op":"status"}\n',capture_output=True,text=True,timeout=40,check=True)
    reply = json.loads(result.stdout)
    if not reply.get('ok'):
        raise RuntimeError(reply.get('error'))
    print(json.dumps(reply['result'],indent=2))


if __name__ == '__main__':
    main()
