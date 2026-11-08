//! Shared paper model for IPP, PDF defaults and device registration.
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

pub const NAMED: &[(&str, f64, f64)] = &[
    ("na_letter_8.5x11in", 215.9, 279.4),
    ("na_legal_8.5x14in", 215.9, 355.6),
    ("na_ledger_11x17in", 279.4, 431.8),
    ("na_c_17x22in", 431.8, 558.8),
    ("na_d_22x34in", 558.8, 863.6),
    ("na_e_34x44in", 863.6, 1117.6),
    ("na_arch-c_18x24in", 457.2, 609.6),
    ("na_arch-d_24x36in", 609.6, 914.4),
    ("na_arch-e_36x48in", 914.4, 1219.2),
    ("iso_a4_210x297mm", 210., 297.),
    ("iso_a3_297x420mm", 297., 420.),
    ("iso_a2_420x594mm", 420., 594.),
    ("iso_a1_594x841mm", 594., 841.),
    ("iso_a0_841x1189mm", 841., 1189.),
];

pub fn profile(size: [f64; 2]) -> Result<Value> {
    let [w, h] = size;
    ensure!(
        size.iter().all(|n| n.is_finite() && *n > 0. && *n <= 5080.),
        "Paper must be positive and at most 200 inches per side"
    );
    if (w - 431.8).abs() < 0.01 && (h - 279.4).abs() < 0.01 {
        return Ok(serde_json::from_str(include_str!(
            "../../contracts/v1/examples/hp7585-tabloid-landscape.json"
        ))?);
    }
    let letter = ((w.min(h) - 215.9).abs() < 0.01) && ((w.max(h) - 279.4).abs() < 0.01);
    let tabloid = (w - 279.4).abs() < 0.01 && (h - 431.8).abs() < 0.01;
    let (back, front, left, right) = if letter {
        (39., 15., 15., 15.)
    } else if tabloid {
        (39.425, 15.425, 14.8, 11.)
    } else {
        (39., 15., 14.8, 11.)
    };
    let (l, t, r, b) = if w >= h {
        (back, right, front, left)
    } else {
        (left, back, right, front)
    };
    ensure!(
        w > l + r + 2. && h > t + b + 2.,
        "This sheet is smaller than the HP7585 margins; choose a larger target paper in PDF studio"
    );
    Ok(
        json!({"schema":"plotline.media-profile/1","id":format!("hp7585-{}-{}",(w*100.).round() as u32,(h*100.).round() as u32),"revision":1,
        "label":format!("HP7585 / {:.1} × {:.1} in{}",w/25.4,h/25.4,if letter || tabloid { "" } else { " · nominal margins" }),
        "sheet_size_mm":size,"printable_rect_mm":[l,t,w-l-r,h-t-b],"content_inset_mm":1}),
    )
}

pub fn catalog() -> Result<Value> {
    let mut profiles = vec![profile([431.8, 279.4])?];
    let mut ipp = vec![];
    for &(name, w, h) in NAMED {
        let p = profile([w, h])?;
        ipp.push(json!({"name":name,"profile":p}));
        // Keep IPP's standard page dimensions. Studio's Letter preset starts
        // with its 11-inch edge across the platen, independently of PDF rotation.
        profiles.push(review_profile(if name == "na_letter_8.5x11in" {
            [h, w]
        } else {
            [w, h]
        })?);
    }
    Ok(json!({"media_profiles":profiles,"ipp_media":ipp,"custom_range_mm":[1.,5080.]}))
}

fn fits_platen(width: f64) -> bool {
    (279.4 - 1e-6..=914.4 + 1e-6).contains(&width)
}

/// New defaults select a usable loading without rotating or scaling the source.
/// Legacy saved profiles keep their previous registration until explicitly edited.
pub fn review_profile(size: [f64; 2]) -> Result<Value> {
    let original = profile(size)?;
    let base = if size[0] >= size[1] { 0 } else { 90 };
    let angle = if fits_platen(size[if base == 0 { 1 } else { 0 }]) {
        base
    } else {
        (base + 90) % 180
    };
    orient_profile(&original, angle)
}

fn orient_profile(media: &Value, angle: u16) -> Result<Value> {
    let size: [f64; 2] = serde_json::from_value(media["sheet_size_mm"].clone())?;
    let base = media["orientation_deg"]
        .as_u64()
        .unwrap_or(if size[0] >= size[1] { 0 } else { 90 }) as u16;
    let turns = ((angle + 360 - base) % 360 / 90) as usize;
    let [x, y, w, h]: [f64; 4] = serde_json::from_value(media["printable_rect_mm"].clone())?;
    let mut edges = [x, y, size[0] - x - w, size[1] - y - h];
    edges.rotate_right(turns);
    let [l, t, r, b] = edges;
    ensure!(
        size[0] > l + r + 2. && size[1] > t + b + 2.,
        "This loading is smaller than the HP7585 margins"
    );
    let mut result = media.clone();
    result["orientation_deg"] = json!(angle);
    result["printable_rect_mm"] = json!([l, t, size[0] - l - r, size[1] - t - b]);
    if turns % 2 == 1 {
        result.as_object_mut().unwrap().remove("calibration");
    } else if turns == 2
        && let Some(cal) = result.get_mut("calibration")
    {
        let [a, b, c, d, e, f]: [f64; 6] =
            serde_json::from_value(cal["sheet_to_device_units"].clone())?;
        cal["sheet_to_device_units"] = json!([
            -a,
            -b,
            -c,
            -d,
            a * size[0] + c * size[1] + e,
            b * size[0] + d * size[1] + f
        ]);
    }
    Ok(result)
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Registration {
    pub sheet_size_mm: [f64; 2],
    pub sheet_to_device_units: [f64; 6],
    pub limits: [i32; 4],
    pub rotation: u16,
    pub evidence: String,
}
impl Registration {
    pub fn for_media(media: &Value) -> Result<Self> {
        let requested: Option<u16> = media
            .get("orientation_deg")
            .map(|v| serde_json::from_value(v.clone()))
            .transpose()?;
        ensure!(
            requested.is_none_or(|angle| [0, 90, 180, 270].contains(&angle)),
            "Use a quarter-turn media orientation"
        );
        let registration = Self::base_registration(media)?;
        if let Some(angle) = requested {
            ensure!(
                fits_platen(registration.sheet_size_mm[if angle % 180 == 0 { 1 } else { 0 }]),
                "HP7585 needs 11–36 inches across the platen; choose a different paper loading"
            );
            ensure!(
                angle == registration.rotation,
                "Media orientation disagrees with its calibration transform"
            );
        }
        Ok(registration)
    }

    fn base_registration(media: &Value) -> Result<Self> {
        let size: [f64; 2] = serde_json::from_value(media["sheet_size_mm"].clone())?;
        let nominal = profile(size)?;
        let nominal = if let Some(angle) = media["orientation_deg"].as_u64() {
            orient_profile(&nominal, angle as u16)?
        } else {
            nominal
        };
        let calibrated = media
            .get("calibration")
            .or_else(|| nominal.get("calibration"));
        if let Some(c) = calibrated {
            ensure!(
                c["device"] == "HP7585B" && c["units_per_mm"] == 40.,
                "Use an HP7585 paper calibration at 40 units/mm"
            );
            let matrix: [f64; 6] = serde_json::from_value(c["sheet_to_device_units"].clone())?;
            let limits: [i32; 4] = serde_json::from_value(c["hard_clip_units"].clone())?;
            ensure!(
                matrix.iter().all(|n| n.is_finite()),
                "Invalid paper registration"
            );
            let axes = [
                [40., 0., 0., -40.],
                [0., 40., 40., 0.],
                [-40., 0., 0., 40.],
                [0., -40., -40., 0.],
            ];
            let rotation = axes
                .iter()
                .position(|expected| {
                    matrix[..4]
                        .iter()
                        .zip(expected)
                        .all(|(actual, want)| (actual - want).abs() < 1e-6)
                })
                .ok_or_else(|| {
                    anyhow::anyhow!("HP7585 calibration must use a quarter-turn at 40 units/mm")
                })? as u16
                * 90;
            ensure!(
                limits[0] < limits[2] && limits[1] < limits[3],
                "Invalid calibrated drawing limits"
            );
            return Ok(Self {
                sheet_size_mm: size,
                sheet_to_device_units: matrix,
                limits,
                rotation,
                evidence: c["evidence"]
                    .as_str()
                    .unwrap_or("Recorded calibration")
                    .into(),
            });
        }
        let [w, h] = size;
        let [l, t, rw, rh]: [f64; 4] =
            serde_json::from_value(nominal["printable_rect_mm"].clone())?;
        let cx = l + rw / 2.;
        let cy = t + rh / 2.;
        let rotation = media["orientation_deg"]
            .as_u64()
            .unwrap_or(if w >= h { 0 } else { 90 }) as u16;
        let [a, b, c, d] = match rotation {
            0 => [40., 0., 0., -40.],
            90 => [0., 40., 40., 0.],
            180 => [-40., 0., 0., 40.],
            270 => [0., -40., -40., 0.],
            _ => anyhow::bail!("Use a quarter-turn media orientation"),
        };
        let matrix = [a, b, c, d, -a * cx - c * cy, -b * cx - d * cy];
        let (dx, dy) = if rotation.is_multiple_of(180) {
            (rw, rh)
        } else {
            (rh, rw)
        };
        Ok(Self { sheet_size_mm:size,sheet_to_device_units:matrix,
            limits:[-(dx*20.).floor() as i32,-(dy*20.).floor() as i32,(dx*20.).floor() as i32,(dy*20.).floor() as i32],
            rotation,evidence:"Nominal page registration using HP7585 margins; loaded drawing limits are checked again at Plot".into() })
    }
    pub fn point(&self, sheet_mm: [f64; 2]) -> [f64; 2] {
        let [a, b, c, d, e, f] = self.sheet_to_device_units;
        let [x, y] = sheet_mm;
        [a * x + c * y + e, b * x + d * y + f]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn page_origin_and_measured_tabloid_ruler_survive_registration() {
        let r = Registration::for_media(&profile([431.8, 279.4]).unwrap()).unwrap();
        assert_eq!(r.point([60., 35.]), [-6716., 4112.]);
        assert_eq!(r.point([160., 35.]), [-2716., 4112.]);
        let p = Registration::for_media(&profile([215.9, 279.4]).unwrap()).unwrap();
        assert_eq!(p.rotation, 90);
        let a = p.point([25., 65.]);
        let b = p.point([125., 65.]);
        assert!((b[1] - a[1] - 4000.).abs() < 1e-6);
        assert!((a[0] - (-3468.)).abs() < 1e-6);
    }

    #[test]
    fn four_orientations_preserve_the_physical_sheet_and_scale() {
        for size in [
            [431.8, 279.4],
            [279.4, 431.8],
            [558.8, 431.8],
            [431.8, 558.8],
        ] {
            let normal = profile(size).unwrap();
            let before = Registration::for_media(&normal).unwrap();
            let mut reversed = normal.clone();
            reversed["orientation_deg"] = json!((before.rotation + 180) % 360);
            let [x, y, w, h]: [f64; 4] =
                serde_json::from_value(normal["printable_rect_mm"].clone()).unwrap();
            reversed["printable_rect_mm"] = json!([size[0] - x - w, size[1] - y - h, w, h]);
            if normal.get("calibration").is_some() {
                let [a, b, c, d, e, f] = before.sheet_to_device_units;
                reversed["calibration"]["sheet_to_device_units"] = json!([
                    -a,
                    -b,
                    -c,
                    -d,
                    a * size[0] + c * size[1] + e,
                    b * size[0] + d * size[1] + f
                ]);
            }
            let after = Registration::for_media(&reversed).unwrap();
            assert_eq!(after.rotation, (before.rotation + 180) % 360);
            assert_eq!(after.limits, before.limits);
            for p in [[0., 0.], size, [60., 35.], [160., 35.]] {
                let a = before.point(p);
                let b = after.point([size[0] - p[0], size[1] - p[1]]);
                for i in 0..2 {
                    assert!((a[i] - b[i]).abs() < 1e-8);
                }
            }
            let a = after.point([60., 35.]);
            let b = after.point([160., 35.]);
            assert!(((b[0] - a[0]).hypot(b[1] - a[1]) - 4000.).abs() < 1e-8);
        }
    }

    #[test]
    fn orientation_is_validated_instead_of_silently_ignored() {
        let base = profile([431.8, 279.4]).unwrap();
        for angle in [
            json!(45),
            json!(90),
            json!(180),
            json!(-90),
            json!("180"),
            json!(true),
        ] {
            let mut media = base.clone();
            media["orientation_deg"] = angle;
            assert!(
                Registration::for_media(&media).is_err(),
                "Mismatched or invalid orientation accepted: {media}"
            );
        }
        let mut media = base.clone();
        media.as_object_mut().unwrap().remove("calibration");
        media["orientation_deg"] = json!(180);
        assert_eq!(
            Registration::for_media(&media).unwrap().rotation,
            180,
            "Nominal fallback must also turn its calibrated default"
        );
        let media = orient_profile(&profile([279.4, 431.8]).unwrap(), 180).unwrap();
        assert_eq!(Registration::for_media(&media).unwrap().rotation, 180);
        let mut media = base;
        media["calibration"]["sheet_to_device_units"] = json!([40., 1., 0., -40., 0., 0.]);
        assert!(Registration::for_media(&media).is_err());
    }

    #[test]
    fn explicit_loading_changes_device_axes_without_changing_sheet_size() {
        let base = profile([431.8, 279.4]).unwrap();
        for angle in [0, 90, 180, 270] {
            let media = orient_profile(&base, angle).unwrap();
            let registration = Registration::for_media(&media).unwrap();
            assert_eq!(registration.rotation, angle);
            assert_eq!(registration.sheet_size_mm, [431.8, 279.4]);
            let a = registration.point([60., 60.]);
            let b = registration.point([160., 60.]);
            let delta = [b[0] - a[0], b[1] - a[1]];
            assert_eq!(
                delta,
                match angle {
                    0 => [4000., 0.],
                    90 => [0., 4000.],
                    180 => [-4000., 0.],
                    _ => [0., -4000.],
                }
            );
            let [x, y, w, h]: [f64; 4] =
                serde_json::from_value(media["printable_rect_mm"].clone()).unwrap();
            for point in [[x + 1., y + 1.], [x + w - 1., y + h - 1.]] {
                let [px, py] = registration.point(point);
                let [l, b, r, t] = registration.limits.map(f64::from);
                assert!(
                    px >= l && px <= r && py >= b && py <= t,
                    "{angle}: {point:?} outside {registration:?}"
                );
            }
            assert_eq!(media.get("calibration").is_some(), angle % 180 == 0);
        }
    }

    #[test]
    fn letter_and_large_paper_choose_the_loadable_edge() {
        for size in [
            [279.4, 215.9],
            [215.9, 279.4],
            [863.6, 1117.6],
            [1117.6, 863.6],
            [914.4, 1219.2],
        ] {
            let media = review_profile(size).unwrap();
            let reg = Registration::for_media(&media).unwrap();
            assert!(fits_platen(
                size[if reg.rotation % 180 == 0 { 1 } else { 0 }]
            ));
            let impossible = orient_profile(&media, (reg.rotation + 90) % 360).unwrap();
            assert!(Registration::for_media(&impossible).is_err());
        }
        let catalog = catalog().unwrap();
        assert_eq!(
            catalog["media_profiles"][1]["sheet_size_mm"],
            json!([279.4, 215.9])
        );
        assert_eq!(catalog["media_profiles"][1]["orientation_deg"], 90);
        assert_eq!(
            catalog["ipp_media"][0]["profile"]["sheet_size_mm"],
            json!([215.9, 279.4])
        );
        for size in [[200., 200.], [1000., 1100.]] {
            assert!(Registration::for_media(&review_profile(size).unwrap()).is_err());
        }
    }
}
