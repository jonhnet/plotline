//! Small downstream summaries of reports that have already passed verification.
use crate::{model::Ink, plan::PaintReport};
use anyhow::{Context, Result, ensure};
use serde::Serialize;
use std::collections::BTreeMap;

#[derive(Default, Serialize)]
pub struct InkTotals {
    pub paths: usize,
    pub pen_down_mm: f64,
    pub unrepresented_area_pt2: f64,
}

#[derive(Default, Serialize)]
pub struct FeatureTotals {
    pub paths: usize,
    pub pen_down_mm: f64,
}

#[derive(Default, Serialize)]
pub struct Verification {
    pub paints_checked: usize,
    pub white_paints: usize,
    pub width_approximations: usize,
    pub max_outside_allowed_area_pt2: f64,
    pub max_uncovered_required_area_pt2: f64,
}

pub fn verification(reports: &[PaintReport]) -> Verification {
    let mut result = Verification::default();
    for paint in reports {
        result.paints_checked += 1;
        result.white_paints += usize::from(paint.white);
        result.width_approximations += usize::from(paint.width_layout.is_some());
        result.max_outside_allowed_area_pt2 = result
            .max_outside_allowed_area_pt2
            .max(paint.outside_allowed_area_pt2);
        result.max_uncovered_required_area_pt2 = result
            .max_uncovered_required_area_pt2
            .max(paint.uncovered_required_area_pt2.unwrap_or(0.));
    }
    result
}

#[derive(Default, Serialize)]
pub struct Summary {
    pub by_ink: BTreeMap<String, InkTotals>,
    pub by_feature: BTreeMap<String, FeatureTotals>,
    pub visible_paints_without_paths: usize,
}

pub fn summarize(reports: &[PaintReport], inks: &[Ink]) -> Result<Summary> {
    let mut result = Summary::default();
    // Retain paint order for exactly the same floating-point accumulation as
    // the previous Python aggregation. Logical ink IDs are not carousel slots.
    for paint in reports {
        if paint.white {
            continue;
        }
        result.visible_paints_without_paths += usize::from(
            paint.representation != "raster_paths"
                && paint.visible_area_pt2.context("Missing ink visibility")? > 0.
                && paint.paths == 0,
        );
        if let Some(pen) = paint.pen_id {
            ensure!(
                pen > 0 && (inks.is_empty() || pen <= inks.len()),
                "Invalid summary ink assignment"
            );
            let id = if inks.is_empty() {
                pen.to_string()
            } else {
                inks[pen - 1].id.clone()
            };
            let row = result.by_ink.entry(id).or_default();
            row.paths += paint.paths;
            row.pen_down_mm += paint.pen_down_mm;
            // Raster coverage is the image clipping footprint, not a solid
            // ink demand. Its deliberate paper gaps are not missing detail.
            if paint.representation != "raster_paths" {
                row.unrepresented_area_pt2 += paint
                    .unrepresented_area_pt2
                    .context("Missing vector area diagnostic")?;
            }
        }
        let row = result.by_feature.entry(paint.source.clone()).or_default();
        row.paths += paint.paths;
        row.pen_down_mm += paint.pen_down_mm;
    }
    Ok(result)
}

/// Count actual pen-down runs per ink. Split connecting travel equally between
/// its source features; per-feature path counts remain original source paths.
pub fn add_continuity(
    totals: &mut Summary,
    continuity: &crate::continuity::Report,
    reports: &[PaintReport],
    inks: &[Ink],
) -> Result<()> {
    let paints: BTreeMap<_, _> = reports.iter().map(|p| (p.id, p)).collect();
    for connection in &continuity.connections {
        let id = if inks.is_empty() {
            connection.pen.to_string()
        } else {
            inks[connection.pen - 1].id.clone()
        };
        let ink = totals
            .by_ink
            .get_mut(&id)
            .context("Unknown connection ink")?;
        ink.paths -= 1;
        ink.pen_down_mm += connection.pen_down_mm;
        for paint_id in [connection.from_paint, connection.to_paint] {
            let source = &paints
                .get(&paint_id)
                .context("Unknown connection paint")?
                .source;
            totals
                .by_feature
                .get_mut(source)
                .context("Unknown connection feature")?
                .pen_down_mm += connection.pen_down_mm / 2.;
        }
    }
    Ok(())
}
