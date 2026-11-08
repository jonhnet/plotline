#!/usr/bin/env python3
"""Reproduce the offline milestone, including PDF-vs-geometry raster checks."""
import json
import subprocess
from pathlib import Path
from PIL import Image
from raster_compare import render_comparison

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def check_case(name, pdf, pen_width="0.8", fill_style="hatch", adapter="js", binary="target/debug/plotline", palette="exact", dpi="216", reference_mode="default"):
    events = f"build/fixtures/{name}.json"
    if adapter == "native":
        print(run(".venv/bin/python", "tools/pdf_native.py", pdf, events))
    else:
        print(run("node", "tools/pdf-events.mjs", pdf, events))
    print(run(binary, events, f"build/{name}", pen_width, "40", fill_style, palette))
    return check_artifacts(name, pdf, dpi, reference_mode)


def check_artifacts(name, pdf, dpi="216", reference_mode="default"):
    """Validate existing conversion artifacts without rerunning expensive geometry."""
    reference = f"build/{name}/reference.png"
    coverage = f"build/{name}/coverage.png"
    sampling = render_comparison(pdf, ROOT / f"build/{name}", dpi, reference_mode)
    expected = Image.open(ROOT / reference).convert("RGB")
    actual = Image.open(ROOT / coverage).convert("RGB")
    ratio = sampling['sampled']['ratio']
    default_ratio = sampling.get('default_sampled', sampling['sampled'])['ratio']
    # Fixed supersampling reduces rasterizer snapping; the original color and
    # ink-area thresholds remain unchanged. Native rasters stay diagnostic.
    if ratio >= 0.01:
        raise AssertionError(f"{name}: supersampled large pixel discrepancy ratio {ratio:.4%}")
    report = json.loads((ROOT / f"build/{name}/report.json").read_text())
    if not report["metrics"]["paths"]:
        raise AssertionError(f"{name}: no physical paths")
    for paint in report["decoded_hpgl"]:
        if paint.get("outside_allowed_area_pt2",paint["outside_visible_area_pt2"]) > 1e-7 or (paint["white"] and paint["paths"]):
            raise AssertionError(f"{name}: invalid physical footprint/white pen")
        if (paint.get("uncovered_required_area_pt2") or 0) > 1e-7:
            raise AssertionError(f"{name}: required stroke interior is unpainted")
    groups = {}
    for paint in report["decoded_hpgl"]:
        if paint["white"]:
            continue
        group = groups.setdefault(paint["representation"], [0.0, 0.0])
        group[0] += paint["visible_area_pt2"]
        group[1] += paint.get("ink_inside_visible_area_pt2",paint["ink_area_pt2"])
    fractions = {kind: ink / area for kind, (area, ink) in groups.items() if area}
    check_representation(name, report, fractions)
    # This second comparison is a MEASUREMENT, not the interpretation 1% test.
    # Orange gaps include intentional hatching and unavoidable pen-width margins.
    physical = f"build/{name}/hpgl.png"
    run("mutool", "draw", "-q", "-r", dpi, "-o", physical, f"build/{name}/hpgl.svg")
    plotted = Image.open(ROOT / physical).convert("RGB")
    if plotted.size != actual.size:
        raise AssertionError(f"{name}: physical preview dimensions differ")
    missing = excess = logical_count = plotted_count = 0
    physical_color_differences = physical_comparison_ink = 0
    difference = []
    for source, logical, physical_pixel in zip(expected.getdata(), actual.getdata(), plotted.getdata()):
        l = min(logical) < 224
        p = min(physical_pixel) < 224
        logical_count += l
        plotted_count += p
        missing += l and not p
        excess += p and not l
        physical_comparison_ink += min(source + physical_pixel) < 224
        physical_color_differences += max(abs(x-y) for x, y in zip(source, physical_pixel)) > 64
        difference.append((235, 155, 35) if l and not p else (205, 25, 155) if p and not l else (255, 255, 255))
    delta = Image.new("RGB", actual.size)
    delta.putdata(difference)
    delta.save(ROOT / f"build/{name}/representation-difference.png")
    return {"case": name, "dpi": float(dpi), "reference_mode": reference_mode,
            "interpretation_large_pixel_discrepancy_ratio": ratio,
            "native_resolution_large_pixel_discrepancy_ratio": sampling['native']['ratio'],
            "raster_sampling": sampling,
            "default_pdf_raster_discrepancy_ratio": default_ratio,
            "representation_ink_fractions_by_policy": fractions,
            "representation_raster": {"missing_logical_ink_fraction": missing/max(logical_count,1),
                                      "excess_plotted_ink_fraction": excess/max(plotted_count,1),
                                      "large_color_discrepancy_ratio_vs_pdf": physical_color_differences/max(physical_comparison_ink,1),
                                      "note": "Measured separately; hatch gaps are intentional. Containment is checked geometrically."},
            "metrics": report["metrics"]}


def check_representation(name, report, fractions):
    # Quality gates are explicit fixture expectations, independent of safety.
    if name in ("roads", "roads-solid"):
        for paint in report["decoded_hpgl"]:
            if paint["id"] in (0, 2) and paint["ink_fraction_of_visible_area"] < 0.94:
                raise AssertionError(f"{name}: road casing completeness below 94%")
        green = next(p for p in report["decoded_hpgl"] if p["id"] == 5)
        fraction = green["ink_fraction_of_visible_area"]
        if name == "roads" and not 0.40 <= fraction <= 0.50:
            raise AssertionError("Hatch box must retain its intended sparse coverage")
        if name == "roads-solid" and fraction < 0.97:
            raise AssertionError("Solid box must cover at least 97% of visible area")
        if report["metrics"]["paths"] > 30:
            raise AssertionError("Road fixture path fragmentation regressed")
    floors = {
        "clip-and-holes": {"hatch": 0.30},
        "winding-rules": {"hatch": 0.30},
        "curves-and-dashes": {"stroke_contours": 0.90},
        "legacy-dashes": {"stroke_contours": 0.50},
        "native-text": {"glyph_contours": 0.95},
        "native-operations": {"glyph_contours": 0.90, "stroke_contours": 0.80, "hatch": 0.30},
        "roslyn": {"stroke_contours": 0.85, "glyph_contours": 0.75, "hatch": 0.30},
        "patterns": {"stroke_contours": 0.85, "hatch": 0.35},
        "pattern-bbox": {"hatch": 0.40},
        "miter-limits": {"stroke_contours": 0.97},
        "roosevelt": {"stroke_contours": 0.96, "glyph_contours": 0.80, "hatch": 0.35},
    }
    for kind, floor in floors.get(name, {}).items():
        if fractions.get(kind, 0.0) < floor:
            raise AssertionError(f"{name}: {kind} ink fraction {fractions.get(kind)} below {floor}")


def main():
    for args in [("cargo", "fmt", "--all", "--check"),
                 ("cargo", "clippy", "--all-targets", "--", "-D", "warnings"),
                 ("cargo", "test", "--locked"), ("npm", "test"), ("cargo", "build", "--locked")]:
        run(*args)
        print("PASS", " ".join(args))
    print(run("node", "tools/make-fixtures.mjs"))
    results = []
    for name in ["roads", "clip-and-holes", "curves-and-dashes", "winding-rules"]:
        results.append(check_case(name, f"build/fixtures/{name}.pdf"))
    results.append(check_case("roads-solid", "build/fixtures/roads.pdf", fill_style="solid"))
    results.append(check_case("legacy-dashes", "archealogy/fixtures/recovered-2dbf0747/test/plotler/dashed-stroke-test.pdf", "0.3"))
    # End-to-end failure must precede creating any output directory.
    events = "build/fixtures/legacy-text.json"
    run("node", "tools/pdf-events.mjs", "archealogy/fixtures/recovered-2dbf0747/test/plotler/simple-text.pdf", events)
    import tempfile
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as temp:
        destination = str(Path(temp) / "must-not-exist")
        fail = subprocess.run(["target/debug/plotline", events, destination], cwd=ROOT, capture_output=True, text=True)
        if fail.returncode == 0 or "fillText" not in fail.stderr or Path(destination).exists():
            raise AssertionError("Unsupported text was not rejected before output")
    run("mutool", "draw", "-q", "-r", "120", "-o", "build/roads/comparison.png", "build/roads/comparison.svg")
    (ROOT / "build/validation.json").write_text(json.dumps(results, indent=2) + "\n")
    for result in results:
        print(f"PASS {result['case']}: PDF INTERPRETATION discrepancy {result['interpretation_large_pixel_discrepancy_ratio']:.3%}")
    print("PASS unsupported text rejected; artifacts in build/, summary in build/validation.json")


if __name__ == "__main__":
    main()
