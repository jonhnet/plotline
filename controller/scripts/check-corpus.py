#!/usr/bin/env python3
"""Check generated Plotline HPGL against its independent conversion reports.

Optional generated inputs only; never runs the converter or legacy controller.
"""
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / "controller/target/release/plotline-studios"


def main():
    sources = sorted((ROOT / "build").glob("*/plot.hpgl"))
    if not sources:
        raise SystemExit("No generated build/*/plot.hpgl inputs; generate a map first.")
    comparisons = 0
    with tempfile.TemporaryDirectory(prefix="plotline-corpus-") as spool:
        for path in sources:
            output = subprocess.run([str(EXE), "--state", spool, "--import", str(path)],
                                    capture_output=True, text=True, check=True)
            job = json.loads(output.stdout)
            report = path.with_name("report.json")
            if report.exists():
                want = json.loads(report.read_text())["metrics"]
                assert job["summary"]["paths"] == want["paths"], path
                assert math.isclose(job["summary"]["pen_down_mm"], want["pen_down_mm"],
                                    rel_tol=1e-10, abs_tol=1e-5), path
                comparisons += 1
            # Preview omits the unknown initial carriage-to-first-point travel;
            # converter reports assume an initial carriage at (0,0).
            shutil.rmtree(Path(spool) / job["id"])
    print(f"{len(sources)} HPGL files parsed; {comparisons} converter reports match path counts and pen-down distance.")


if __name__ == "__main__":
    main()
