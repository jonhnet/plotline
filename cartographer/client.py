"""Plotline plot-preparation API client. No carousel writes or physical execution APIs."""
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urljoin, urlsplit, urlunsplit
import uuid
from jsonschema.exceptions import ValidationError

from . import ROOT
from .model import media_profiles, canonical
from .storage import atomic_json
from print_contract import capabilities, validate_schema, validate_intent


def error_summary(message):
    """Separate the actionable driver error from terminal escapes and traces."""
    text=re.sub(r'(?:\x1b|␛)\[[0-?]*[ -/]*[@-~]','',str(message)).strip()
    errors=re.findall(r'(?:^|\n)(?:[\w.]+(?:Error|Exception)):\s*(.*)',text)
    return errors[-1].strip() if errors else text


class Rejected(ValueError):
    def __init__(self,status,message):
        super().__init__(f'Plotline Studios rejected the request ({status}): {error_summary(message)}')
        self.status=status;self.diagnostic=str(message)


class Ambiguous(ValueError):
    """A mutation may have succeeded; never repeat it without reconciliation."""


class HandoffUnavailable(ValueError):pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Plotline Studios returned an unexpected redirect')


def validate_capabilities(value):
    validate_schema('capabilities',value)


class PrintClient:
    def __init__(self, server, state, public_server=None):
        self.server = server.rstrip('/')
        self.public_server = (public_server or server).rstrip('/')
        self.state = Path(state)
        self.opener=build_opener(NoRedirect)

    def resolve(self,url,public=False):
        parsed=urlsplit(urljoin(self.server+'/',url))
        def origin(value):
            p=urlsplit(value)
            return p.scheme,p.hostname,p.port or (443 if p.scheme=='https' else 80)
        if (parsed.username or parsed.password or parsed.scheme not in ('http','https') or
                origin(parsed.geturl()) not in (origin(self.server),origin(self.public_server))):
            raise ValueError('Unexpected Plotline Studios URL origin')
        target=urlsplit(self.public_server if public else self.server)
        return urlunsplit((target.scheme,target.netloc,parsed.path,parsed.query,parsed.fragment))

    def read(self, path):
        with self.opener.open(self.resolve(path), timeout=5) as response:
            return json.load(response)

    def check_handoff(self):
        current=self.read('/api/v1/capabilities');validate_capabilities(current)
        if not all(current['implemented'].get(key) for key in ['pdf_upload','pdf_handoff']):
            raise HandoffUnavailable('Plotline Studios does not advertise PDF upload and draft handoff. Download the bundle instead.')
        return current

    def post(self,path,data,content_type='application/json',headers=None):
        request=Request(self.resolve(path),data,{'Content-Type':content_type,'X-Plotline-Request':'1',**(headers or {})},method='POST')
        try:
            with self.opener.open(request,timeout=45) as response:
                if response.status!=202:raise Ambiguous('Plotline Studios did not acknowledge this request with HTTP 202')
                return json.load(response)
        except HTTPError as error:
            detail=error.read(4000).decode(errors='replace')
            try:detail=json.loads(detail).get('error',detail)
            except ValueError:pass
            if 400<=error.code<500:raise Rejected(error.code,detail) from error
            raise Ambiguous(f'Plotline Studios returned HTTP {error.code}; the request may already have been accepted') from error
        except (OSError,ValueError) as error:
            if isinstance(error,Ambiguous):raise
            raise Ambiguous('The acknowledgement was lost or invalid; the request may already have been accepted') from error

    def receipt(self,result):
        try:
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',result['id']):raise ValueError('Invalid receipt ID')
            for key in ['status_url','review_url']:result[key]=self.resolve(result[key],public=True)
            if 'status' not in result:raise ValueError('Missing receipt status')
        except (KeyError,TypeError,ValueError) as error:
            raise Ambiguous('Incomplete or invalid receipt from Plotline Studios') from error
        return result

    def draft(self,document_id,intent):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',document_id):raise ValueError('Invalid document ID')
        return self.receipt(self.post('/api/v1/documents/'+document_id+'/draft',canonical(intent)))

    def submission(self,key):
        if str(uuid.UUID(key))!=key:raise ValueError('Invalid submission key')
        try:return self.receipt(self.read('/api/v1/submissions/'+key))
        except HTTPError as error:
            if error.code==404:return None
            raise

    def inventory(self):
        try:
            try:
                result = self.read('/api/v1/capabilities')
                validate_capabilities(result)
                source = 'live capabilities'
            except HTTPError as error:
                if error.code not in (404, 405):
                    raise
                result = capabilities(self.read('/api/carousel'), media_profiles())
                source = 'live carousel adapter'
            physical = self.read('/api/carousel')
            actual = capabilities(physical, media_profiles())
            result.update(inventory_revision=actual['inventory_revision'],available_inks=actual['available_inks'])
            envelope = dict(capabilities=result, carousel=physical, source=source, offline=False,
                            captured_at=datetime.now(timezone.utc).isoformat())
            atomic_json(self.state/'inventory-snapshot.json', envelope)
            return envelope
        except (OSError, ValueError, KeyError, ValidationError) as error:
            saved = self.state/'inventory-snapshot.json'
            if saved.exists():
                envelope = json.loads(saved.read_text())
            else:
                carousel = json.loads((ROOT/'cartographer/fixtures/carousel.json').read_text())
                envelope = dict(capabilities=capabilities(carousel, media_profiles()),carousel=carousel,
                                captured_at='2026-10-30', source='recorded carousel revision 5')
            return dict(envelope, offline=True, reason='Plotline Studios is unavailable; using the recorded snapshot')

    def submit(self, pdf, intent, checked=False, submission_key=None):
        validate_intent(intent, pdf)
        # Recheck advertised support at the time of this explicit submission.
        if not checked:
            current=self.check_handoff()
            if submission_key and not current['implemented'].get('pdf_submission_idempotency'):
                raise HandoffUnavailable('Plotline Studios does not support recoverable PDF submissions')
        if submission_key and str(uuid.UUID(submission_key))!=submission_key:raise ValueError('Invalid submission key')
        boundary = 'plotline-'+uuid.uuid4().hex
        filename = (re.sub(r'[^A-Za-z0-9 _.-]+', '-', intent['title']).strip(' .-') or 'map')[:90]+'.pdf'
        chunks = []
        for field, filename, mime, data in [('document', filename, 'application/pdf', Path(pdf).read_bytes()),
                                            ('intent', 'map.print-intent.json', 'application/json', canonical(intent))]:
            chunks += [f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode(),
                       data, b'\r\n']
        chunks.append(f'--{boundary}--\r\n'.encode())
        return self.receipt(self.post('/api/v1/documents',b''.join(chunks),'multipart/form-data; boundary='+boundary,
                                     headers={'Idempotency-Key':submission_key} if submission_key else None))
