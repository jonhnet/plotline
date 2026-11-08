"""Development lab with local notes. Never serves private source URL files."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
import re

from .comments import Comments, CommentError

WEB = Path(__file__).resolve().parent / 'web'


def handler(output, state):
    comments = Comments(state, output)
    class Handler(BaseHTTPRequestHandler):
        def json_response(self, value, status=200):
            content = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(content)

        def read_json(self):
            origin = self.headers.get('Origin')
            if origin and (urlsplit(origin).scheme not in ('http', 'https') or urlsplit(origin).netloc != self.headers.get('Host')):
                raise CommentError('Cross-origin writes are not allowed', 403)
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise CommentError('Cross-origin writes are not allowed', 403)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise CommentError('Expected application/json', 415)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 24000:
                    raise CommentError('Comment request is too large or empty', 413)
                return json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError) as error:
                if isinstance(error, CommentError):
                    raise
                raise CommentError('Invalid JSON request') from None

        def do_POST(self):
            if urlsplit(self.path).path != '/api/comments':
                self.send_error(404)
                return
            try:
                self.json_response(comments.add(self.read_json()), 201)
            except CommentError as error:
                self.json_response(dict(error=str(error)), error.status)

        def do_PATCH(self):
            match = re.fullmatch(r'/api/comments/([a-f0-9-]{36})', urlsplit(self.path).path)
            if not match:
                self.send_error(404)
                return
            try:
                self.json_response(comments.edit(match[1], self.read_json()))
            except CommentError as error:
                self.json_response(dict(error=str(error)), error.status)

        def do_GET(self):
            raw = unquote(urlsplit(self.path).path)
            if raw == '/api/comments':
                self.json_response(dict(comments=comments.list()))
                return
            if raw == '/':
                path = WEB / 'index.html'
            elif raw.startswith('/comment-samples/'):
                match = re.fullmatch(r'/comment-samples/([a-f0-9]{20}(?:-source)?)/(preview.png|paths.svg|result.json)', raw)
                if not match:
                    self.send_error(404)
                    return
                path = comments.samples / match[1] / match[2]
            elif raw.startswith('/artifacts/'):
                relative = Path(raw.removeprefix('/artifacts/'))
                path = (output / relative).resolve()
                if not path.is_relative_to(output.resolve()) or path.suffix not in ('.json', '.png', '.svg'):
                    self.send_error(404)
                    return
            elif raw.startswith('/originals/'):
                name = raw.removeprefix('/originals/')
                if '/' in name or not name.endswith(('.jpg', '.png')):
                    self.send_error(404)
                    return
                path = state / 'inputs' / name
            elif raw in ('/app.js', '/notes.js', '/style.css'):
                path = WEB / raw[1:]
            else:
                self.send_error(404)
                return
            if not path.is_file():
                self.send_error(404)
                return
            content = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=9194, type=int)
    args = parser.parse_args()
    with ThreadingHTTPServer((args.host, args.port), handler(args.output, args.state)) as server:
        print(f'Raster lab listening on {args.host}:{args.port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
