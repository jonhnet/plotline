#!/usr/bin/env python3
"""Isolated Map document HTTP fixture for cross-studio navigation acceptance."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from cartographer.service import Handler
from cartographer.storage import Recipes
from cartographer.model import VECTOR_SOURCE
state=Path(sys.argv[1]);state.mkdir(parents=True,exist_ok=True)
class Quiet(Handler):
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Quiet)
server.app=SimpleNamespace(cache_lock=threading.RLock(),recipes=Recipes(state/'recipes'),print_client=SimpleNamespace(public_server=sys.argv[2] if len(sys.argv)>2 else None),version='navigation-fixture',vector_template=VECTOR_SOURCE,queue_thumbnail=lambda _:None,
    preparations=SimpleNamespace(load=lambda key:dict(recipe=json.loads((state/(key+'.json')).read_text()))),build=state)
print(server.server_port,flush=True)
server.serve_forever()
