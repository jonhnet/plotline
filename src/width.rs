//! Nominal width selection, independent of PDF visibility and HPGL rounding.
use anyhow::{Result, ensure};
use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct Layout {
    pub requested_width_pt: f64,
    pub ideal_passes: usize,
    pub passes: usize,
    pub ideal_spacing_pt: f64,
    pub ideal_span_pt: f64,
    pub ideal_width_error_pt: f64,
    pub ideal_overlap_pt: f64,
    pub planned_spacing_pt: f64,
    pub planned_span_pt: f64,
    /// A single source traversal was shifted locally to clear clips/later paint.
    pub locally_adjusted: bool,
}

pub fn layout(width: f64, pen: f64, tolerance: f64) -> Result<Option<Layout>> {
    ensure!(
        width.is_finite() && width > 0.0 && pen.is_finite() && pen > 0.0,
        "Invalid stroke/pen width"
    );
    ensure!(
        tolerance.is_finite() && (0.0..=1.0).contains(&tolerance),
        "Stroke width tolerance must be between 0 and 1"
    );
    if width < pen * (1.0 - tolerance) - pen * 1e-6 {
        return Ok(None);
    }
    // Equality at the shortfall threshold chooses the extra pass. Allow for
    // float32 PDF widths (e.g. 0.7 arriving as 0.699999988).
    let passes = if tolerance == 0.0 {
        (width / pen - 1e-6).ceil().max(1.0)
    } else {
        (width / pen + 1.0 - tolerance + 1e-6).floor().max(1.0)
    };
    ensure!(passes <= 10_000.0, "Stroke pass count exceeds probe limit");
    let passes = passes as usize;
    let span = if passes == 1 {
        pen
    } else {
        width.min(passes as f64 * pen)
    };
    let spacing = if passes == 1 {
        0.0
    } else {
        (span - pen) / (passes - 1) as f64
    };
    Ok(Some(Layout {
        requested_width_pt: width,
        ideal_passes: passes,
        passes,
        ideal_spacing_pt: spacing,
        ideal_span_pt: span,
        ideal_width_error_pt: span - width,
        ideal_overlap_pt: if passes > 1 { pen - spacing } else { 0.0 },
        planned_spacing_pt: spacing,
        planned_span_pt: span,
        locally_adjusted: false,
    }))
}
