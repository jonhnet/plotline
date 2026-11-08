//! Reorder/reverse existing paths only; this stage never invents ink geometry.
use crate::{hpgl, model::*, plan::Plan};
use anyhow::Result;
use rstar::{AABB, PointDistance, RTree, RTreeObject};
use serde::Serialize;

pub const Y_PENALTY: f64 = 0.5;

#[derive(Serialize)]
pub struct RoutingReport {
    pub applied: bool,
    pub y_penalty: f64,
    pub before: hpgl::Metrics,
    pub candidate: hpgl::Metrics,
}

fn cost(a: Point, b: Point) -> f64 {
    (b[0] - a[0]).hypot(b[1] - a[1]) + Y_PENALTY * (b[1] - a[1]).abs()
}

fn objective(m: &hpgl::Metrics) -> f64 {
    // Pen-down Y travel is invariant under these changes, so total Y is usable.
    m.pen_up_mm + Y_PENALTY * m.total_y_travel_mm
}

#[derive(Clone, Copy, PartialEq)]
struct Entry {
    point: Point,
    path: usize,
    vertex: usize,
}

impl RTreeObject for Entry {
    type Envelope = AABB<Point>;
    fn envelope(&self) -> Self::Envelope {
        AABB::from_point(self.point)
    }
}

impl PointDistance for Entry {
    fn distance_2(&self, point: &Point) -> f64 {
        (self.point[0] - point[0]).powi(2) + (self.point[1] - point[1]).powi(2)
    }
}

fn entries(path: &PenPath, index: usize) -> Vec<Entry> {
    let n = path.points.len();
    let indices: Vec<_> = if path.points.first() == path.points.last() {
        (0..n - 1).step_by(((n - 1) / 64).max(1)).collect()
    } else {
        vec![0, n - 1]
    };
    indices
        .into_iter()
        .map(|vertex| Entry {
            point: path.points[vertex],
            path: index,
            vertex,
        })
        .collect()
}

pub fn reorder(paths: &[PenPath], origin: Point, pens: usize) -> Vec<PenPath> {
    let mut result = Vec::with_capacity(paths.len());
    let mut position = origin;
    for pen in 1..=pens {
        let samples = paths
            .iter()
            .enumerate()
            .filter(|(_, p)| p.pen == pen)
            .flat_map(|(i, p)| entries(p, i))
            .collect();
        let mut index = RTree::bulk_load(samples);
        while index.size() != 0 {
            let mut best = (f64::INFINITY, usize::MAX, usize::MAX);
            for entry in index.nearest_neighbor_iter(position) {
                // Euclidean distance is a lower bound on our Y-weighted cost.
                // Once it exceeds the best score, all remaining entries lose.
                if entry.distance_2(&position) > best.0 * best.0 * (1.0 + 1e-14) {
                    break;
                }
                let candidate = (cost(position, entry.point), entry.path, entry.vertex);
                if candidate < best {
                    best = candidate;
                }
            }
            for entry in entries(&paths[best.1], best.1) {
                index.remove(&entry);
            }
            let mut next = paths[best.1].clone();
            let n = next.points.len();
            if next.points.first() == next.points.last() {
                let k = best.2;
                next.points = next.points[k..n - 1]
                    .iter()
                    .chain(&next.points[..=k])
                    .copied()
                    .collect();
            } else if best.2 != 0 {
                next.points.reverse();
            }
            position = *next.points.last().unwrap();
            result.push(next);
        }
    }
    result
}

pub fn optimize(plan: &mut Plan) -> Result<RoutingReport> {
    let (before_text, _) = hpgl::encode(plan)?;
    let (_, before) = hpgl::decode(&before_text, plan.bounds, plan.settings)?;
    let candidate = reorder(
        &plan.paths,
        [plan.bounds[0], plan.bounds[3]],
        plan.palette.len(),
    );
    let old = std::mem::replace(&mut plan.paths, candidate);
    let (candidate_text, _) = hpgl::encode(plan)?;
    let (_, candidate) = hpgl::decode(&candidate_text, plan.bounds, plan.settings)?;
    // Judge the integer-coordinate HPGL, not just an idealized floating route.
    let applied = objective(&candidate) <= objective(&before)
        && candidate.pen_selections <= before.pen_selections;
    if !applied {
        plan.paths = old;
    }
    Ok(RoutingReport {
        applied,
        y_penalty: Y_PENALTY,
        before,
        candidate,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn indexed_route_matches_exhaustive_weighted_nearest_choice() {
        let paths: Vec<_> = (0..180)
            .map(|i| {
                let x = (i * 17 % 90) as f64;
                let y = (i * 31 % 80) as f64;
                PenPath {
                    paint_id: i,
                    pen: i % 3 + 1,
                    points: if i % 2 == 0 {
                        vec![[x, y], [x + 3., y], [x + 3., y + 2.], [x, y]]
                    } else {
                        vec![[x, y], [x + 5., y + 1.]]
                    },
                }
            })
            .collect();
        let origin = [0., 100.];
        let ordered = reorder(&paths, origin, 3);
        let mut used = vec![false; paths.len()];
        let mut position = origin;
        assert_eq!(ordered.len(), paths.len());
        for actual in ordered {
            let mut best = (f64::INFINITY, usize::MAX, usize::MAX);
            for (i, p) in paths.iter().enumerate() {
                if p.pen != actual.pen || used[i] {
                    continue;
                }
                for entry in entries(p, i) {
                    let score = (cost(position, entry.point), i, entry.vertex);
                    if score < best {
                        best = score;
                    }
                }
            }
            assert_eq!(actual.paint_id, best.1);
            assert_eq!(actual.points[0], paths[best.1].points[best.2]);
            used[actual.paint_id] = true;
            position = *actual.points.last().unwrap();
        }
    }
}
