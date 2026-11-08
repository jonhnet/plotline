# Dependency and artwork notices

Plotline-authored code is **AGPL-3.0-only**; see [LICENSE](LICENSE).
Dependencies and example artwork retain their own terms. The source package
does not bundle installed Python wheels, Node modules, Rust crates, system
libraries, fonts or a Raspberry Pi OS image.

## Runtime and build dependencies

| Component | Use | Declared license / notice |
| --- | --- | --- |
| MuPDF / PyMuPDF Lite | Native PDF interpretation | AGPL v3 or Artifex commercial license; Plotline uses the open-source edition |
| MuPDF.js | PDF adapter and reference checks | AGPL-3.0-or-later |
| GEOS | Geometry engine | LGPL-2.1-or-later |
| Shapely | Python geometry binding | BSD-3-Clause; its wheel also contains GEOS |
| Rust dependencies | Server, geometry, serialization, spatial indexes | Individual MIT, Apache-2.0, BSD and other notices in the inventory below |
| NumPy / SciPy / scikit-image | Numeric processing and scan analysis | BSD-family licenses and bundled-component notices |
| ContourPy | Connected flat-map elevation contours | BSD-3-Clause |
| Pillow | Image input, previews, scans | MIT-CMU and bundled-component notices |
| OpenCV, distribution build | Raster stroke extraction | Apache-2.0 / BSD notices; Intel IPP and non-free algorithms are rejected |
| Cairo / Pycairo | Raster previews | LGPL-2.1 / MPL-1.1 alternatives and component notices |
| mapbox-vector-tile / pyclipper / protobuf | Map decoding and geometry | MIT / MIT / BSD-3-Clause |
| orjson | JSON interchange | MPL-2.0 AND (Apache-2.0 OR MIT) |
| jsonschema and supporting packages | Contract validation | MIT and supporting-package notices |
| Defusedxml / Tinycss2 / webencodings | SVG validation | PSF / BSD-3-Clause / BSD-3-Clause |
| Inkscape | SVG-to-PDF import | GPL-2.0-or-later and component notices |
| DejaVu fonts | Map lettering | Bitstream Vera license and DejaVu public-domain additions |
| OpenPrinting CUPS | Optional IPP intake | Apache-2.0 with exceptions; the version, source checksum and local patch are in `tools/print_sink/` |
| OpenSSH / Python / Raspberry Pi OS | Optional remote serial bridge | Their respective distribution notices; provisioned OS images are not included |
| Playwright | Browser tests | Apache-2.0; installed browsers carry their own notices |

[The dependency inventory](docs/dependency-inventory.json) records exact locked
Rust/npm versions and declared licenses, plus metadata from the tested Python
environments. Generate it with `python3 tools/dependency_inventory.py` after
installing the documented dependencies and fetching the locked Cargo packages.
It is an inventory, not a new license grant or a binary-distribution notice bundle.
The installed distribution packages' complete copyright files are under
`/usr/share/doc/<package>/copyright` on Ubuntu.

MuPDF's licensing is documented by [PyMuPDF](https://pymupdf.readthedocs.io/en/latest/about.html)
and [MuPDF.js](https://mupdfjs.readthedocs.io/en/latest/faq/index.html).
Any future binary or container distribution needs the corresponding dependency
license texts and source availability alongside its reproducible build.

## Examples and data

See [sample credits](docs/readme/CREDITS.md) for OpenStreetMap/USGS attribution,
the FreeCAD model's CC-BY-4.0 notice and the Spider-Man artwork's attribution.
Jon's original example assets have a separate [CC BY 4.0 notice](docs/readme/ASSET-LICENSE.md).
AGPL coverage of the software does not relicense these inputs.

The public source export excludes the private historical archive, manuals,
original scans, provider download caches and live Studio documents. Map Studio
fetches data from its providers; their attribution and data-use terms still apply.
The standalone historical geographic snapshots are kept in the private development
checkout, rather than bundled with this source package.

README processing uses Pillow/NumPy/OpenCV, and FFmpeg/libx264/libwebp for video.
Those editorial tools are not runtime dependencies. The photographs show actual
physical plots; their processing records describe presentation corrections.
