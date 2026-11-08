"""Durable notes, anchored to an exact result and optional normalized viewport."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import UUID


class CommentError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def atomic_record(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.note-', delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def valid_id(value):
    if not isinstance(value, str):
        raise CommentError('Invalid comment ID')
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise CommentError('Invalid comment ID') from None
    return value


def valid_text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise CommentError('Write a comment between 1 and 4,000 characters')
    return value.strip()


def valid_view(value):
    if not isinstance(value, dict):
        raise CommentError('Invalid saved view')
    result = {}
    for key, minimum, maximum in [('zoom', 1, 8), ('x', 0, 1), ('y', 0, 1)]:
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not minimum <= number <= maximum:
            raise CommentError('Invalid saved view')
        result[key] = number
    return result


class Comments:
    def __init__(self, state, output):
        self.state, self.output = Path(state), Path(output)
        self.root = self.state / 'comments'
        self.samples = self.root / 'samples'
        self.samples.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self):
        with (self.root / '.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def list(self):
        # Atomic file replacement permits reads without locking the whole list.
        notes = [json.loads(p.read_text()) for p in self.root.glob('*.json')]
        return sorted(notes, key=lambda n: (n['created_utc'], n['id']))

    def sample(self, anchor):
        if not isinstance(anchor, dict):
            raise CommentError('Missing sample')
        row, column, key = (anchor.get(k) for k in ('challenge_id', 'experiment_id', 'result_key'))
        if not all(isinstance(s, str) and re.fullmatch(r'[a-z0-9-]{1,80}', s) for s in (row, column, key)):
            raise CommentError('Invalid sample identifiers')
        sources = json.loads((self.state / 'sources.json').read_text())['images']
        extra = self.state / 'palette-sources.json'
        if extra.is_file():
            sources += json.loads(extra.read_text())['images']
        source = next((s for s in sources if s['id'] == row), None)
        if not source or source['sha256'] != anchor.get('source_sha256'):
            raise CommentError('This source image has changed; reload before commenting', 409)
        if column == 'source':
            if key != source['sha256'][:20] + '-source':
                raise CommentError('Invalid source version')
            result = dict(key=key, signature=dict(source=source['sha256'], recipe=dict(id='source', name='Original')))
            assets = {'preview.png': self.output / row / 'source.png'}
        else:
            if not re.fullmatch('[0-9a-f]{20}', key):
                raise CommentError('Invalid result version')
            folder = self.output / row / key
            # Already saved results remain commentable even after build-cache removal.
            if not (folder / 'result.json').is_file():
                folder = self.samples / key
            if not (folder / 'result.json').is_file():
                raise CommentError('This result is unavailable; reload before commenting', 409)
            result = json.loads((folder / 'result.json').read_text())
            if (result['key'] != key or result['signature']['source'] != source['sha256']
                    or result['signature']['recipe']['id'] != column):
                raise CommentError('Sample and result do not match')
            assets = {name: folder / name for name in ('preview.png', 'paths.svg')}
        saved = self.samples / key
        if not saved.exists():
            temporary = Path(tempfile.mkdtemp(prefix='.sample-', dir=self.samples))
            try:
                for name, path in assets.items():
                    shutil.copyfile(path, temporary / name)
                (temporary / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
                for path in temporary.iterdir():
                    with path.open('rb') as stream:
                        os.fsync(stream.fileno())
                fd = os.open(temporary, os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                temporary.rename(saved)
                # Durably retain the sample before acknowledging its first note.
                fd = os.open(self.samples, os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        return dict(challenge_id=row, experiment_id=column, result_key=key,
                    source_sha256=source['sha256'], title=source['title'], artist=source['artist'],
                    experiment_name=result['signature']['recipe']['name'], recipe=result['signature']['recipe'])

    def add(self, data):
        if not isinstance(data, dict):
            raise CommentError('Expected a JSON object')
        note_id, text = valid_id(data.get('id')), valid_text(data.get('text'))
        view = valid_view(data.get('view', dict(zoom=1, x=.5, y=.5)))
        with self.locked():
            path = self.root / (note_id + '.json')
            if path.exists():
                note = json.loads(path.read_text())
                fields = ('challenge_id', 'experiment_id', 'result_key', 'source_sha256')
                if (note['text'] == text and isinstance(data.get('sample'), dict)
                        and all(note['sample'][k] == data['sample'].get(k) for k in fields)):
                    return note  # A retried response must not create a duplicate.
                raise CommentError('This submission ID has already been used', 409)
            sample = self.sample(data.get('sample'))
            now = datetime.now(timezone.utc).isoformat()
            note = dict(id=note_id, text=text, sample=sample, view=view, version=1,
                        created_utc=now, updated_utc=now, history=[])
            atomic_record(path, note)
            return note

    def edit(self, note_id, data):
        valid_id(note_id)
        if not isinstance(data, dict):
            raise CommentError('Expected a JSON object')
        text = valid_text(data.get('text'))
        with self.locked():
            path = self.root / (note_id + '.json')
            if not path.is_file():
                raise CommentError('Comment not found', 404)
            note = json.loads(path.read_text())
            if data.get('version') != note['version']:
                raise CommentError('This comment changed in another window. Reload the notes before editing.', 409)
            note['history'].append({k: note[k] for k in ('text', 'version', 'updated_utc')})
            note.update(text=text, version=note['version'] + 1, updated_utc=datetime.now(timezone.utc).isoformat())
            atomic_record(path, note)
            return note
