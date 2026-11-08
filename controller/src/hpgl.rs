//! A deliberately bounded centerline interpreter, independent of the converter.
//! Never executes HPGL, serial escapes, shell commands or archived Python.
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt::Write,
};

type Point = [f64; 2];
const MAX_POINTS: usize = 8_000_000;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Pen {
    pub id: u8,
    pub color: String,
    #[serde(default)]
    pub width_mm: Option<f64>,
    #[serde(default)]
    pub speed_cm_s: Option<u8>,
    #[serde(default)]
    pub force: Option<u8>,
    pub paths: usize,
    pub distance_mm: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Summary {
    pub commands: usize,
    pub points: usize,
    pub paths: usize,
    pub bounds: [f64; 4],
    pub width_mm: f64,
    pub height_mm: f64,
    pub pen_down_mm: f64,
    pub pen_up_mm: f64,
    pub y_travel_mm: f64,
    pub pens: Vec<Pen>,
    pub notes: Vec<String>,
}

#[derive(Debug)]
pub(crate) struct Stroke {
    pub(crate) pen: u8,
    pub(crate) points: Vec<Point>,
}

pub struct Drawing {
    pub summary: Summary,
    pub(crate) strokes: Vec<Stroke>,
    travel: Vec<[Point; 2]>,
}

fn numbers(args: &str) -> Result<Vec<i32>> {
    if args.trim().is_empty() {
        return Ok(vec![]);
    }
    args.split(',')
        .map(|n| {
            n.trim()
                .parse()
                .map_err(|_| anyhow::anyhow!("Expected integer parameters, got {args:?}"))
        })
        .collect()
}

/// CO strings may contain semicolons and newlines. Ordinary commands may be
/// separated by semicolons, newlines or the next two-letter mnemonic.
fn commands(text: &str) -> Result<Vec<(&str, &str, usize)>> {
    ensure!(text.is_ascii(), "HPGL input must be ASCII");
    let bytes = text.as_bytes();
    let mut i = 0;
    let mut result = vec![];
    while i < bytes.len() {
        if bytes[i].is_ascii_whitespace() || bytes[i] == b';' {
            i += 1;
            continue;
        }
        let start = i;
        ensure!(
            i + 2 <= bytes.len() && bytes[i..i + 2].iter().all(u8::is_ascii_alphabetic),
            "Expected HPGL command at byte {i}; device-control escapes are unsupported"
        );
        let op = &text[i..i + 2];
        i += 2;
        let args_start = i;
        if op.eq_ignore_ascii_case("CO") {
            while i < bytes.len() && bytes[i].is_ascii_whitespace() {
                i += 1;
            }
            ensure!(
                bytes.get(i) == Some(&b'"'),
                "CO requires a quoted string at byte {start}"
            );
            i += 1;
            while i < bytes.len() && bytes[i] != b'"' {
                i += 1;
            }
            ensure!(i < bytes.len(), "Unterminated CO string at byte {start}");
            i += 1;
        } else {
            while i < bytes.len()
                && bytes[i] != b';'
                && bytes[i] != b'\n'
                && !bytes[i].is_ascii_alphabetic()
            {
                i += 1;
            }
        }
        result.push((op, text[args_start..i].trim(), start));
        ensure!(
            result.len() <= MAX_POINTS,
            "Too many commands (limit {MAX_POINTS})"
        );
    }
    Ok(result)
}

pub fn parse(text: &str, units_per_mm: f64) -> Result<Drawing> {
    ensure!(
        units_per_mm.is_finite() && (1.0..=1000.0).contains(&units_per_mm),
        "Units/mm must be between 1 and 1000"
    );
    let mut pens: BTreeMap<u8, Pen> = [
        "#171d21", "#d13e36", "#25864b", "#255dc2", "#00a0aa", "#d4b000", "#b431a7", "#e57924",
    ]
    .iter()
    .enumerate()
    .map(|(i, color)| {
        (
            (i + 1) as u8,
            Pen {
                id: (i + 1) as u8,
                color: color.to_string(),
                width_mm: None,
                speed_cm_s: None,
                force: None,
                paths: 0,
                distance_mm: 0.0,
            },
        )
    })
    .collect();
    let mut initialized = false;
    let mut absolute = true;
    let mut down = false;
    let mut pen = 0;
    let mut position: Option<Point> = None;
    let mut strokes: Vec<Stroke> = vec![];
    let mut current: Vec<Point> = vec![];
    let mut travel = vec![];
    let mut pen_up_mm = 0.0;
    let mut y_travel_mm = 0.0;
    let mut point_count = 0;
    let mut settings_seen = false;
    let mut palette_seen = false;
    let mut speed = None;
    let mut force = None;
    let mut speeds: BTreeMap<u8, BTreeSet<Option<u8>>> = BTreeMap::new();
    let mut forces: BTreeMap<u8, BTreeSet<Option<u8>>> = BTreeMap::new();
    let cmds = commands(text)?;
    let finish = |strokes: &mut Vec<Stroke>, points: &mut Vec<Point>, pen| {
        if points.len() > 1 {
            strokes.push(Stroke {
                pen,
                points: std::mem::take(points),
            });
        }
        points.clear();
    };
    for &(op, args, offset) in &cmds {
        let op = op.to_ascii_uppercase();
        if op == "CO" {
            let comment = args.trim_matches('"');
            if comment.split_whitespace().next() == Some("pen") {
                let fields: BTreeMap<_, _> = comment
                    .split_whitespace()
                    .skip(1)
                    .filter_map(|v| v.split_once('='))
                    .collect();
                let field = |key| -> Result<u8> {
                    fields
                        .get(key)
                        .ok_or_else(|| anyhow::anyhow!("Palette missing {key}"))?
                        .parse()
                        .map_err(Into::into)
                };
                let id = field("id")?;
                ensure!((1..=8).contains(&id), "Palette pen must be 1..8");
                let color = format!("#{:02x}{:02x}{:02x}", field("r")?, field("g")?, field("b")?);
                let previous = pens.get_mut(&id).unwrap();
                ensure!(
                    previous.distance_mm == 0.0
                        && !strokes.iter().any(|s| s.pen == id)
                        && !(pen == id && current.len() > 1),
                    "Palette changes after drawing are unsupported"
                );
                previous.color = color;
                if let Some(width) = fields.get("width_mm") {
                    let width: f64 = width.parse()?;
                    ensure!(
                        width.is_finite() && (0.01..=5.).contains(&width),
                        "Pen width_mm must be 0.01–5 mm"
                    );
                    previous.width_mm = Some(width);
                }
                palette_seen = true;
            }
            continue;
        }
        ensure!(
            matches!(
                op.as_str(),
                "IN" | "PA" | "PR" | "PU" | "PD" | "SP" | "VS" | "FS"
            ),
            "Unsupported HPGL command {op} at byte {offset}; no partial preview was saved"
        );
        let n = numbers(args)?;
        if op == "IN" {
            ensure!(n.is_empty(), "IN takes no parameters");
            finish(&mut strokes, &mut current, pen);
            initialized = true;
            absolute = true;
            down = false;
            pen = 0;
            speed = None;
            force = None;
            // IN resets settings; it does not move the carriage.
            continue;
        }
        ensure!(
            initialized,
            "An IN command is required before {op} at byte {offset}"
        );
        if op == "SP" {
            ensure!(
                n.len() == 1 && (0..=8).contains(&n[0]),
                "SP requires one pen number in 0..8; got SP{args} at byte {offset}"
            );
            finish(&mut strokes, &mut current, pen);
            pen = n[0] as u8;
            continue;
        }
        if op == "VS" || op == "FS" {
            ensure!(
                n.len() == 1 && (1..=if op == "VS" { 60 } else { 8 }).contains(&n[0]),
                "{op} requires one integer in {}",
                if op == "VS" { "1..60 cm/s" } else { "1..8" }
            );
            if op == "VS" {
                speed = Some(n[0] as u8);
            } else {
                force = Some(n[0] as u8);
            }
            settings_seen = true;
            continue;
        }
        match op.as_str() {
            "PA" => absolute = true,
            "PR" => absolute = false,
            "PU" => {
                finish(&mut strokes, &mut current, pen);
                down = false;
            }
            "PD" => down = true,
            _ => unreachable!(),
        }
        ensure!(
            n.len() % 2 == 0,
            "Odd coordinate count for {op} at byte {offset}"
        );
        if down && pen != 0 && position.is_some() && !n.is_empty() {
            speeds.entry(pen).or_default().insert(speed);
            forces.entry(pen).or_default().insert(force);
        }
        for xy in n.chunks_exact(2) {
            point_count += 1;
            ensure!(
                point_count <= MAX_POINTS,
                "Too many coordinate pairs (limit {MAX_POINTS})"
            );
            let mut next = [xy[0] as f64, xy[1] as f64];
            if !absolute {
                let p = position.ok_or_else(|| {
                    anyhow::anyhow!("Relative move before known absolute position at byte {offset}")
                })?;
                next = [p[0] + next[0], p[1] + next[1]];
            }
            ensure!(
                next.iter().all(|v| v.abs() <= 10_000_000.0),
                "Coordinate exceeds preview range at byte {offset}"
            );
            if let Some(previous) = position {
                let distance = (next[0] - previous[0]).hypot(next[1] - previous[1]) / units_per_mm;
                y_travel_mm += (next[1] - previous[1]).abs() / units_per_mm;
                if down && pen != 0 {
                    if current.is_empty() {
                        current.push(previous);
                    }
                    current.push(next);
                    pens.get_mut(&pen).unwrap().distance_mm += distance;
                } else {
                    travel.push([previous, next]);
                    pen_up_mm += distance;
                }
            } else {
                ensure!(
                    !down,
                    "Drawing starts at an unknown position; establish position with PU first"
                );
            }
            position = Some(next);
        }
    }
    finish(&mut strokes, &mut current, pen);
    ensure!(!strokes.is_empty(), "No drawable pen paths found");
    let mut bounds = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for stroke in &strokes {
        pens.get_mut(&stroke.pen).unwrap().paths += 1;
        for p in &stroke.points {
            bounds[0] = bounds[0].min(p[0]);
            bounds[1] = bounds[1].min(p[1]);
            bounds[2] = bounds[2].max(p[0]);
            bounds[3] = bounds[3].max(p[1]);
        }
    }
    for (id, p) in &mut pens {
        if let Some(values) = speeds.get(id).filter(|s| s.len() == 1) {
            p.speed_cm_s = *values.first().unwrap();
        }
        if let Some(values) = forces.get(id).filter(|s| s.len() == 1) {
            p.force = *values.first().unwrap();
        }
    }
    let pens: Vec<_> = pens.into_values().filter(|p| p.paths > 0).collect();
    let mut notes = vec!["Dimensions are drawn bounds, not paper size. Units/mm are an import assumption, not device calibration.".into(),
        "Travel excludes the unknown initial carriage position and mechanical pen changes.".into()];
    if !palette_seen {
        notes.push("Preview uses the default slot colors (1 black, 2 red, 3 green); installed pens may differ.".into());
    }
    if settings_seen {
        notes.push("VS/FS settings initialize the ink controls when constant for that ink. Missing or varying settings use explicit setup defaults; the original file remains unchanged.".into());
    }
    if down {
        notes.push("Source ends with pen down. Hardware submission will require a separately validated device plan.".into());
    }
    let summary = Summary {
        commands: cmds.len(),
        points: point_count,
        paths: strokes.len(),
        bounds,
        width_mm: (bounds[2] - bounds[0]) / units_per_mm,
        height_mm: (bounds[3] - bounds[1]) / units_per_mm,
        pen_down_mm: pens.iter().map(|p| p.distance_mm).sum(),
        pen_up_mm,
        y_travel_mm,
        pens,
        notes,
    };
    Ok(Drawing {
        summary,
        strokes,
        travel,
    })
}

impl Drawing {
    pub fn svg(&self) -> String {
        let [x0, y0, x1, y1] = self.summary.bounds;
        let pad = ((x1 - x0).max(y1 - y0) * 0.025).max(40.0);
        let mut svg = format!(
            r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="{} {} {} {}" role="img" aria-label="HPGL centerline preview"><g id="drawing"><g transform="scale(1,-1)" fill="none" stroke-linecap="round" stroke-linejoin="round">"#,
            x0 - pad,
            -y1 - pad,
            x1 - x0 + 2.0 * pad,
            y1 - y0 + 2.0 * pad
        );
        for pen in &self.summary.pens {
            write!(svg, r#"<g id="pen-{}" stroke="{}"><path vector-effect="non-scaling-stroke" stroke-width="0.8" d=""#, pen.id, pen.color).unwrap();
            for stroke in self.strokes.iter().filter(|s| s.pen == pen.id) {
                for (i, p) in stroke.points.iter().enumerate() {
                    write!(svg, "{}{},{} ", if i == 0 { 'M' } else { 'L' }, p[0], p[1]).unwrap();
                }
            }
            svg.push_str("\"/></g>");
        }
        svg.push_str(r##"<g id="travel" display="none" stroke="#6e97ae" opacity="0.55"><path vector-effect="non-scaling-stroke" stroke-width="0.6" stroke-dasharray="3 4" d=""##);
        for [a, b] in &self.travel {
            write!(svg, "M{},{}L{},{} ", a[0], a[1], b[0], b[1]).unwrap();
        }
        svg.push_str("\"/></g></g></g></svg>");
        svg
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn widths_and_motion_settings_describe_each_ink_without_changing_paths() {
        let source = "IN;CO\"pen id=1 r=255 g=0 b=0 width_mm=0.7\";SP1;VS12;FS6;PU0,0;PD40,0;PU;SP2;VS8;FS2;PU0,40;PD40,40;PU;SP1;VS12;FS6;PU0,80;PD40,80;PU;SP0;";
        let d = parse(source, 40.).unwrap();
        let p = &d.summary.pens[0];
        assert_eq!(p.width_mm, Some(0.7));
        assert_eq!(p.speed_cm_s, Some(12));
        assert_eq!(p.force, Some(6));
        assert_eq!(d.summary.pens[1].width_mm, None);
        assert_eq!(d.summary.pens[1].speed_cm_s, Some(8));
        assert_eq!(d.summary.pen_down_mm, 3.);
        let varying = source.replacen("VS12;FS6;PU0,80", "VS10;FS4;PU0,80", 1);
        let p = &parse(&varying, 40.).unwrap().summary.pens[0];
        assert_eq!(p.speed_cm_s, None);
        assert_eq!(p.force, None);
        for width in ["NaN", "inf", "0", "-1", "6"] {
            assert!(
                parse(
                    &source.replace("width_mm=0.7", &format!("width_mm={width}")),
                    40.
                )
                .is_err()
            );
        }
    }
    #[test]
    fn plotline_multi_point_and_pen_up_state() {
        let d = parse(
            "IN;PA;SP1;PU100,100;PD500,100,500,500;PU;PA100,500;PD100,100;PU;SP0;",
            40.0,
        )
        .unwrap();
        assert_eq!(d.summary.paths, 2);
        assert_eq!(d.summary.pen_down_mm, 30.0);
        assert_eq!(d.summary.pen_up_mm, 10.0);
        assert_eq!(d.summary.bounds, [100.0, 100.0, 500.0, 500.0]);
    }
    #[test]
    fn relative_moves_and_reset_preserve_position() {
        let d = parse(
            "IN;SP1;PU100,200;PR;PD40,0,0,40;PU;IN;SP2;PD180,240;PU;",
            40.0,
        )
        .unwrap();
        assert_eq!(d.summary.pen_down_mm, 3.0);
        assert_eq!(d.summary.pens.len(), 2);
    }
    #[test]
    fn legacy_newlines_palette_and_settings() {
        let d=parse("IN;\nCO\"a comment; with separators\"\nCO\"pen id=2 r=17 g=34 b=51 desc=ink\"\nSP2;VS5;FS4;\nPU-400,-200\nPD400,200\nPU;",40.1968503937).unwrap();
        assert_eq!(d.summary.pens[0].color, "#112233");
        assert!(d.summary.notes.iter().any(|s| s.contains("VS/FS")));
        assert!(d.svg().contains("M-400,-200 L400,200"));
    }
    #[test]
    fn unsupported_or_ambiguous_input_fails_whole_job() {
        for source in [
            "IN;SP1;PD10,10;",
            "IN;PU0,0;SP1;PD10,10;SC0,1,0,1;",
            "IN;PU0,0;SP1;PD1,2,3;",
            "IN;PR;PU1,2;",
            "IN;SP9;",
            "IN;CO\"unterminated",
            "\x1b.I81;;17:",
            "IN;PU0,0;SP1;PD1,2;CO\"pen id=1 r=2 g=3 b=4\";",
        ] {
            assert!(parse(source, 40.0).is_err(), "accepted {source}");
        }
    }
    #[test]
    fn no_pen_motion_does_not_draw_and_final_stroke_is_kept() {
        let d = parse("in;pu0,0;sp0;pd100,100;sp1;pd200,100", 40.0).unwrap();
        assert_eq!(d.summary.bounds, [100.0, 100.0, 200.0, 100.0]);
        assert_eq!(d.summary.pen_down_mm, 2.5);
        assert!(
            d.summary
                .notes
                .iter()
                .any(|n| n.contains("ends with pen down"))
        );
    }
    #[test]
    fn scale_is_explicit_and_finite() {
        for scale in [0.0, f64::NAN, f64::INFINITY, -40.0] {
            assert!(parse("IN;", scale).is_err());
        }
        let source = "IN;PU0,0;SP1;PD1016,0;PU;";
        assert_eq!(parse(source, 40.0).unwrap().summary.width_mm, 25.4);
    }
}
