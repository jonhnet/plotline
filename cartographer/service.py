"""Dev-only cartography HTTP service. Run: python -m cartographer.service."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import html
import json
import mimetypes
from pathlib import Path
import re
import threading
import uuid
from urllib.parse import urlsplit, parse_qs

from jsonschema import ValidationError
from PIL import Image

from . import ROOT
from .client import PrintClient
from .compose import fit_recipe
from .jobs import Jobs
from .printing import PrintTasks
from .preparing import Preparations
from .model import canonical, catalog, validate_recipe, migrate
from .sources import source_version, tile_source
from . import boundaries
from .storage import Conflict, Recipes, identifier
from .retention import Retention, preserve_sources, touch

ARTIFACTS = {'map.pdf', 'preview.png', 'recipe.json', 'provenance.json', 'map.print-intent.json', 'map-bundle.zip', 'scene.json', 'scene-detail.json'}


class Application:
    def __init__(self, state, build, studios, studios_public=None):
        self.state, self.build = Path(state).resolve(), Path(build).resolve()
        self.cache_lock = threading.RLock()
        preserve_sources(self.state,self.build)
        self.version = source_version()
        self.vector_template = tile_source(self.build/'geometry')
        self.recipes = Recipes(self.state/'recipes')
        self.jobs = Jobs(self.build, self.version)
        self.print_client = PrintClient(studios, self.state, studios_public)
        self.print_tasks = PrintTasks(self.state,self.build/'exports',self.print_client)
        self.preparations = Preparations(self.state,self.jobs,self.print_tasks)
        self.retention = Retention(self)
        self.thumbnail_stop=threading.Event()
        self.thumbnail_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='saved-map-thumbnail')
        for row in self.recipes.list():
            value=self.recipes.get(row['id'])
            if value.get('thumbnail_pending') and not value['thumbnail_url']:self.queue_thumbnail(value)

    def queue_thumbnail(self,value):
        if not value['thumbnail_url']:self.thumbnail_pool.submit(self.finish_thumbnail,value)

    def finish_thumbnail(self,value):
        session='thumbnail-'+uuid.uuid4().hex;job=None
        try:
            # Give an already-running browser preview a chance to supply its
            # thumbnail first. Save itself never waits for another rendering.
            if self.thumbnail_stop.wait(2):return
            if self.recipes.get(value['id'],value['revision'])['thumbnail_url']:return
            job=self.jobs.submit(value['recipe'],'draft',session,1)
            while not self.thumbnail_stop.is_set():
                if self.recipes.get(value['id'],value['revision'])['thumbnail_url']:return
                job=self.jobs.public(job['id'])
                if job['status']=='ready':
                    directory=self.jobs.export(job['id'],session,1,value['recipe_hash'])
                    with Image.open(directory/'preview.png') as image:
                        image.thumbnail((384,288));output=io.BytesIO();image.convert('RGB').save(output,format='JPEG',quality=85)
                    thumbnail='data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode()
                    self.recipes.set_thumbnail(value['id'],value['revision'],value['recipe_hash'],thumbnail)
                    return
                if job['status'] in ('error','cancelled'):
                    raise ValueError(job.get('error','Thumbnail preview was cancelled'))
                if self.thumbnail_stop.wait(.5):return
        except (OSError,ValueError,KeyError) as error:
            print(f'Saved thumbnail {value["id"]}/{value["revision"]}: {error}',flush=True)
        finally:
            if job and job['status'] in ('queued','running'):
                try:self.jobs.cancel(session,2)
                except (ValueError,FileNotFoundError):pass

    def close_thumbnails(self):
        self.retention.close();self.thumbnail_stop.set();self.thumbnail_pool.shutdown(wait=True,cancel_futures=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'PlotlineCartographer/1'

    @property
    def app(self):
        return self.server.app

    def json(self, value, status=200):
        self.send_data(canonical(value), 'application/json; charset=utf-8', status)

    def send_data(self, data, mime, status=200, filename=None, immutable=False):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'public, max-age=31536000, immutable' if immutable else 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header('Content-Disposition', 'attachment; filename="'+filename+'"')
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        # This dev service is reachable on the private guest. Browser writes
        # require same-origin JSON; it has no device-control endpoints.
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            raise PermissionError('Cross-origin writes are not accepted')
        if self.headers.get_content_type() != 'application/json':
            raise ValueError('Expected application/json')
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 8_000_000:
            raise ValueError('Request must contain at most 8 MB of JSON')
        value = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object')
        return value

    def do_GET(self):
        self.handle_request('GET')

    def do_POST(self):
        self.handle_request('POST')

    def handle_request(self, method):
        with self.app.cache_lock:
            self.dispatch(method)

    def dispatch(self, method):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if method == 'GET':
                if path == '/api/catalog':
                    value=catalog();value['recipe']['data']['vector_template']=self.app.vector_template
                    return self.json(dict(value, composer_version=self.app.version))
                if path.startswith('/api/source-recipes/'):
                    key=path.split('/')[-1]
                    if not re.fullmatch('[0-9a-f]{64}',key):raise ValueError('Invalid source recipe')
                    try:return self.json(self.app.preparations.load(key)['recipe'])
                    except FileNotFoundError:
                        for result in [*(self.app.build/'exports').glob('*/result.json'), *(self.app.state/'export-recipes').glob('*/result.json')]:
                            if json.loads(result.read_text()).get('recipe_hash')==key:
                                return self.json(json.loads((result.parent/'recipe.json').read_text()))
                        raise FileNotFoundError('The source map snapshot is unavailable')
                if path in ('/inksets.js','/inksets.css','/preview-navigation.js','/studio.js','/studio.css','/studio-navigation.js','/paper-units.js'):
                    file=ROOT/'shared/web'/path[1:]
                    return self.send_data(file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'text/plain')
                if path == '/api/capabilities':
                    return self.json(self.app.print_client.inventory())
                if path == '/api/recipes':
                    return self.json(self.app.recipes.list())
                if re.fullmatch(r'/api/recipes/[a-zA-Z0-9_-]+/thumbnail',path):
                    revision=parse_qs(parsed.query).get('revision',[None])[0]
                    return self.send_data(self.app.recipes.thumbnail(path.split('/')[-2],int(revision) if revision else None),'image/jpeg')
                if path.startswith('/api/recipes/'):
                    revision = parse_qs(parsed.query).get('revision', [None])[0]
                    value=self.app.recipes.get(path.split('/')[-1], int(revision) if revision else None)
                    value['recipe']=migrate(value['recipe'])
                    return self.json(value)
                if path.startswith('/api/jobs/'):
                    return self.json(self.app.jobs.public(identifier(path.split('/')[-1])))
                if path.startswith('/api/print-tasks/'):
                    return self.json(self.app.print_tasks.public(path.split('/')[-1]))
                if path.startswith('/api/preparations/'):
                    return self.json(self.app.preparations.public(path.split('/')[-1]))
                if path.startswith('/api/scenes/'):
                    identity=identifier(path.split('/')[-1])
                    job=self.app.jobs.public(identity)
                    directory=self.app.build/'exports'/job['cache_key'] if job['status']=='ready' else self.app.build/'tasks'/identity/'output'
                    detail=parse_qs(parsed.query).get('revision',['1'])[0]=='2'
                    return self.send_data((directory/('scene-detail.json' if detail else 'scene.json')).read_bytes(),'application/json')
                if path.startswith('/artifacts/'):
                    pieces = path.split('/')
                    if len(pieces) != 4 or not re.fullmatch('[0-9a-f]{64}', pieces[2]) or pieces[3] not in ARTIFACTS:
                        raise FileNotFoundError('Artifact not found')
                    directory = self.app.build/'exports'/pieces[2]
                    if not (directory/'result.json').exists():
                        raise FileNotFoundError('Export is incomplete')
                    file = directory/pieces[3]
                    touch(directory)
                    return self.send_data(file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'application/octet-stream',
                                          filename=None if file.suffix == '.png' else file.name, immutable=True)
                static = {'/': 'index.html', '/app.js': 'app.js', '/paper.js': 'paper.js', '/style.css': 'style.css'}
                if path in static:
                    file = ROOT/'cartographer/web'/static[path]
                    data=file.read_bytes()
                    if path=='/':
                        public=getattr(getattr(self.app,'print_client',None),'public_server',None)
                        if self.headers.get('X-Forwarded-Prefix')=='/map':
                            host=urlsplit('http://'+self.headers.get('Host','localhost')).hostname
                            host=('['+host+']') if ':' in host else host
                            legacy='http://'+host+':'+str(self.server.server_port)
                            data=data.replace(b'</head>',('<meta name="plotline-legacy-map-origin" content="'+html.escape(legacy,quote=True)+'"></head>').encode())
                        elif public:
                            data=data.replace(b'</head>',('<meta name="plotline-root" content="'+html.escape(public,quote=True)+'"></head>').encode())
                    if path=='/' and self.headers.get('X-Forwarded-Prefix')=='/map':
                        data=data.replace(b'href="/style.css"',b'href="/map/style.css"').replace(b'src="/app.js"',b'src="/map/app.js"')
                    return self.send_data(data, mimetypes.guess_type(file.name)[0] or 'text/plain')
            else:
                body = self.body()
                if path == '/api/recipes':
                    value=self.app.recipes.save(body['recipe'], body.get('id'), body.get('expected_revision'),body.get('name'),body.get('thumbnail'))
                    self.app.queue_thumbnail(value)
                    return self.json(value,201)
                if re.fullmatch(r'/api/recipes/[a-zA-Z0-9_-]+/thumbnail',path):
                    return self.json(self.app.recipes.set_thumbnail(path.split('/')[-2],body['revision'],body['recipe_hash'],body['thumbnail']))
                if path == '/api/validate':
                    value=migrate(body['recipe']);validate_recipe(value)
                    return self.json(dict(valid=True,recipe=value))
                if path == '/api/boundaries/search':
                    return self.json(boundaries.search(body['name'],body['kind'],self.app.build/'geometry'))
                if path == '/api/boundaries/select':
                    return self.json(boundaries.select(body['kind'],body['id'],self.app.build/'geometry'))
                if path == '/api/boundaries/import':
                    return self.json(boundaries.snapshot(body['geometry'],body['name'],'Imported GeoJSON'))
                if path == '/api/fit':
                    return self.json(fit_recipe(body['recipe']))
                if path == '/api/preview':
                    return self.json(self.app.jobs.submit(body['recipe'], body['quality'], body['session'], body['revision']), 202)
                if path == '/api/prepare':
                    retry=body.get('retry',False)
                    if type(retry) is not bool:raise ValueError('Retry must be true or false')
                    rebuild=body.get('rebuild',False)
                    if type(rebuild) is not bool:raise ValueError('Rebuild must be true or false')
                    origin=body.get('source_document')
                    if origin:
                        saved=self.app.recipes.get(origin['id'],origin.get('revision'))
                        if origin.get('revision') and saved['recipe']!=body['recipe']:raise ValueError('Source revision does not match this map')
                        origin={'id':saved['id'],**({'revision':saved['revision']} if origin.get('revision') else {})}
                    return self.json(self.app.preparations.request(body['recipe'],retry=retry,rebuild=rebuild,source_document=origin),202)
                if path == '/api/cancel':
                    self.app.jobs.cancel(body['session'], body['revision'])
                    return self.json(dict(status='cancelled'))
                if re.fullmatch(r'/api/print-tasks/[0-9a-f]{64}/retry',path):
                    return self.json(self.app.print_tasks.retry(path.split('/')[-2],body['recipe_hash']),202)
                if re.fullmatch(r'/api/jobs/[a-zA-Z0-9_-]+/submit', path):
                    identity = path.split('/')[-2]
                    directory = self.app.jobs.export(identity, body['session'], body['revision'], body['recipe_hash'])
                    intent_file = directory/'map.print-intent.json'
                    if not intent_file.exists():
                        raise ValueError('Measure unknown ink diameters before sending a print intent')
                    retry=body.get('retry',False)
                    if type(retry) is not bool:raise ValueError('Retry must be true or false')
                    return self.json(self.app.print_tasks.request(directory,body['recipe_hash'],retry=retry),202)
            raise FileNotFoundError('Not found')
        except (BrokenPipeError, ConnectionResetError):
            return
        except Conflict as error:
            self.json(dict(error=str(error)), 409)
        except FileNotFoundError as error:
            self.json(dict(error=str(error)), 404)
        except PermissionError as error:
            self.json(dict(error=str(error)), 403)
        except (ValueError, KeyError, TypeError, ValidationError) as error:
            self.json(dict(error=str(error).split('\n')[0]), 422)
        except OSError as error:
            self.json(dict(error='Service request failed: '+str(error)), 502)

    def log_message(self, format, *args):
        if '/api/jobs/' not in self.path:
            super().log_message(format, *args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=9193)
    parser.add_argument('--state', type=Path, default=ROOT/'state/cartographer')
    parser.add_argument('--build', type=Path, default=ROOT/'build/cartographer')
    parser.add_argument('--studios', dest='studios', default='http://127.0.0.1:9191')
    parser.add_argument('--studios-public', dest='studios_public', default='http://dev.iac.jonh.net:9191')
    args = parser.parse_args()
    app = Application(args.state, args.build, args.studios, args.studios_public)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app
    print(f'Plotline map studio: http://{args.host}:{args.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close(); app.retention.close(); app.close_thumbnails(); app.preparations.close(); app.print_tasks.close(); app.jobs.close()


if __name__ == '__main__':
    main()
