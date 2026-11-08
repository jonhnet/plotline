#!/usr/bin/env python3
"""Build the future root README and serve a read-only local editorial preview."""
import argparse
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import mimetypes
from markdown_it import MarkdownIt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = ROOT/'build/readme-preview'
MARKDOWN = MarkdownIt('commonmark', {'html': True}).enable('table')
TOKEN = re.compile(r'\{\{(sample|links):([a-z0-9-]+)\}\}')


def sample_picture(key, sample):
    prefix = f'docs/readme/examples/{key}/'
    source = prefix + sample.get('image', 'awaiting.svg')
    target = prefix + sample['video'] if sample.get('video') else source
    return f'<a href="{html.escape(target, quote=True)}"><img src="{html.escape(source, quote=True)}" alt="{html.escape(sample["title"], quote=True)}" width="440"></a>'


def sample_links(key, sample):
    links = []
    for label, filename in sample['files']:
        path = (HERE/'examples'/key/filename).resolve()
        if not path.is_relative_to(HERE/'examples') or not path.is_file():
            raise ValueError(f'Missing or unsafe example input: {key}/{filename}')
        links.append(f'[{label}]({path.relative_to(ROOT).as_posix()})')
    return ' · '.join(links)


def samples():
    return json.loads((HERE/'samples.json').read_text())


def placeholder(key, sample):
    label = 'Film the plotter in action' if sample['kind'] == 'awaiting-video' else 'Awaiting a photograph of the physical plot'
    # A labeled empty slot, never a synthetic or simulated finished print.
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="260" viewBox="0 0 1000 260"><rect x="1" y="1" width="998" height="258" rx="6" fill="#faf9f5" stroke="#d8ddd3"/><text x="500" y="118" text-anchor="middle" font-family="sans-serif" font-size="24" fill="#334b40">{html.escape(sample['title'])}</text><text x="500" y="155" text-anchor="middle" font-family="sans-serif" font-size="17" fill="#737b73">{label}</text></svg>'''


def compile_markdown():
    entries = samples()
    template = (HERE/'README.template.md').read_text()
    def expand(match):
        kind, key = match[1], match[2]
        sample = entries[key]
        ending = f'\n\n<!-- /sample:{key} -->'
        if kind == 'links':
            return sample_links(key, sample) + ending
        directory = HERE/'examples'/key
        image = sample.get('image', 'awaiting.svg')
        if not sample.get('image'):
            directory.mkdir(parents=True, exist_ok=True)
            data = placeholder(key, sample)
            path = directory/image
            if not path.exists() or path.read_text() != data:
                path.write_text(data)
        elif not (directory/image).is_file():
            raise ValueError(f'Missing sample image: {key}/{image}')
        for field in ('video', 'poster'):
            if sample.get(field):
                asset = (directory/sample[field]).resolve()
                if not asset.is_relative_to(HERE/'examples') or not asset.is_file():
                    raise ValueError(f'Missing or unsafe sample {field}: {key}')
        result = f'<!-- sample:{key} -->\n\n' + sample_picture(key, sample) + '\n\n' + sample['caption']
        if '{{links:' + key + '}}' in template:
            return result
        links = sample_links(key, sample)
        return result + ('\n\n' + links if links else '') + ending
    return TOKEN.sub(expand, template)


def verify():
    """Check frozen bytes and every local link in the actual GitHub Markdown."""
    text = compile_markdown()
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls = []

        def handle_starttag(self, tag, attrs):
            self.urls.extend(value for key, value in attrs if key in ('src', 'href') and value)

    links = Links()
    links.feed(MARKDOWN.render(text))
    for link in links.urls:
        if urlsplit(link).scheme or link.startswith('#'):
            continue
        path = (ROOT/urlsplit(link).path).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError(f'Broken local README link: {link}')
    for manifest in (HERE/'examples').rglob('provenance.json'):
        for name, info in json.loads(manifest.read_text()).get('files', {}).items():
            path = (manifest.parent/name).resolve()
            if not path.is_relative_to(HERE/'examples'):
                raise ValueError(f'Unsafe provenance path: {name}')
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != info['sha256']:
                raise ValueError(f'Archived input changed: {path}')
    for path in (HERE/'examples').rglob('print-intent.json'):
        intent = json.loads(path.read_text())
        with (path.parent/'source.pdf').open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != intent['source_sha256']:
            raise ValueError(f'PDF / print-intent mismatch: {path.parent}')
    return text


def page(text, notes=False):
    content = MARKDOWN.render(text)
    entries = samples()
    def frame(match):
        key, body = match[1], match[2]
        sample = entries[key]
        kind = 'still'
        if sample.get('video'):
            kind = 'motion'
            prefix = f'docs/readme/examples/{key}/'
            body = body.replace(sample_picture(key, sample), f'<video class="sample-video" controls muted loop playsinline preload="metadata" poster="/{prefix}{sample["poster"]}"><source src="/{prefix}{sample["video"]}" type="video/mp4"></video>')
        picture, caption = body.split('</p>', 1)
        return f'<figure class="sample sample-{kind}" data-sample="{key}">{picture}</p><figcaption>{caption}</figcaption></figure>'
    content = re.sub(r'<!-- sample:([a-z0-9-]+) -->\s*(.*?)\s*<!-- /sample:\1 -->', frame, content, flags=re.S)
    base = '<base href="/docs/readme/">' if notes else ''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">{base}<meta name="viewport" content="width=device-width,initial-scale=1"><title>Plotline Studios · README draft</title><link rel="stylesheet" href="/preview.css"></head><body><nav class="draft-bar"><strong>README draft · private review</strong><a href="/">Draft</a><a href="/notes">Capture plan</a><a href="/README.md">Markdown ↓</a></nav><main class="markdown-body">{content}</main><script src="/preview.js" defer></script></body></html>'''


def version():
    files = [HERE/'README.template.md', HERE/'samples.json', HERE/'WORKLOG.md', HERE/'preview.css', HERE/'preview.js',
             *(HERE/'examples').rglob('*')]
    return hashlib.sha256('\n'.join(f'{p}:{p.stat().st_mtime_ns}:{p.stat().st_size}' for p in files if p.is_file()).encode()).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        try:
            if path == '/':
                return self.reply(page(compile_markdown()).encode(), 'text/html; charset=utf-8')
            if path == '/notes':
                return self.reply(page((HERE/'WORKLOG.md').read_text(), True).encode(), 'text/html; charset=utf-8')
            if path == '/__version':
                return self.reply(version().encode(), 'text/plain')
            if path == '/README.md':
                return self.reply(compile_markdown().encode(), 'text/markdown; charset=utf-8')
            if path == '/preview.css':
                return self.reply((HERE/'preview.css').read_bytes(), 'text/css')
            if path == '/preview.js':
                return self.reply((HERE/'preview.js').read_bytes(), 'text/javascript; charset=utf-8')
            file = (ROOT/path.lstrip('/')).resolve()
            allowed = file.is_relative_to(HERE/'examples') or file in {
                ROOT/'LICENSE', ROOT/'controller/README.md', ROOT/'docs/GETTING_STARTED.md', ROOT/'docs/pattern-studio.md', ROOT/'docs/maze-studio.md',
                ROOT/'docs/release-preparation.md', HERE/'CREDITS.md', HERE/'ASSET-LICENSE.md'}
            if not allowed or not file.is_file():
                return self.send_error(404)
            if file.suffix == '.md':
                return self.reply(page(file.read_text()).encode(), 'text/html; charset=utf-8')
            if file.suffix == '.mp4':
                return self.video(file)
            self.reply(file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        except (ValueError, KeyError, OSError) as error:
            self.reply(str(error).encode(), 'text/plain; charset=utf-8', 500)

    def video(self, file):
        """Single byte ranges let browsers seek without restarting the clip."""
        size = file.stat().st_size
        start, end, status = 0, size - 1, 200
        headers = {'Accept-Ranges': 'bytes'}
        if requested := self.headers.get('Range'):
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if match and any(match.groups()):
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), size - 1) if right else size - 1
                else:
                    start = max(0, size - int(right))
                status = 206
            else:
                start = size
            if start >= size or end < start:
                return self.reply(b'', 'video/mp4', 416, {'Content-Range': f'bytes */{size}'})
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        with file.open('rb') as stream:
            stream.seek(start)
            body = stream.read(end - start + 1)
        return self.reply(body, 'video/mp4', status, headers)

    def reply(self, body, content_type, status=200, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'check', 'serve'])
    parser.add_argument('--port', type=int, default=9195)
    parser.add_argument('--bind', default='0.0.0.0')
    args = parser.parse_args()
    markdown = verify()
    if args.command == 'build':
        BUILD.mkdir(parents=True, exist_ok=True)
        (BUILD/'README.md').write_text(markdown)
        (BUILD/'index.html').write_text(page(markdown))
        print(BUILD/'README.md')
    elif args.command == 'check':
        print('PASS README links, sample assets, frozen hashes and PDF/intent identities')
    else:
        print(f'README draft: http://{args.bind}:{args.port}/', flush=True)
        ThreadingHTTPServer((args.bind, args.port), Handler).serve_forever()
