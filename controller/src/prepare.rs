//! Device planning stays on dev. The Pi receives only bounded absolute moves.
use crate::hpgl;
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt::Write,
};

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Profile {
    pub units_per_mm: f64,
    pub limits: [i32; 4],
    pub margin_mm: f64,
    pub speed_cm_s: u8,
    pub force: u8,
}

pub struct Prepared {
    pub commands: String,
    /// Interchange HPGL with color/width comments. Never send these comments to
    /// the Pi: the serial protocol accepts only the normalized `commands`.
    pub document: String,
    pub svg: String,
    pub report: serde_json::Value,
}

pub fn check_loaded_window(commands: &str, [x0, y0, x1, y1]: [i32; 4]) -> Result<()> {
    for line in commands.lines() {
        if line.len() > 3 && (line.starts_with("PU") || line.starts_with("PD")) {
            let (x, y) = line[2..line.len() - 1]
                .split_once(',')
                .context("Invalid normalized move")?;
            let (x, y): (i32, i32) = (x.parse()?, y.parse()?);
            ensure!(
                x >= x0 && x <= x1 && y >= y0 && y <= y1,
                "Drawing does not fit the paper currently loaded in the plotter"
            );
        }
    }
    Ok(())
}

/// Converter HPGL is measured from the sheet's bottom-left, with Y upward.
/// Recover top-left PDF millimetres before registering the page exactly once.
pub fn register_sheet(source: &str, registration: &crate::media::Registration) -> Result<String> {
    let drawing = hpgl::parse(source, 40.)?;
    let mut result = String::from("IN;\nPA;\n");
    for pen in &drawing.summary.pens {
        let rgb = u32::from_str_radix(&pen.color[1..], 16)?;
        writeln!(
            result,
            "CO\"pen id={} r={} g={} b={} width_mm={}\";",
            pen.id,
            (rgb >> 16) & 255,
            (rgb >> 8) & 255,
            rgb & 255,
            pen.width_mm
                .context("Converted PDF ink must have a diameter")?
        )?;
    }
    for stroke in drawing.strokes {
        writeln!(result, "SP{};", stroke.pen)?;
        for (i, p) in stroke.points.iter().enumerate() {
            let [x, y] =
                registration.point([p[0] / 40., registration.sheet_size_mm[1] - p[1] / 40.]);
            ensure!(
                x.is_finite()
                    && y.is_finite()
                    && x.abs() < i32::MAX as f64
                    && y.abs() < i32::MAX as f64,
                "Invalid registered coordinate"
            );
            writeln!(
                result,
                "{}{},{};",
                if i == 0 { "PU" } else { "PD" },
                x.round() as i32,
                y.round() as i32
            )?;
        }
        result.push_str("PU;\n");
    }
    result.push_str("SP0;\n");
    Ok(result)
}

pub fn prepare(
    source: &str,
    source_units: f64,
    profile: Profile,
    rotation: u16,
    pens: BTreeSet<u8>,
) -> Result<Prepared> {
    let mapping = pens.iter().map(|p| (*p, *p)).collect();
    prepare_mapped(source, source_units, profile, rotation, pens, mapping)
}

pub fn prepare_mapped(
    source: &str,
    source_units: f64,
    profile: Profile,
    rotation: u16,
    pens: BTreeSet<u8>,
    pen_map: BTreeMap<u8, u8>,
) -> Result<Prepared> {
    prepare_with_inks(
        source,
        source_units,
        profile,
        rotation,
        pens,
        pen_map,
        BTreeMap::new(),
    )
}

pub fn prepare_with_inks(
    source: &str,
    source_units: f64,
    profile: Profile,
    rotation: u16,
    pens: BTreeSet<u8>,
    pen_map: BTreeMap<u8, u8>,
    ink_settings: BTreeMap<u8, crate::layout::InkSettings>,
) -> Result<Prepared> {
    prepare_with_colors(
        source,
        source_units,
        profile,
        rotation,
        pens,
        pen_map,
        ink_settings,
        BTreeMap::new(),
    )
}

#[allow(clippy::too_many_arguments)]
pub fn prepare_with_colors(
    source: &str,
    source_units: f64,
    profile: Profile,
    rotation: u16,
    pens: BTreeSet<u8>,
    pen_map: BTreeMap<u8, u8>,
    ink_settings: BTreeMap<u8, crate::layout::InkSettings>,
    colors: BTreeMap<u8, String>,
) -> Result<Prepared> {
    prepare_registered(
        source,
        source_units,
        profile,
        rotation,
        pens,
        pen_map,
        ink_settings,
        colors,
        false,
        None,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn prepare_registered(
    source: &str,
    source_units: f64,
    profile: Profile,
    rotation: u16,
    pens: BTreeSet<u8>,
    pen_map: BTreeMap<u8, u8>,
    mut ink_settings: BTreeMap<u8, crate::layout::InkSettings>,
    colors: BTreeMap<u8, String>,
    registered: bool,
    unknown_width_clearance_mm: Option<f64>,
) -> Result<Prepared> {
    ensure!(
        pens.iter()
            .all(|p| pen_map.get(p).is_some_and(|slot| (1..=8).contains(slot))),
        "Map every included ink to a carousel slot 1–8"
    );
    ensure!(
        [0, 90, 180, 270].contains(&rotation),
        "Rotation must be 0, 90, 180 or 270 degrees"
    );
    ensure!(
        !pens.is_empty() && pens.iter().all(|p| (1..=8).contains(p)),
        "Choose one or more pen slots 1..8"
    );
    ensure!(
        profile.units_per_mm.is_finite() && (1.0..=1000.0).contains(&profile.units_per_mm),
        "Invalid device units/mm"
    );
    ensure!(
        profile.margin_mm.is_finite() && profile.margin_mm >= 0.0,
        "Invalid margin"
    );
    ensure!(
        (1..=60).contains(&profile.speed_cm_s) && (1..=8).contains(&profile.force),
        "Invalid speed or force"
    );
    let [x0, y0, x1, y1] = profile.limits;
    ensure!(
        x0 < x1 && y0 < y1 && profile.limits.iter().all(|n| (-32767..=32767).contains(n)),
        "Invalid configured device window"
    );
    let drawing = hpgl::parse(source, source_units)?;
    for pen in &drawing.summary.pens {
        ink_settings
            .entry(pen.id)
            .or_insert(crate::layout::InkSettings {
                width_mm: pen.width_mm,
                speed_cm_s: pen.speed_cm_s.unwrap_or(profile.speed_cm_s),
                force: pen.force.unwrap_or(profile.force),
            });
    }
    for ink in ink_settings.values() {
        ink.validate()?;
    }
    ensure!(
        pens.iter()
            .all(|id| drawing.summary.pens.iter().any(|p| p.id == *id)),
        "A requested pen is absent from this source"
    );
    let b = drawing.summary.bounds;
    let source_center = if registered {
        [0., 0.]
    } else {
        [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0]
    };
    let device_center = if registered {
        [0., 0.]
    } else {
        [(x0 as f64 + x1 as f64) / 2.0, (y0 as f64 + y1 as f64) / 2.0]
    };
    let scale = profile.units_per_mm / source_units;
    let margin = profile.margin_mm * profile.units_per_mm;
    let transform = |p: [f64; 2]| -> Result<[i32; 2]> {
        let [x, y] = [p[0] - source_center[0], p[1] - source_center[1]];
        let [x, y] = match if registered { 0 } else { rotation } {
            0 => [x, y],
            90 => [-y, x],
            180 => [-x, -y],
            270 => [y, -x],
            _ => unreachable!(),
        };
        let [x, y] = [
            (x * scale + device_center[0]).round(),
            (y * scale + device_center[1]).round(),
        ];
        ensure!(
            x >= x0 as f64 + margin
                && x <= x1 as f64 - margin
                && y >= y0 as f64 + margin
                && y <= y1 as f64 - margin,
            "Drawing does not fit the configured window and margin at its original physical scale"
        );
        Ok([x as i32, y as i32])
    };
    // Check the complete layout even for a one-pen pass. Filtering must never
    // re-center layers and spoil registration across separately plotted pens.
    for corner in [[b[0], b[1]], [b[0], b[3]], [b[2], b[1]], [b[2], b[3]]] {
        transform(corner)?;
    }
    if registered {
        for stroke in &drawing.strokes {
            let radius = ink_settings[&stroke.pen]
                .width_mm
                // Calibration targets deliberately permit unmeasured pens.
                // Reserve the largest supported footprint without inventing a
                // measured width in the inventory or exported pen metadata.
                .or(unknown_width_clearance_mm)
                .context("PDF pen diameter is unknown")?
                * profile.units_per_mm
                / 2.;
            for &point in &stroke.points {
                let [x, y] = transform(point)?;
                ensure!(
                    x as f64 - radius >= x0 as f64
                        && x as f64 + radius <= x1 as f64
                        && y as f64 - radius >= y0 as f64
                        && y as f64 + radius <= y1 as f64,
                    "PDF pen footprint exceeds the loaded/selected paper. Adjust paper or placement in PDF studio"
                );
            }
        }
    }
    let mut commands = String::from("IN;\nPA;\n");
    let mut selected = 0;
    for stroke in &drawing.strokes {
        if !pens.contains(&stroke.pen) {
            continue;
        }
        if selected != pen_map[&stroke.pen] {
            writeln!(
                commands,
                "SP{};\nVS{};\nFS{};",
                pen_map[&stroke.pen],
                ink_settings[&stroke.pen].speed_cm_s,
                ink_settings[&stroke.pen].force
            )?;
            selected = pen_map[&stroke.pen];
        }
        let mut last = None;
        for (i, p) in stroke.points.iter().enumerate() {
            let xy = transform(*p)?;
            if i != 0 && last == Some(xy) {
                continue;
            }
            writeln!(
                commands,
                "{}{},{};",
                if i == 0 { "PU" } else { "PD" },
                xy[0],
                xy[1]
            )?;
            last = Some(xy);
        }
        commands.push_str("PU;\n");
    }
    commands.push_str("PU;\nSP0;\n");
    ensure!(
        commands.len() <= crate::store::MAX_BYTES,
        "Normalized device job exceeds 64 MiB"
    );
    let mut decoded = hpgl::parse(&commands, profile.units_per_mm)?;
    for ink in &drawing.summary.pens {
        if pens.contains(&ink.id)
            && let Some(pen) = decoded
                .summary
                .pens
                .iter_mut()
                .find(|p| p.id == pen_map[&ink.id])
        {
            pen.color = colors.get(&ink.id).unwrap_or(&ink.color).clone();
            pen.width_mm = ink_settings[&ink.id].width_mm;
        }
    }
    let report = serde_json::json!({"source_sha256":format!("{:x}",Sha256::digest(source.as_bytes())),
        "prepared_sha256":format!("{:x}",Sha256::digest(commands.as_bytes())),"profile":profile,
        "source_units_per_mm":source_units,"rotation":rotation,"pens":pens,"pen_map":pen_map,"ink_settings":ink_settings,
        "placement":if registered {"PDF sheet registration preserved; no ink centering or rescaling"} else {"Full source ink bounds centered in configured device window; no fit-to-page rescaling"},
        "settings":"Explicit VS/FS after every pen selection; per-ink controls override source settings, with profile defaults for missing or varying source settings",
        "decoded":decoded.summary});
    let mut document = String::from("IN;\nPA;\n");
    let mut described_slots = BTreeSet::new();
    for ink in &drawing.summary.pens {
        if !pens.contains(&ink.id) {
            continue;
        }
        if !described_slots.insert(pen_map[&ink.id]) {
            continue;
        }
        let color = colors.get(&ink.id).unwrap_or(&ink.color);
        let rgb = u32::from_str_radix(&color[1..], 16)?;
        write!(
            document,
            "CO\"pen id={} r={} g={} b={}",
            pen_map[&ink.id],
            (rgb >> 16) & 255,
            (rgb >> 8) & 255,
            rgb & 255
        )?;
        if let Some(width) = ink_settings[&ink.id].width_mm {
            write!(document, " width_mm={width}")?;
        }
        document.push_str("\";\n");
    }
    document.push_str(commands.strip_prefix("IN;\nPA;\n").unwrap());
    ensure!(
        document.len() <= crate::store::MAX_BYTES,
        "Interchange HPGL exceeds 64 MiB"
    );
    Ok(Prepared {
        commands,
        document,
        svg: decoded.svg(),
        report,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pdf_registration_is_bottom_left_to_top_left_then_device_without_recentering() {
        let registration =
            crate::media::Registration::for_media(&crate::media::profile([431.8, 279.4]).unwrap())
                .unwrap();
        let source =
            "IN;CO\"pen id=1 r=0 g=0 b=0 width_mm=0.3\";SP1;PU2400,9776;PD6400,9776;PU;SP0;";
        let registered = register_sheet(source, &registration).unwrap();
        assert!(registered.contains("PU-6716,4112;\nPD-2716,4112;"));
        let plan = prepare_registered(
            &registered,
            40.,
            Profile {
                units_per_mm: 40.,
                limits: registration.limits,
                margin_mm: 0.,
                speed_cm_s: 5,
                force: 4,
            },
            0,
            BTreeSet::from([1]),
            BTreeMap::from([(1, 2)]),
            BTreeMap::new(),
            BTreeMap::new(),
            true,
            None,
        )
        .unwrap();
        assert!(plan.commands.contains("PU-6716,4112;\nPD-2716,4112;"));
        let restricted = Profile {
            units_per_mm: 40.,
            limits: [-6716, -5088, 7539, 5088],
            margin_mm: 0.,
            speed_cm_s: 5,
            force: 4,
        };
        assert!(
            prepare_registered(
                &registered,
                40.,
                restricted,
                0,
                BTreeSet::from([1]),
                BTreeMap::from([(1, 2)]),
                BTreeMap::new(),
                BTreeMap::new(),
                true,
                None
            )
            .err()
            .unwrap()
            .to_string()
            .contains("footprint")
        );
    }
    fn profile() -> Profile {
        Profile {
            units_per_mm: 40.0,
            limits: [-4000, -4000, 4000, 4000],
            margin_mm: 2.0,
            speed_cm_s: 5,
            force: 4,
        }
    }
    const SOURCE: &str = "IN;PA;SP1;PU0,0;PD400,0;PU;SP2;PU800,0;PD800,800;PU;SP0;";
    #[test]
    fn each_pen_selection_restores_its_settings_and_metadata_round_trips() {
        use crate::layout::InkSettings;
        let source = format!("{SOURCE}SP1;PU0,0;PD400,0;PU;SP0;");
        let p = prepare_with_inks(
            &source,
            40.,
            profile(),
            90,
            BTreeSet::from([1, 2]),
            BTreeMap::from([(1, 6), (2, 4)]),
            BTreeMap::from([
                (
                    1,
                    InkSettings {
                        width_mm: Some(0.7),
                        speed_cm_s: 12,
                        force: 6,
                    },
                ),
                (
                    2,
                    InkSettings {
                        width_mm: Some(0.3),
                        speed_cm_s: 8,
                        force: 2,
                    },
                ),
            ]),
        )
        .unwrap();
        assert_eq!(p.commands.matches("SP6;\nVS12;\nFS6;\n").count(), 2);
        assert_eq!(p.commands.matches("SP4;\nVS8;\nFS2;\n").count(), 1);
        assert!(!p.commands.contains("CO"));
        let d = hpgl::parse(&p.document, 40.).unwrap();
        let red = d.summary.pens.iter().find(|p| p.id == 4).unwrap();
        assert_eq!(red.width_mm, Some(0.3));
        assert_eq!(red.speed_cm_s, Some(8));
        assert_eq!(red.force, Some(2));
        assert_eq!(d.summary.pen_down_mm, 40.);
        // The serial planner can consume the UI's interchange file. Its profile
        // defaults must not overwrite the explicit per-ink VS/FS in that file.
        let serial = prepare(&p.document, 40., profile(), 0, BTreeSet::from([4, 6])).unwrap();
        assert_eq!(serial.commands, p.commands);
        for command in serial.commands.lines() {
            assert!(command.len() < 80);
        }
    }
    #[test]
    fn separate_pen_passes_keep_registration_and_source_scale() {
        let all = prepare(SOURCE, 40.0, profile(), 0, BTreeSet::from([1, 2])).unwrap();
        let one = prepare(SOURCE, 40.0, profile(), 0, BTreeSet::from([1])).unwrap();
        assert!(all.commands.contains("PU-400,-400;\nPD0,-400;"));
        assert!(one.commands.contains("PU-400,-400;\nPD0,-400;"));
        assert!(!one.commands.contains("SP2;"));
        assert_eq!(one.report["decoded"]["pen_down_mm"], 10.0);
    }
    #[test]
    fn rotation_and_unit_conversion_have_explicit_physical_meaning() {
        let mut p = profile();
        p.units_per_mm = 80.0;
        let prepared = prepare(SOURCE, 40.0, p, 90, BTreeSet::from([1, 2])).unwrap();
        assert!(prepared.commands.contains("PU800,-800;\nPD800,0;"));
        assert_eq!(prepared.report["decoded"]["pen_down_mm"], 30.0);
        for line in prepared.commands.lines() {
            assert!(line.len() <= 79);
            assert!(line.ends_with(';'));
        }
    }
    #[test]
    fn oversized_work_is_rejected_even_when_filter_hides_outlying_pen() {
        let mut p = profile();
        p.limits = [-300, -300, 300, 300];
        p.margin_mm = 0.0;
        assert!(prepare(SOURCE, 40.0, p, 0, BTreeSet::from([1])).is_err());
        assert!(prepare(SOURCE, 40.0, profile(), 45, BTreeSet::from([1])).is_err());
    }
    #[test]
    fn mapped_rotation_preserves_registration_and_color() {
        let mapping = BTreeMap::from([(1, 6), (2, 4)]);
        let all = prepare_mapped(
            SOURCE,
            40.,
            profile(),
            90,
            BTreeSet::from([1, 2]),
            mapping.clone(),
        )
        .unwrap();
        let one = prepare_mapped(SOURCE, 40., profile(), 90, BTreeSet::from([1]), mapping).unwrap();
        for output in [&all, &one] {
            assert!(output.commands.contains("SP6;"));
            assert!(output.commands.contains("PU400,-400;\nPD400,0;"));
            assert!(!output.commands.contains("SP1;"));
            assert!(!output.commands.contains("SP2;"));
        }
        assert!(all.commands.contains("SP4;"));
        assert!(!one.commands.contains("SP4;"));
        let source_color = &hpgl::parse(SOURCE, 40.).unwrap().summary.pens[0].color;
        assert_eq!(one.report["decoded"]["pens"][0]["color"], *source_color);
        let merged = prepare_mapped(
            SOURCE,
            40.,
            profile(),
            0,
            BTreeSet::from([1, 2]),
            BTreeMap::from([(1, 4), (2, 4)]),
        )
        .unwrap();
        let decoded = hpgl::parse(&merged.document, 40.).unwrap();
        assert_eq!(decoded.summary.pens.len(), 1);
        assert_eq!(
            decoded.summary.paths,
            hpgl::parse(SOURCE, 40.).unwrap().summary.paths
        );
        for bad in [BTreeMap::from([(1, 9), (2, 4)]), BTreeMap::from([(1, 4)])] {
            assert!(
                prepare_mapped(SOURCE, 40., profile(), 0, BTreeSet::from([1, 2]), bad).is_err()
            );
        }
    }
}
