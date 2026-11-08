# Plotline Studios

Read `docs/GETTING_STARTED.md` and the relevant Studio guide before changing a
workflow. On Jon's host, also follow `/root/AGENTS.md` if it exists.

## Repository and services

This is the canonical history, started fresh on November 8, 2026. Create new
branches and worktrees from current `main`. Never merge an old private branch;
transfer fixes as reviewed patches or cherry-picks. See `docs/PUBLICATION.md`.

On the development host, `/root/plotline` remains the working directory.
`origin` is the private home repository; `github` is `jonhnet/plotline`.
Existing old directories under `worktrees/` belong to the retired private
repository. They remain for existing references and the raster lab. Start new
work in fresh worktrees. Preserve live state and source documents. Never restart
a service while it is converting or plotting; use the development service
helper's idle checks. Receiving, converting and preparing never start physical
plotting. Physical plotting needs explicit operator instruction.

## Geometry and workflows

- Shared PDF, ink and paper contracts are in `contracts/v1/README.md`.
  Preserve physical page placement and paper origin; never center ink bounds.
- Document inks are independent of carousel slots and may share colors at
  different widths. Authoring can read the carousel but cannot silently rewrite
  physical inventory. Unknown widths remain unknown.
- Keep logical paint coverage separate from pen paths. White paint occludes
  without a white pen. Preserve road casings, original clips, later paint and
  finite pen footprints. Width tolerance is total error in pen diameters;
  adjacent passes touch or overlap. Excess cannot cross clips or later paint.
- IPP input stops in PDF Studio for interpretation. Pen-aware authored documents
  proceed through conversion to HPGL Studio. See `docs/printing-workflows.md`,
  `docs/pen-workflow-plan.md` and `docs/thin-detail-recovery.md`.
- Pattern/Maze strokes need roles and full-pen containment in the printable
  area. Recipe imports create unsaved tabs, preserve design settings, and never
  adopt foreign document IDs, print receipts or physical inventory.
- Reuse shared Studio chrome, paper units, ink controls, preview controls and
  source navigation. Keep new UX consistent across the Studios.
- Recipes, uploads, source snapshots, plot receipts and raster-lab comments are
  durable user data even when ignored by Git. Do not create test documents or
  comments in live stores. Keep build caches separate.
- Process artwork locally with open-source tools, never image-analysis
  services. Use distribution OpenCV without Intel IPP or non-free algorithms.
  See `raster_lab/README.md` and `docs/pdf-raster.md`.
- Map hatching is continuous; spacing controls sparsity. Keep 3D line widths
  constant on paper and join visible centerlines before stroking.

## Validation and documentation

Run checks appropriate to the change. Browser checks use isolated stores with
no plotter connection; commands are in `controller/README.md`. Preserve physical
placement, clipping, pen-width, role and stale-preview regressions. Windows is
untested and community-supported, not a release gate.

README source is `docs/readme/README.template.md` and `samples.json`. Build with
`docs/readme/preview.py build` and update root `README.md`. Use actual plot
photographs with recorded processing. Keep originals and private correspondence
out of Git. Keep showcase prose brief and link useful authoring inputs.

Code is AGPL-3.0-only. Jon's original example assets are CC BY 4.0; preserve
third-party terms in `docs/readme/CREDITS.md`. The Spider-Man example has artist
permission for this demonstration; its illustration is not CC BY or AGPL.
