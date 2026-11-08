//! Explicit simulation palette. Source colors never change the occlusion model.
use crate::model::{Color, PalettePolicy, is_white};

pub const BLACK: Color = [0.0, 0.0, 0.0];
pub const RED: Color = [1.0, 0.0, 0.0];
pub const GREEN: Color = [0.0, 0.5, 0.0];

pub fn map(color: Color, policy: PalettePolicy) -> Color {
    if is_white(color) || matches!(policy, PalettePolicy::Exact) {
        return color;
    }
    // A deliberately simple hue policy: neutral, blue and purple use black;
    // red/orange/brown use red; green-dominant colors use green. Lightness is
    // discarded. This is not a perceptual nearest-color or ink calibration model.
    let [r, g, b] = color;
    if r.max(g).max(b) - r.min(g).min(b) < 0.08 {
        BLACK
    } else if r > g && r > b {
        RED
    } else if g > r && g > b {
        GREEN
    } else {
        BLACK
    }
}
