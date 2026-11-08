"""Preserve the eight supplied renditions, including private URL provenance."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", type=Path, help="Private text file, one supplied URL per portfolio row")
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    portfolio = json.loads(Path(__file__).with_name("portfolio.json").read_text())
    urls = [s.strip() for s in args.urls.read_text().splitlines() if s.strip()]
    if len(urls) != len(portfolio):
        parser.error("URL count must match the portfolio")
    (args.state / "inputs").mkdir(parents=True, exist_ok=True)

    def fetch(pair):
        item, url = pair
        dest = args.state / "inputs" / (item['id'] + ".jpg")
        if dest.exists():
            content = dest.read_bytes()
        else:
            request = Request(url, headers={"User-Agent": "Plotline raster lab source capture"})
            with urlopen(request, timeout=45) as response:
                content = response.read(32 * 1024 * 1024 + 1)
            if len(content) > 32 * 1024 * 1024:
                raise ValueError(f"Image too large: {item['id']}")
        with Image.open(BytesIO(content)) as image:
            image.load()
            if image.format != "JPEG":
                raise ValueError(f"Expected supplied JPEG rendition: {item['id']}")
            size = image.size
        if not dest.exists():
            dest.write_bytes(content)
        print(f"{item['id']}: {size[0]} × {size[1]}, {len(content)} bytes", flush=True)
        return dict(item, file=dest.name, source_url=url, pixels=list(size),
                    sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, zip(portfolio, urls)))
    manifest = dict(captured_utc=datetime.now(timezone.utc).isoformat(),
                    note="Exact supplied renditions; titles and artist handles transcribed from filenames.",
                    images=results)
    target = args.state / "sources.json"
    if not target.exists():
        target.write_text(json.dumps(manifest, indent=2) + "\n")
    else:
        previous = json.loads(target.read_text())
        if [(v['id'],v['sha256'],v['source_url']) for v in previous['images']] != [
                (v['id'],v['sha256'],v['source_url']) for v in results]:
            raise ValueError("Existing portfolio differs; preserve it and use a new state directory")


if __name__ == "__main__":
    main()
