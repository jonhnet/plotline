//! Deterministic planar mazes. Inspired by Jon Howell's maze-harvester (GPL-3.0).
//! New implementation: integer tiling topology, seeded Prim tree, improving
//! cycle swaps. No raster tracing, geometric all-pairs tests or JVM required.
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, VecDeque};

type Vertex = (i32, i32);
type Point = [f64; 2];
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Mask {
    pub width: usize,
    pub height: usize,
    pub pixels: Vec<u8>,
    #[serde(default)]
    pub name: String,
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Options {
    pub columns: usize,
    pub rows: usize,
    pub seed: u32,
    pub stretch: f64,
    pub exits: [Point; 2],
    #[serde(default)]
    pub mask: Option<Mask>,
}
#[derive(Clone)]
struct Cell {
    center: Point,
    edges: Vec<usize>,
}
#[derive(Clone)]
struct Edge {
    ends: [Vertex; 2],
    cells: Vec<usize>,
    open: bool,
}
struct Grid {
    cells: Vec<Cell>,
    edges: Vec<Edge>,
    sy: f64,
    bounds: [f64; 4],
}
#[derive(Serialize)]
pub struct Drawing {
    pub paths: Vec<Stroke>,
    pub solution: Vec<Point>,
    pub bounds: [f64; 4],
    pub cells: usize,
    pub solution_cells: usize,
    pub shortest_cells: usize,
    pub achieved_stretch: f64,
    pub target_reached: bool,
    pub swaps: usize,
    pub edge_length: f64,
    pub clearance: f64,
}
#[derive(Serialize)]
pub struct Stroke {
    role: usize,
    points: Vec<Point>,
}
struct Random(u64);
impl Random {
    fn index(&mut self, n: usize) -> usize {
        // SplitMix64; stable across machines and Rust/library revisions.
        self.0 = self.0.wrapping_add(0x9e3779b97f4a7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
        ((z ^ (z >> 31)) % n as u64) as usize
    }
}
impl Grid {
    fn point(&self, p: Vertex) -> Point {
        [p.0 as f64 * 0.5, p.1 as f64 * self.sy]
    }
    fn other(&self, e: usize, c: usize) -> Option<usize> {
        self.edges[e].cells.iter().copied().find(|&v| v != c)
    }
    fn route(&self, start: usize, end: usize, tree: bool) -> Vec<usize> {
        let mut parents = vec![usize::MAX; self.cells.len()];
        parents[start] = start;
        let mut q = VecDeque::from([start]);
        while let Some(c) = q.pop_front() {
            if c == end {
                break;
            }
            for &e in &self.cells[c].edges {
                if tree && !self.edges[e].open {
                    continue;
                }
                if let Some(n) = self.other(e, c) {
                    if parents[n] == usize::MAX {
                        parents[n] = c;
                        q.push_back(n);
                    }
                }
            }
        }
        if parents[end] == usize::MAX {
            return vec![];
        }
        let mut path = vec![end];
        while *path.last().unwrap() != start {
            path.push(parents[*path.last().unwrap()]);
        }
        path.reverse();
        path
    }
    fn grow(&mut self, rng: &mut Random) -> Result<()> {
        let start = rng.index(self.cells.len());
        let mut reached = vec![false; self.cells.len()];
        let mut queued = reached.clone();
        let mut frontier = vec![start];
        queued[start] = true;
        let mut count = 0;
        while !frontier.is_empty() {
            let i = rng.index(frontier.len());
            let c = frontier.swap_remove(i);
            let eligible: Vec<_> = self.cells[c]
                .edges
                .iter()
                .copied()
                .filter(|&e| self.other(e, c).is_some_and(|n| reached[n]))
                .collect();
            if !eligible.is_empty() {
                self.edges[eligible[rng.index(eligible.len())]].open = true;
            }
            reached[c] = true;
            count += 1;
            for &e in &self.cells[c].edges {
                if let Some(n) = self.other(e, c) {
                    if !queued[n] {
                        queued[n] = true;
                        frontier.push(n);
                    }
                }
            }
        }
        ensure!(
            count == self.cells.len(),
            "The mask makes disconnected islands at this cell size. Connect its dark regions, increase cell count, or remove the mask."
        );
        Ok(())
    }
    fn stretch_once(&mut self, route: &[usize]) -> bool {
        // Every off-solution branch has exactly one attachment to the solution.
        // One multi-source traversal labels all of them; each closed door then
        // needs O(1) work to measure its possible detour.
        let mut root = vec![usize::MAX; self.cells.len()];
        let mut depth = vec![0usize; self.cells.len()];
        let mut q = VecDeque::new();
        for (i, &c) in route.iter().enumerate() {
            root[c] = i;
            q.push_back(c);
        }
        while let Some(c) = q.pop_front() {
            for &e in &self.cells[c].edges {
                if !self.edges[e].open {
                    continue;
                }
                if let Some(n) = self.other(e, c) {
                    if root[n] == usize::MAX {
                        root[n] = root[c];
                        depth[n] = depth[c] + 1;
                        q.push_back(n);
                    }
                }
            }
        }
        let mut best = None;
        let mut gain = 0;
        for (i, e) in self.edges.iter().enumerate() {
            if e.open || e.cells.len() != 2 {
                continue;
            }
            let (a, b) = (e.cells[0], e.cells[1]);
            if root[a] == root[b] {
                continue;
            }
            let delta = (depth[a] + depth[b] + 1) as isize - root[a].abs_diff(root[b]) as isize;
            if delta > gain {
                gain = delta;
                best = Some((i, root[a].min(root[b])));
            }
        }
        let Some((add, k)) = best else {
            return false;
        };
        let remove = *self.cells[route[k]]
            .edges
            .iter()
            .find(|&&e| self.edges[e].open && self.other(e, route[k]) == Some(route[k + 1]))
            .unwrap();
        self.edges[remove].open = false;
        self.edges[add].open = true;
        true
    }
}
fn grid(family: &str, p: &Options) -> Result<Grid> {
    ensure!(
        ["square", "hexagon", "triangle"].contains(&family),
        "Unknown maze geometry"
    );
    ensure!(
        (2..=160).contains(&p.columns)
            && (2..=160).contains(&p.rows)
            && p.columns * p.rows <= 16000,
        "Use 2–160 rows/columns, with at most 16,000 cells"
    );
    ensure!(
        p.stretch.is_finite() && (0.0..=12.0).contains(&p.stretch),
        "Stretch must be between 0 and 12"
    );
    ensure!(
        p.exits
            .iter()
            .flatten()
            .all(|v| v.is_finite() && (0.0..=1.0).contains(v)),
        "Entrance positions must be between 0 and 1"
    );
    if let Some(m) = &p.mask {
        ensure!(
            (1..=512).contains(&m.width)
                && (1..=512).contains(&m.height)
                && m.pixels.len() == m.width * m.height
                && m.pixels.iter().all(|&v| v <= 1)
                && m.name.len() <= 240,
            "Invalid mask; use at most 512 × 512 binary pixels"
        );
    }
    let sy = if family == "square" {
        0.5
    } else {
        3f64.sqrt() / 2.0
    };
    let mut polygons = vec![];
    let mut bounds = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for y in 0..p.rows as i32 {
        for x in 0..p.columns as i32 {
            let v: Vec<Vertex> = match family {
                "square" => vec![
                    (2 * x, 2 * y),
                    (2 * x + 2, 2 * y),
                    (2 * x + 2, 2 * y + 2),
                    (2 * x, 2 * y + 2),
                ],
                "hexagon" => [(2, 0), (1, 1), (-1, 1), (-2, 0), (-1, -1), (1, -1)]
                    .iter()
                    .map(|&(a, b)| (3 * x + a, 2 * y + x % 2 + b))
                    .collect(),
                _ if (x + y) % 2 == 0 => vec![(x, y), (x + 2, y), (x + 1, y + 1)],
                _ => vec![(x + 1, y), (x + 2, y + 1), (x, y + 1)],
            };
            for &(a, b) in &v {
                let px = a as f64 * 0.5;
                let py = b as f64 * sy;
                bounds[0] = bounds[0].min(px);
                bounds[1] = bounds[1].min(py);
                bounds[2] = bounds[2].max(px);
                bounds[3] = bounds[3].max(py);
            }
            polygons.push(v);
        }
    }
    let mut g = Grid {
        cells: vec![],
        edges: vec![],
        sy,
        bounds,
    };
    let mut lookup = BTreeMap::new();
    for v in polygons {
        let center = [
            v.iter().map(|p| p.0 as f64 * 0.5).sum::<f64>() / v.len() as f64,
            v.iter().map(|p| p.1 as f64 * sy).sum::<f64>() / v.len() as f64,
        ];
        if let Some(m) = &p.mask {
            let x = (((center[0] - bounds[0]) / (bounds[2] - bounds[0]) * m.width as f64) as usize)
                .min(m.width - 1);
            let y = (((center[1] - bounds[1]) / (bounds[3] - bounds[1]) * m.height as f64)
                as usize)
                .min(m.height - 1);
            if m.pixels[y * m.width + x] == 0 {
                continue;
            }
        }
        let c = g.cells.len();
        let mut edges = vec![];
        for i in 0..v.len() {
            let a = v[i];
            let b = v[(i + 1) % v.len()];
            let key = [a.min(b), a.max(b)];
            let e = *lookup.entry(key).or_insert_with(|| {
                let i = g.edges.len();
                g.edges.push(Edge {
                    ends: key,
                    cells: vec![],
                    open: false,
                });
                i
            });
            g.edges[e].cells.push(c);
            edges.push(e);
        }
        g.cells.push(Cell { center, edges });
    }
    ensure!(
        g.cells.len() >= 2,
        "The mask leaves fewer than two cells. Dark pixels form the maze; white or transparent pixels are holes."
    );
    Ok(g)
}
fn exit(g: &Grid, target: Point, exclude: Option<usize>) -> Result<usize> {
    let [x, y, x2, y2] = g.bounds;
    let target = [x + target[0] * (x2 - x), y + target[1] * (y2 - y)];
    let found = g
        .edges
        .iter()
        .enumerate()
        .filter(|(_, e)| e.cells.len() == 1 && Some(e.cells[0]) != exclude)
        .min_by(|(_, a), (_, b)| {
            let distance = |e: &Edge| {
                let a = g.point(e.ends[0]);
                let b = g.point(e.ends[1]);
                ((a[0] + b[0]) / 2.0 - target[0]).powi(2)
                    + ((a[1] + b[1]) / 2.0 - target[1]).powi(2)
            };
            distance(a).total_cmp(&distance(b))
        });
    found
        .map(|(i, _)| i)
        .ok_or_else(|| anyhow::anyhow!("Choose entrances in different cells"))
}
fn walls(g: &Grid, exits: [usize; 2]) -> Vec<Stroke> {
    // Trace maximal chains through degree-two vertices; draw each wall once.
    let mut at: BTreeMap<Vertex, Vec<usize>> = BTreeMap::new();
    let mut used = vec![true; g.edges.len()];
    for (i, e) in g.edges.iter().enumerate() {
        if !e.open && !exits.contains(&i) {
            used[i] = false;
            for &v in &e.ends {
                at.entry(v).or_default().push(i);
            }
        }
    }
    let mut paths = vec![];
    let starts: Vec<_> = at
        .iter()
        .filter(|(_, es)| es.len() != 2)
        .flat_map(|(&v, es)| es.iter().map(move |&e| (v, e)))
        .chain(
            at.iter()
                .flat_map(|(&v, es)| es.iter().map(move |&e| (v, e))),
        )
        .collect();
    for (v, first) in starts {
        if used[first] {
            continue;
        }
        let mut points = vec![g.point(v)];
        let mut v = v;
        let mut e = first;
        loop {
            used[e] = true;
            let edge = &g.edges[e];
            v = if edge.ends[0] == v {
                edge.ends[1]
            } else {
                edge.ends[0]
            };
            points.push(g.point(v));
            if at[&v].len() != 2 {
                break;
            }
            let Some(next) = at[&v].iter().copied().find(|&i| !used[i]) else {
                break;
            };
            e = next;
        }
        paths.push(Stroke { role: 0, points });
    }
    paths
}
pub fn generate(family: &str, p: &Options) -> Result<Drawing> {
    let mut g = grid(family, p)?;
    let a = exit(&g, p.exits[0], None)?;
    let b = exit(&g, p.exits[1], Some(g.edges[a].cells[0]))?;
    let (start, end) = (g.edges[a].cells[0], g.edges[b].cells[0]);
    g.grow(&mut Random(p.seed as u64))?;
    let shortest = g.route(start, end, false).len();
    let target = ((shortest - 1) as f64 * p.stretch).ceil() as usize;
    let mut route = g.route(start, end, true);
    let mut swaps = 0;
    while route.len() - 1 < target && swaps < 128 {
        if !g.stretch_once(&route) {
            break;
        }
        swaps += 1;
        route = g.route(start, end, true);
    }
    let midpoint = |e: usize| {
        let a = g.point(g.edges[e].ends[0]);
        let b = g.point(g.edges[e].ends[1]);
        [(a[0] + b[0]) / 2., (a[1] + b[1]) / 2.]
    };
    let mut solution = vec![midpoint(a)];
    solution.extend(route.iter().map(|&c| g.cells[c].center));
    solution.push(midpoint(b));
    Ok(Drawing {
        paths: walls(&g, [a, b]),
        solution,
        bounds: g.bounds,
        cells: g.cells.len(),
        solution_cells: route.len(),
        shortest_cells: shortest,
        achieved_stretch: (route.len() - 1) as f64 / (shortest - 1) as f64,
        target_reached: route.len() - 1 >= target,
        swaps,
        edge_length: 1.,
        clearance: match family {
            "hexagon" => 3f64.sqrt(),
            "triangle" => 3f64.sqrt() / 3.,
            _ => 1.,
        },
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    fn options() -> Options {
        Options {
            columns: 30,
            rows: 23,
            seed: 7,
            stretch: 5.,
            exits: [[0., 0.9], [1., 0.1]],
            mask: None,
        }
    }
    #[test]
    fn tilings_are_connected_trees_and_swaps_strictly_lengthen_the_unique_solution() {
        for family in ["square", "hexagon", "triangle"] {
            for seed in [0, 7, 42] {
                let mut p = options();
                p.seed = seed;
                let mut g = grid(family, &p).unwrap();
                g.grow(&mut Random(seed as u64)).unwrap();
                for _ in 0..30 {
                    assert_eq!(g.edges.iter().filter(|e| e.open).count(), g.cells.len() - 1);
                    let before = g.route(0, g.cells.len() - 1, true);
                    assert!(!before.is_empty());
                    for c in 0..g.cells.len() {
                        assert!(!g.route(0, c, true).is_empty());
                    }
                    if !g.stretch_once(&before) {
                        break;
                    }
                    assert!(g.route(0, g.cells.len() - 1, true).len() > before.len());
                }
            }
        }
    }
    #[test]
    fn seeded_output_and_stretch_are_stable_and_walls_are_not_duplicated() {
        for family in ["square", "hexagon", "triangle"] {
            let p = options();
            let a = generate(family, &p).unwrap();
            let b = generate(family, &p).unwrap();
            assert_eq!(
                serde_json::to_value(&a).unwrap(),
                serde_json::to_value(&b).unwrap()
            );
            let mut edges = std::collections::BTreeSet::new();
            for stroke in &a.paths {
                for pair in stroke.points.windows(2) {
                    let key = |p: Point| ((p[0] * 1e6).round() as i64, (p[1] * 1e6).round() as i64);
                    let a = key(pair[0]);
                    let b = key(pair[1]);
                    assert!(edges.insert([a.min(b), a.max(b)]));
                }
            }
            let mut low = p.clone();
            low.stretch = 0.;
            assert!(a.solution_cells >= generate(family, &low).unwrap().solution_cells);
            low.seed += 1;
            assert_ne!(
                serde_json::to_value(&a).unwrap(),
                serde_json::to_value(generate(family, &low).unwrap()).unwrap()
            );
        }
    }
    #[test]
    fn masks_keep_holes_and_reject_islands_empty_and_malformed_input() {
        let mut p = options();
        p.columns = 12;
        p.rows = 12;
        let mut pixels = vec![1; 144];
        for y in 4..8 {
            for x in 4..8 {
                pixels[y * 12 + x] = 0;
            }
        }
        p.mask = Some(Mask {
            width: 12,
            height: 12,
            pixels,
            name: "ring.png".into(),
        });
        let d = generate("square", &p).unwrap();
        assert_eq!(d.cells, 128);
        for stroke in d.paths {
            for v in stroke.points {
                assert!(!(v[0] > 4. && v[0] < 8. && v[1] > 4. && v[1] < 8.));
            }
        }
        for y in 0..12 {
            p.mask.as_mut().unwrap().pixels[y * 12 + 2] = 0;
        }
        assert!(
            generate("square", &p)
                .err()
                .unwrap()
                .to_string()
                .contains("disconnected")
        );
        p.mask.as_mut().unwrap().pixels.fill(0);
        assert!(generate("square", &p).is_err());
        p.mask.as_mut().unwrap().pixels.pop();
        assert!(generate("square", &p).is_err());
        p.mask = None;
        p.columns = usize::MAX;
        assert!(generate("square", &p).is_err());
    }
}
