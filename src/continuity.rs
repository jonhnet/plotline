//! Join routed, quantized strokes only where their round end caps overlap and
//! the complete connecting pen footprint fits their surviving allowed ink.
//!
//! Source paths keep their paint IDs for coverage and mileage accounting.
//! Physical runs may span paints; validate their exact HPGL readback separately
//! and record each connection's two source paints rather than inventing an owner.
use crate::{geometry as g, hpgl, model::*, plan::Plan};
use anyhow::{Context, Result};
use geos::Geom;
use serde::Serialize;
use std::collections::HashMap;

#[derive(Debug, Serialize)]
pub struct Connection {
    pub from_paint: usize,
    pub to_paint: usize,
    pub pen: usize,
    pub pen_down_mm: f64,
    pub outside_allowed_area_pt2: f64,
}

#[derive(Debug, Default, Serialize)]
pub struct Report {
    pub source_paths: usize,
    pub physical_runs: usize,
    pub lifts_removed: usize,
    pub touching_joins: usize,
    pub overlap_candidates: usize,
    pub overlap_joins: usize,
    pub pen_down_added_mm: f64,
    pub max_outside_allowed_area_pt2: f64,
    pub connections: Vec<Connection>,
}

pub struct Encoding {
    pub commands: String,
    pub source_paths: Vec<PenPath>,
    pub report: Report,
    expected_runs: Vec<PenPath>,
}

impl Encoding {
    /// Read the actual bytes and require every coordinate/pen/run to match the
    /// concatenation of the original paths and checked connecting segments.
    pub fn readback(&self, plan: &Plan) -> Result<(Vec<PenPath>, hpgl::Metrics)> {
        hpgl::readback(plan, &self.commands, &self.expected_runs)
    }
}

pub fn encode(plan: &Plan) -> Result<Encoding> {
    let (_, source_paths) = hpgl::encode(plan)?;
    let paints: HashMap<_, _> = plan.paints.iter().map(|p| (p.id, p)).collect();
    let mut report = Report {
        source_paths: source_paths.len(),
        ..Report::default()
    };
    let mut runs: Vec<PenPath> = Vec::with_capacity(source_paths.len());
    for (index, path) in source_paths.iter().enumerate() {
        let mut connection = None;
        if let Some(previous) = index.checked_sub(1).map(|i| &source_paths[i])
            && previous.pen == path.pen
        {
            let a = *previous.points.last().unwrap();
            let b = path.points[0];
            let distance = (a[0] - b[0]).hypot(a[1] - b[1]);
            let width = plan.pen_widths[path.pen - 1];
            // Farther moves remain pen-up even inside large filled regions:
            // this pass joins overlapping strokes, it does not add new hatching.
            if distance == 0. {
                report.touching_joins += 1;
                connection = Some(0.);
            } else if distance <= width {
                report.overlap_candidates += 1;
                let before = paints
                    .get(&previous.paint_id)
                    .context("Unknown source paint")?;
                let after = paints.get(&path.paint_id).context("Unknown source paint")?;
                // Highlight diffusion deliberately leaves gaps. Raster joining
                // was already constrained to its original ink by the renderer.
                if before.is_raster || after.is_raster {
                    runs.push(path.clone());
                    continue;
                }
                let footprint = g::round_buffer(&g::line(&[a, b])?, width / 2.)?;
                // Subtract independently; no global ink union or all-pairs
                // search. Both masks already exclude clips and later paint.
                let outside = footprint
                    .difference(&before.allowed)?
                    .difference(&after.allowed)?
                    .area()?;
                if outside <= 1e-7 {
                    report.overlap_joins += 1;
                    connection = Some(outside);
                }
            }
            if let Some(outside) = connection {
                let run = runs.last_mut().unwrap();
                if a != b {
                    run.points.push(b);
                }
                run.points.extend_from_slice(&path.points[1..]);
                let mm = distance * 25.4 / 72.;
                report.pen_down_added_mm += mm;
                report.max_outside_allowed_area_pt2 =
                    report.max_outside_allowed_area_pt2.max(outside);
                report.connections.push(Connection {
                    from_paint: previous.paint_id,
                    to_paint: path.paint_id,
                    pen: path.pen,
                    pen_down_mm: mm,
                    outside_allowed_area_pt2: outside,
                });
            }
        }
        if connection.is_none() {
            runs.push(path.clone());
        }
    }
    report.physical_runs = runs.len();
    report.lifts_removed = report.source_paths - report.physical_runs;
    let (commands, expected_runs) = hpgl::encode_paths(plan, &runs)?;
    Ok(Encoding {
        commands,
        source_paths,
        report,
        expected_runs,
    })
}
