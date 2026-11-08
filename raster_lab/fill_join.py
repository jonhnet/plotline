"""Join horizontal fill runs using corridors already covered by their ink.

The connector's full-width rectangle fits within the overlapping round-ended
run footprints, so the connector adds no new painted area.
Small retraces preserve both original endpoints and their round caps.
"""
import math
from collections import defaultdict

from . import engine


def bridge(a, b, diameter, right, max_extra):
    """Return a certified vertical connector and its additional pen travel."""
    left, end = max(a[0][0], b[0][0]), min(a[1][0], b[1][0])
    dy = b[0][1] - a[0][1]
    if not 0 < dy <= diameter-.002:
        return None
    # At every height between the rows, the nearest row is at most dy/2 away.
    # Its circular cap therefore extends at least sqrt(r*r-(dy/2)**2) past
    # either end. Insetting the bridge by r minus that reach puts its entire
    # width inside that row's ink. This also works for very short source runs.
    r = diameter/2
    inset = r-math.sqrt(r*r-(dy/2)**2)+.001
    # Round inward to the same physical grid used by the stored paths.
    low = math.ceil((left+inset)*1000)/1000
    high = math.floor((end-inset)*1000)/1000
    if low > high:
        return None
    x = high if right else low
    side = 1 if right else 0
    extra = abs(a[side][0]-x) + dy + abs(b[side][0]-x)
    if extra > max_extra:
        return None
    return x, extra


def join(paths, diameter, max_extra_mm):
    rows = defaultdict(list)
    for path in paths:
        if len(path) != 2 or path[0][1] != path[1][1]:
            raise ValueError('Fill joining requires two-point horizontal runs')
        left, right = sorted(path)
        rows[left[1]].append([left, right])
    output, previous, chains = [], [], []
    last_y = None
    for row_index, y in enumerate(sorted(rows)):
        current = sorted(rows[y])
        if last_y is not None and y-last_y > diameter-.002:
            output.extend(chains)
            previous, chains = [], []
        # Alternating rows put both sides of a join at the same end of a run.
        previous_right = row_index % 2 == 1
        candidates, first = [], 0
        for j, b in enumerate(current):
            while first < len(previous) and previous[first][1][0] < b[0][0]:
                first += 1
            i = first
            while i < len(previous) and previous[i][0][0] <= b[1][0]:
                found = bridge(previous[i], b, diameter, previous_right, max_extra_mm)
                if found:
                    x, extra = found
                    candidates.append((extra, i, j, x))
                i += 1
        used, incoming = set(), {}
        # Local overlap candidates only; greedy travel cost, not a global route
        # optimizer. Each source run is used exactly once in a continuous chain.
        for _, i, j, x in sorted(candidates):
            if i not in used and j not in incoming:
                used.add(i)
                incoming[j] = i, x
        output.extend(chain for i, chain in enumerate(chains) if i not in used)
        next_chains = []
        for j, b in enumerate(current):
            oriented = b if row_index % 2 == 0 else b[::-1]
            if j in incoming:
                i, x = incoming[j]
                chain = chains[i]
                chain.extend([[x, last_y], [x, y], *oriented])
                next_chains.append(chain)
            else:
                next_chains.append(list(oriented))
        previous, chains, last_y = current, next_chains, y
    output.extend(chains)
    return output


def pen_up_distance(paths):
    return sum(math.dist(a[-1], b[0]) for a, b in zip(paths, paths[1:])) / 1000


def metrics(original, joined):
    return dict(original_runs=len(original), joined_runs=len(joined),
                joins=len(original)-len(joined),
                extra_pen_down_meters=(sum(map(engine.path_length, joined)) - sum(map(engine.path_length, original)))/1000,
                original_pen_up_meters=pen_up_distance(original),
                joined_pen_up_meters=pen_up_distance(joined),
                interpretation='Pen-up travel follows the listed path order, excluding home and carousel moves; not a plotting-time prediction.')
