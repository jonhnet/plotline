"""Import self-contained SVG through Inkscape's vector PDF exporter."""
import base64
import io
import os
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from PIL import Image
import pymupdf
import tinycss2

SVG = 'http://www.w3.org/2000/svg'
RESOURCE_ERROR = ('SVG contains a linked resource. Embed images in Inkscape before '
                  'uploading; external files, URLs and stylesheets are not imported.')
FONT_NOTE = ('SVG text uses fonts installed on the server. For a font installed only '
             'on your laptop, convert the text to paths in Inkscape before uploading, '
             'or export a PDF with the font embedded.')


def check_css(css):
    # Tokenization handles CSS escapes, comments and quoted url() arguments.
    pending = tinycss2.parse_component_value_list(css, skip_comments=True)
    while pending:
        token = pending.pop()
        if token.type == 'error':
            raise ValueError('SVG contains invalid CSS; resave it in Inkscape')
        if token.type == 'at-keyword' and token.value.lower() in ('import', 'font-face'):
            raise ValueError(RESOURCE_ERROR)
        if token.type == 'url':
            if not token.value.strip().startswith('#'):
                raise ValueError(RESOURCE_ERROR)
        elif token.type == 'function' and token.lower_name == 'url':
            args = [t for t in token.arguments if t.type not in ('whitespace', 'comment')]
            if len(args) != 1 or args[0].type != 'string' or not args[0].value.strip().startswith('#'):
                raise ValueError(RESOURCE_ERROR)
        pending.extend(getattr(token, 'content', []))
        pending.extend(getattr(token, 'arguments', []))


def embedded_image(value):
    header, separator, payload = value.partition(',')
    if not separator or header.lower() not in ('data:image/png;base64', 'data:image/jpeg;base64'):
        raise ValueError('SVG images must be embedded JPEG or PNG images; use Embed in Inkscape')
    try:
        raw = base64.b64decode(''.join(payload.split()), validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('PNG', 'JPEG') or getattr(image, 'n_frames', 1) != 1:
                raise ValueError('SVG images must be static JPEG or PNG images')
            if image.width * image.height > 40_000_000:
                raise ValueError('SVG image exceeds the 40 megapixel import limit')
            image.verify()
    except (OSError, Image.DecompressionBombError, base64.binascii.Error) as error:
        raise ValueError('SVG contains an invalid embedded image') from error


def validated_svg(raw):
    try:
        root = fromstring(raw, forbid_dtd=True)
    except (ET.ParseError, DefusedXmlException) as error:
        raise ValueError('Invalid SVG or unsupported XML declarations; resave as SVG in Inkscape') from error
    if root.tag != f'{{{SVG}}}svg':
        raise ValueError('Expected an SVG document with the SVG namespace')
    has_text = False
    images = 0
    for element in root.iter():
        tag = element.tag.rsplit('}', 1)[-1]
        if tag in ('script', 'foreignObject') or tag.startswith('animate') or tag == 'set':
            raise ValueError('SVG scripts, animation and embedded HTML are not supported; export a static SVG or PDF')
        has_text |= tag in ('text', 'flowRoot')
        images += tag == 'image'
        if tag == 'style':
            check_css(''.join(element.itertext()))
        for name, value in list(element.attrib.items()):
            local = name.rsplit('}', 1)[-1]
            if local == 'absref':
                # Inkscape's fallback to the author's local image path must not
                # override the validated embedded image on a different machine.
                del element.attrib[name]
            elif name == '{http://www.w3.org/XML/1998/namespace}base':
                raise ValueError(RESOURCE_ERROR)
            elif local == 'href':
                value = value.strip()
                if value.startswith('#'):
                    continue
                if tag == 'image' and value.lower().startswith('data:'):
                    embedded_image(value)
                elif tag == 'a':
                    # Keep the artwork, but don't pass hyperlinks to the importer.
                    del element.attrib[name]
                else:
                    raise ValueError(RESOURCE_ERROR)
            elif not name.startswith('{'):
                if local.lower().startswith('on'):
                    raise ValueError('SVG event scripts are not supported; export a static SVG or PDF')
                check_css(value)
        if 'href' in element.attrib:
            # Inkscape 1.2 handles xlink:href but silently omits some SVG 2 href
            # references. SVG 2 gives the unprefixed attribute precedence.
            element.set('{http://www.w3.org/1999/xlink}href', element.attrib.pop('href'))
    # Re-serialization also excludes XML processing instructions, including
    # external stylesheet declarations. Reject them instead of dropping style.
    class CheckedTree(ET.TreeBuilder):
        def pi(self, target, text):
            if target == 'xml-stylesheet':
                raise ValueError(RESOURCE_ERROR)
    ET.fromstring(raw, parser=ET.XMLParser(target=CheckedTree()))
    ET.register_namespace('', SVG)
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True), has_text, images


def svg_pdf(source, destination):
    content, has_text, images = validated_svg(Path(source).read_bytes())
    destination = Path(destination)
    # No original source is opened by Inkscape: only the checked, self-contained
    # copy in an otherwise empty directory. Its private profile is disposable.
    with tempfile.TemporaryDirectory(prefix='plotline-svg-') as directory:
        directory = Path(directory)
        svg, pdf = directory/'input.svg', directory/'output.pdf'
        svg.write_bytes(content)
        env = dict(os.environ, INKSCAPE_PROFILE_DIR=str(directory/'profile'))
        command = ['inkscape', str(svg), '--export-area-page', '--export-type=pdf',
                   '--export-dpi=300', f'--export-filename={pdf}']
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120, env=env)
        except FileNotFoundError as error:
            raise ValueError('SVG import needs Inkscape on the server; install inkscape and retry the upload') from error
        except subprocess.TimeoutExpired as error:
            raise ValueError('SVG import exceeded two minutes; original retained. Try exporting PDF from Inkscape') from error
        if result.returncode or not pdf.is_file():
            raise ValueError('Inkscape could not import this SVG: '+result.stderr[-1500:].strip())
        with pymupdf.open(pdf) as doc:
            if not 1 <= len(doc) <= 200:
                raise ValueError('SVG import supports 1–200 pages')
            # Remove volatile export timestamps; retries normalize the same art.
            doc.set_metadata({**doc.metadata, 'creationDate': '', 'modDate': ''})
            doc.xref_set_key(-1, 'ID', 'null')
            data = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
        version = subprocess.run(['inkscape', '--version'], capture_output=True,
                                 text=True, timeout=10, env=env).stdout.strip()
    temporary = destination.with_suffix('.tmp')
    temporary.write_bytes(data)
    temporary.replace(destination)
    return dict(renderer=version, has_text=has_text, embedded_images=images,
                notes=[FONT_NOTE] if has_text else [])
