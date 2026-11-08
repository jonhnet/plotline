"""Bound preview work by pixel count, independently of PDF paper dimensions."""
import pymupdf

VERSION = 2
SOURCE_EDGE = 1600
PLACED_EDGE = 2200


def render_page(page, *, edge=SOURCE_EDGE, alpha=False):
    scale = edge / max(page.rect.width, page.rect.height)
    return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=alpha)


def filename(page):
    return f'page-{page}-v{VERSION}.png'
