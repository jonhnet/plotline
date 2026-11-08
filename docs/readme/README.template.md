# Plotline Studios

Plotline Studios is a collection of tools for giving your pen plotter some really fun stuff to do.

{{sample:plotter}}

## Map Studio

Draw 2D and 3D maps from OpenStreetMap data.

{{sample:washington}}

{{sample:paine-field}}

{{sample:seattle}}

{{sample:snoqualmie}}

{{sample:ravenna}}

## PDF Studio

Render vector and raster graphics with pens. Print to Plotline over IPP from
applications such as Inkscape and FreeCAD, or open a PDF, SVG or image directly.
Choose how colors, fills and fine details become pen strokes, then inspect the
result before sending it to the plotter.

{{sample:freecad}}

{{sample:spider-man}}

In this illustration by [brunofloresmx](https://www.deviantart.com/brunofloresmx/art/Spiderman-1401741847),
the bitmap becomes colored pen screening with recovered black strokes for
clarity. The lettering added in Inkscape remains vector artwork. White letters
clear the earlier drawing; their black outlines are drawn with the pen.

{{links:spider-man}}

## Pattern Studio

Forty-seven generative designs made for pen plotting: fractals, branching plants,
space-filling curves, spirographs, interlace, guilloché, reaction–diffusion contours
and more.

{{sample:hilbert}}

{{sample:plant}}

{{sample:sierpinski}}

{{sample:spirograph}}

## Maze Studio

Make mazes on square, hexagonal or triangular grids.

{{sample:hex-maze}}

[Explore Maze Studio.](docs/maze-studio.md)

## HPGL Studio

See the pen paths, match the drawing's inks to the pens in your carousel, check
the loaded paper, and plot.

{{sample:hpgl}}

Print a calibration sheet and scan it back in to measure your actual pen widths
and sample their appearance on paper.

{{sample:calibration}}

{{sample:stroke}}

## Supported devices

Developed and tested with Jon's HP 7585B. The studios can prepare and preview
artwork without a connected plotter. Other devices will need their own paper,
transport and control support.

## Run it

Linux setup and the optional Raspberry Pi USB-to-serial bridge are documented in the
[setup guide](docs/GETTING_STARTED.md).

Windows is untested, good luck.

## License and credits

Plotline-authored code is [GNU AGPL v3](LICENSE). Jon's photos and example recipes
are [CC BY 4.0](docs/readme/ASSET-LICENSE.md). Third-party artwork, map data,
models and dependencies carry their own licenses. See the
[sample credits and source notes](docs/readme/CREDITS.md).
