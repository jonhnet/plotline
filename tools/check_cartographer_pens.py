#!/usr/bin/env python3
"""Browser pen-workflow acceptance against an isolated store; no map fetches or plotter."""
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from cartographer.service import Handler
from cartographer.storage import Recipes
from cartographer.model import VECTOR_SOURCE


def main():
    state=Path(tempfile.mkdtemp(prefix='pen-browser-',dir=ROOT/'build'))
    class Quiet(Handler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Quiet)
    server.app=SimpleNamespace(recipes=Recipes(state/'recipes'),version='document-fixture',vector_template=VECTOR_SOURCE,queue_thumbnail=lambda _:None,cache_lock=threading.RLock())
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        subprocess.run(['node',str(ROOT/'tools/check_cartographer_pens.cjs'),f'http://127.0.0.1:{server.server_port}',str(state)],
                       cwd=ROOT,env=dict(os.environ,NODE_PATH='/root/plotline/controller/node_modules'),check=True)
    finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
