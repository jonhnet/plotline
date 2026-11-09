use serde::{Deserialize, Serialize};

pub type Point = [f64; 2];
pub type Matrix = [f64; 6];
pub type Color = [f64; 3];

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    Move { p: Point },
    Line { p: Point },
    Cubic { p1: Point, p2: Point, p3: Point },
    Close,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Stroke {
    pub width: f64,
    pub cap: String,
    pub join: String,
    pub miter_limit: f64,
    pub dash_phase: f64,
    pub dashes: Vec<f64>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PaintRole {
    #[default]
    Artwork,
    Text,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Event {
    Fill {
        path: Vec<Command>,
        matrix: Matrix,
        even_odd: bool,
        color: Color,
        #[serde(default)]
        role: PaintRole,
    },
    Stroke {
        path: Vec<Command>,
        matrix: Matrix,
        style: Stroke,
        color: Color,
    },
    /// Preplanned physical strokes. Coverage constrains them but never occludes
    /// sibling channels; a preceding opaque Fill owns the image paint footprint.
    RasterPaths {
        path: Vec<Command>,
        matrix: Matrix,
        color: Color,
        lines: Vec<Vec<Point>>,
    },
    ClipFill {
        path: Vec<Command>,
        matrix: Matrix,
        even_odd: bool,
    },
    ClipStroke {
        path: Vec<Command>,
        matrix: Matrix,
        style: Stroke,
    },
    PopClip,
    Unsupported {
        feature: String,
        detail: String,
    },
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Ink {
    pub id: String,
    pub color: Color,
    pub width: f64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Page {
    pub version: u32,
    pub engine: String,
    pub units: String,
    pub y_axis: String,
    pub bounds: [f64; 4],
    pub events: Vec<Event>,
    /// Optional PDF layer attribution; does not change paint semantics.
    #[serde(default, skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub sources: std::collections::BTreeMap<usize, String>,
    /// Optional document inks. Assignments are one-based, independent of RGB.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub inks: Vec<Ink>,
    #[serde(default, skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub paint_inks: std::collections::BTreeMap<usize, usize>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct PenPath {
    pub paint_id: usize,
    pub pen: usize,
    pub points: Vec<Point>,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FillStyle {
    Hatch,
    Crosshatch,
    Solid,
    None,
    Outline,
    Feathered,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PalettePolicy {
    Exact,
    RedGreenBlack,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StrokeWidthMode {
    Preserve,
    Pen,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub pen_width: f64,
    pub flatten_tolerance: f64,
    pub units_per_mm: f64,
    pub fill_style: FillStyle,
    pub palette_policy: PalettePolicy,
    pub hatch_spacing: Option<f64>,
    pub hatch_angle: Option<f64>,
    pub feather_distance: Option<f64>,
    pub stroke_width_tolerance: Option<f64>,
    /// Extra total thickness permitted for otherwise unrepresentable details,
    /// in pen diameters. Zero preserves strict glyph/fill boundaries.
    pub thin_detail_thickening: f64,
    pub stroke_width_mode: StrokeWidthMode,
    /// Colored strokes may cross other colored strokes. Fills, white paint
    /// and PDF/page clipping retain their ordinary occlusion semantics.
    pub overlap_strokes: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            pen_width: 0.8,
            flatten_tolerance: 0.01,
            units_per_mm: 40.0,
            fill_style: FillStyle::Hatch,
            palette_policy: PalettePolicy::Exact,
            hatch_spacing: None,
            hatch_angle: None,
            feather_distance: None,
            stroke_width_tolerance: None,
            thin_detail_thickening: 0.,
            stroke_width_mode: StrokeWidthMode::Preserve,
            overlap_strokes: false,
        }
    }
}

impl Settings {
    pub fn units_per_pt(self) -> f64 {
        self.units_per_mm * 25.4 / 72.0
    }
    pub fn guard(self) -> f64 {
        // Leave room for PDF curve flattening, round-buffer chords, and HPGL rounding.
        2.0 * self.flatten_tolerance
            + std::f64::consts::SQRT_2 / (2.0 * self.units_per_pt())
            + self.pen_width * 0.001
    }

    /// Candidate geometry may shed detail far below both the curve tolerance
    /// and the rounding guard. Original paint/allowance and footprint audits
    /// retain their full precision. The absolute ceiling is 0.000353 mm.
    pub(crate) fn clearance_tolerance(self) -> f64 {
        0.001_f64
            .min(self.flatten_tolerance / 10.)
            .min(self.guard() / 64.)
    }
}

pub fn is_white(color: Color) -> bool {
    color == [1.0; 3]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clearance_budget_tracks_curve_and_grid_precision() {
        let normal = Settings::default();
        assert_eq!(normal.clearance_tolerance(), 0.001);
        for flatten_tolerance in [0., 0.0001, 0.001, 0.01] {
            for units_per_mm in [40., 4000.] {
                let settings = Settings {
                    flatten_tolerance,
                    units_per_mm,
                    ..normal
                };
                let tolerance = settings.clearance_tolerance();
                assert!(tolerance <= 0.001);
                assert!(tolerance <= flatten_tolerance / 10.);
                assert!(tolerance <= settings.guard() / 64.);
            }
        }
    }
}
