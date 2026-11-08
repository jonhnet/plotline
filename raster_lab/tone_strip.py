"""Render a synthetic highlight ramp; no challenge artwork is used."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from . import engine
from .waves import wave_paths


def make_strip(output):
    engine.check_dependencies()
    recipes = {r['id']: r for r in json.loads(Path(__file__).with_name('experiments.json').read_text())}
    ids = ['highlight-reference', 'highlight-low-cutoff', 'highlight-short', 'highlight-long']
    channel = np.tile(np.linspace(0, .5, 1600, dtype=np.float32), (200, 1))
    fig, axes = plt.subplots(4, 1, figsize=(14, 8), sharex=True, layout='constrained')
    report = {}
    for ax, id in zip(axes, ids):
        recipe = recipes[id]
        drawing = engine.Drawing(160, 20, .3, {})
        drawing.paths['K'] = wave_paths(channel, drawing, 0, recipe)
        preview, coverage = engine.render(drawing, 240)
        metrics = engine.metrics(drawing)
        report[id] = dict(metrics=metrics, preview_coverage=coverage, recipe=recipe)
        ax.imshow(preview, extent=[0, 35, 0, 20], aspect=35/160, interpolation='nearest')
        ax.set_yticks([])
        ax.set_title(f"{recipe['name']}  |  {metrics['paths']:,} runs, {metrics['meters']:.2f} m", loc='left', fontsize=11)
        ax.set_xlim(0,35)
    axes[-1].set_xticks(np.arange(0,36,5))
    axes[-1].set_xlabel('Requested nominal ink area (%) — 70% demand × source channel; one straight carrier = 25%')
    fig.suptitle('Highlight screening: 0.30 mm pen, 1.2 mm rows, 6.3″ × 0.8″ per strip\nRound caps count in the diffusion budget; optical ink response is uncalibrated.', fontsize=12)
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output/'highlight-tone-strip.png', dpi=160)
    plt.close(fig)
    (output/'highlight-tone-strip.json').write_text(json.dumps(report,indent=2)+'\n')
    return {id:dict(paths=v['metrics']['paths'],meters=v['metrics']['meters']) for id,v in report.items()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(make_strip(args.output),indent=2))
