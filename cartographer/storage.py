"""Named map documents with durable revisions and small saved thumbnails."""
import base64
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import fcntl
import io
import json
import os
from pathlib import Path
import re
import threading
import uuid
from PIL import Image, UnidentifiedImageError

from .model import canonical, digest, validate_recipe


class Conflict(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value):
        raise ValueError('Invalid identifier')
    return value


def atomic_json(path, value, *, durable=True):
    atomic_bytes(path, canonical(value), durable=durable)


def atomic_bytes(path, value, *, durable=True):
    """Publish a complete file; saved state is durable unless explicitly transient."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('wb') as f:
            f.write(value)
            if durable:
                f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
        if durable:
            descriptor = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temp.unlink(missing_ok=True)


def thumbnail_bytes(value):
    if value is None:return None
    if not isinstance(value,str) or len(value)>400_000 or not re.fullmatch(r'data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=\r\n]+',value):
        raise ValueError('Thumbnail must be a PNG or JPEG smaller than 300 KB')
    try:
        data=base64.b64decode(value.split(',',1)[1],validate=True)
        with Image.open(io.BytesIO(data)) as image:
            if image.width>1024 or image.height>1024:raise ValueError('Thumbnail dimensions exceed 1024 pixels')
            image.load();image.thumbnail((384,288))
            rgb=Image.new('RGB',image.size,'white');rgba=image.convert('RGBA');rgb.paste(rgba,mask=rgba.getchannel('A'))
            result=io.BytesIO();rgb.save(result,format='JPEG',quality=85)
            return result.getvalue()
    except (UnidentifiedImageError,Image.DecompressionBombError,OSError) as error:raise ValueError('Invalid thumbnail image') from error


class Recipes:
    def __init__(self, directory):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    @contextmanager
    def locked(self):
        with self.lock, (self.directory/'.lock').open('a+b') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def get(self, identity, revision=None):
        directory = self.directory/identifier(identity)
        files = sorted(directory.glob('[0-9]'*8+'.json'))
        if not files:
            raise FileNotFoundError('Recipe not found')
        if revision is not None:
            if type(revision) is not int or revision < 1:
                raise ValueError('Invalid recipe revision')
            value=json.loads((directory/f'{revision:08d}.json').read_text())
        else:value=json.loads(files[-1].read_text())
        value['recipe_hash']=digest(value['recipe'])
        value['thumbnail_url']=(f'/api/recipes/{identity}/thumbnail?revision={value["revision"]}'
                                if (directory/f'{value["revision"]:08d}.jpg').exists() else None)
        return value

    def list(self):
        result = []
        for directory in self.directory.iterdir():
            if directory.is_dir():
                try:value = self.get(directory.name)
                except FileNotFoundError:continue # A failed first save has no committed revision.
                result.append({k: value[k] for k in ['id', 'revision', 'updated_at', 'title','thumbnail_url']})
        return sorted(result, key=lambda v: v['updated_at'], reverse=True)

    def save(self, recipe, identity=None, expected_revision=None, name=None, thumbnail=None):
        validate_recipe(recipe)
        recipe=copy.deepcopy(recipe);picture=thumbnail_bytes(thumbnail)
        with self.locked():
            if identity is None:
                identity, revision = uuid.uuid4().hex, 1
            else:
                old = self.get(identity)
                if type(expected_revision) is not int or old['revision'] != expected_revision:
                    raise Conflict('This document was saved in another window. Your edits are still here; use Save As to keep a separate copy.')
                revision = old['revision']+1
            title=name if name is not None else (old['title'] if revision>1 else recipe['title'])
            if not isinstance(title,str) or not title.strip() or len(title.strip())>100:raise ValueError('Document name must contain 1–100 characters')
            title=title.strip()
            names={row['title'].casefold() for row in self.list() if row['id']!=identity}
            base=re.sub(r' \([0-9]+\)$','',title);number=2
            while (revision==1 or title!=old['title']) and title.casefold() in names:
                suffix=f' ({number})';title=base[:100-len(suffix)]+suffix;number+=1
            value = dict(id=identity, revision=revision, title=title,thumbnail_pending=picture is None,
                         updated_at=datetime.now(timezone.utc).isoformat(), recipe=recipe)
            if picture is not None:atomic_bytes(self.directory/identity/f'{revision:08d}.jpg',picture)
            atomic_json(self.directory/identity/f'{revision:08d}.json', value)
            return self.get(identity,revision)

    def thumbnail(self,identity,revision=None):
        value=self.get(identity,revision)
        return (self.directory/identity/f'{value["revision"]:08d}.jpg').read_bytes()

    def set_thumbnail(self,identity,revision,recipe_hash,thumbnail):
        if type(revision) is not int or revision<1:raise ValueError('Invalid document revision')
        picture=thumbnail_bytes(thumbnail)
        if picture is None:raise ValueError('Missing thumbnail')
        with self.locked():
            value=self.get(identity,revision)
            if value['recipe_hash']!=recipe_hash:raise Conflict('Thumbnail belongs to different map settings')
            atomic_bytes(self.directory/identity/f'{value["revision"]:08d}.jpg',picture)
            return self.get(identity,revision)
