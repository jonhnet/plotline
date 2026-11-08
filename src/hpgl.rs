//! Offline subset only; no device connection, speed commands, or hardware profile.
use crate::{model::*, plan::Plan};
use anyhow::{Result, bail, ensure};
use serde::Serialize;

pub fn quantize_points(points: &[Point], bounds: [f64; 4], settings: Settings) -> Vec<Point> {
    let scale = settings.units_per_pt();
    let mut grid: Vec<[i32; 2]> = points
        .iter()
        .map(|p| {
            [
                ((p[0] - bounds[0]) * scale).round() as i32,
                ((bounds[3] - p[1]) * scale).round() as i32,
            ]
        })
        .collect();
    grid.dedup();
    grid.into_iter()
        .map(|p| {
            [
                p[0] as f64 / scale + bounds[0],
                bounds[3] - p[1] as f64 / scale,
            ]
        })
        .collect()
}

pub fn encode(plan: &Plan) -> Result<(String, Vec<PenPath>)> {
    encode_paths(plan, &plan.paths)
}

pub(crate) fn encode_paths(plan: &Plan, paths: &[PenPath]) -> Result<(String, Vec<PenPath>)> {
    let scale = plan.settings.units_per_pt();
    let [x0, _, _, y1] = plan.bounds;
    let mut result = String::from("IN;PA;\n");
    // Interchange metadata for previewers. Physical width is not a motion
    // command on this pen plotter; the controller removes CO before feeding it.
    for (i, color) in plan.palette.iter().enumerate() {
        result.push_str(&format!(
            "CO\"pen id={} r={} g={} b={} width_mm={:.6}\";\n",
            i + 1,
            (color[0] * 255.).round() as u8,
            (color[1] * 255.).round() as u8,
            (color[2] * 255.).round() as u8,
            plan.pen_widths[i] * 25.4 / 72.
        ));
    }
    let mut expected = Vec::new();
    let mut last_pen = 0;
    for path in paths {
        let mut points: Vec<[i32; 2]> = path
            .points
            .iter()
            .map(|p| {
                [
                    ((p[0] - x0) * scale).round() as i32,
                    ((y1 - p[1]) * scale).round() as i32,
                ]
            })
            .collect();
        points.dedup();
        if points.len() < 2 {
            continue;
        }
        if last_pen != path.pen {
            result.push_str(&format!("SP{};", path.pen));
            last_pen = path.pen;
        }
        result.push_str(&format!("PU{},{};PD", points[0][0], points[0][1]));
        for (i, p) in points.iter().skip(1).enumerate() {
            if i > 0 {
                result.push(',');
            }
            result.push_str(&format!("{},{}", p[0], p[1]));
        }
        result.push_str(";PU;\n");
        expected.push(PenPath {
            paint_id: path.paint_id,
            pen: path.pen,
            points: points
                .iter()
                .map(|p| [p[0] as f64 / scale + x0, y1 - p[1] as f64 / scale])
                .collect(),
        });
    }
    result.push_str("PU;SP0;\n");
    Ok((result, expected))
}

#[derive(Debug, Default, Serialize)]
pub struct Metrics {
    pub paths: usize,
    pub pen_selections: usize,
    pub pen_down_mm: f64,
    pub pen_up_mm: f64,
    pub total_y_travel_mm: f64,
}

// Independent state machine reads only emitted command bytes. Source IDs are
// joined back by the caller after checking paths/counts/pen assignments.
pub fn decode(text: &str, bounds: [f64; 4], settings: Settings) -> Result<(Vec<PenPath>, Metrics)> {
    ensure!(text.trim_end().ends_with(';'), "Unterminated HPGL command");
    let mut initialized = false;
    let mut absolute = false;
    let mut down = false;
    let mut pen = 0;
    let mut position = [0.0, 0.0];
    let mut current: Vec<Point> = Vec::new();
    let mut paths = Vec::new();
    let mut metrics = Metrics::default();
    let scale = settings.units_per_pt();
    let to_page = |p: Point| [p[0] / scale + bounds[0], bounds[3] - p[1] / scale];
    for raw in text.split(';') {
        let command = raw.trim();
        if command.is_empty() {
            continue;
        }
        ensure!(
            command.is_ascii() && command.len() >= 2,
            "Malformed HPGL command"
        );
        let (op, args) = command.split_at(2);
        if op == "IN" {
            ensure!(!initialized && args.is_empty(), "Unexpected IN");
            initialized = true;
            continue;
        }
        ensure!(initialized, "HPGL must start with IN");
        if op == "CO" {
            ensure!(
                args.starts_with('"') && args.ends_with('"') && args.len() >= 2,
                "Malformed HPGL comment"
            );
            continue;
        }
        if op == "PU" || op == "SP" {
            if current.len() > 1 {
                paths.push(PenPath {
                    paint_id: 0,
                    pen,
                    points: std::mem::take(&mut current),
                });
            }
            current.clear();
        }
        match op {
            "PA" => {
                ensure!(args.is_empty(), "Only mode-only PA is supported");
                absolute = true;
            }
            "SP" => {
                ensure!(!down, "Changing pens while down");
                let next: usize = args.parse()?;
                ensure!(next <= 8, "Pen out of range");
                if next != pen && next != 0 {
                    metrics.pen_selections += 1;
                }
                pen = next;
            }
            "PU" | "PD" => {
                ensure!(absolute, "PA required before moves");
                down = op == "PD";
                ensure!(!down || pen > 0, "Drawing without a selected pen");
                if down && current.is_empty() {
                    current.push(to_page(position));
                }
                if !args.is_empty() {
                    let coords: Vec<i32> = args
                        .split(',')
                        .map(str::parse)
                        .collect::<std::result::Result<_, _>>()?;
                    ensure!(coords.len().is_multiple_of(2), "Odd coordinate count");
                    for p in coords.chunks_exact(2) {
                        let next = [p[0] as f64, p[1] as f64];
                        let distance = (next[0] - position[0]).hypot(next[1] - position[1])
                            / settings.units_per_mm;
                        if down {
                            metrics.pen_down_mm += distance;
                            current.push(to_page(next));
                        } else {
                            metrics.pen_up_mm += distance;
                        }
                        metrics.total_y_travel_mm +=
                            (next[1] - position[1]).abs() / settings.units_per_mm;
                        position = next;
                    }
                }
            }
            _ => bail!("Unsupported HPGL command {op}"),
        }
    }
    ensure!(
        initialized && absolute && !down && pen == 0,
        "Incomplete HPGL final state"
    );
    metrics.paths = paths.len();
    Ok((paths, metrics))
}

pub fn readback(plan: &Plan, text: &str, expected: &[PenPath]) -> Result<(Vec<PenPath>, Metrics)> {
    let (mut decoded, metrics) = decode(text, plan.bounds, plan.settings)?;
    ensure!(decoded.len() == expected.len(), "HPGL path count changed");
    for (actual, want) in decoded.iter_mut().zip(expected) {
        ensure!(
            actual.pen == want.pen && actual.points == want.points,
            "HPGL path round-trip mismatch"
        );
        actual.paint_id = want.paint_id;
    }
    Ok((decoded, metrics))
}
