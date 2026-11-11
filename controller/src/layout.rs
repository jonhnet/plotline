//! Offline layout preferences; neither a saved observation nor a plan is live
//! plotter state or permission to send a job.
use crate::{prepare::Profile, store::Job};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct InkSettings {
    pub width_mm: Option<f64>,
    pub speed_cm_s: u8,
    pub force: u8,
}
impl InkSettings {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.width_mm
                .is_none_or(|w| w.is_finite() && (0.01..=5.).contains(&w)),
            "Pen width must be 0.01–5 mm, or unknown"
        );
        ensure!(
            (1..=60).contains(&self.speed_cm_s),
            "Speed must be 1–60 cm/s"
        );
        ensure!((1..=8).contains(&self.force), "Force must be 1–8");
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Setup {
    pub paper_x_mm: f64,
    pub paper_y_mm: f64,
    /// Back (-X), front (+X), left (-Y), right (+Y).
    pub margins_mm: [f64; 4],
    pub window: String,
    pub rotation: u16,
    pub facing: String,
    pub pens: BTreeSet<u8>,
    pub pen_map: BTreeMap<u8, u8>,
    #[serde(default)]
    pub ink_settings: BTreeMap<u8, InkSettings>,
    #[serde(default)]
    pub carousel_revision: Option<u64>,
    #[serde(default)]
    pub accepted_substitutions: BTreeMap<u8, PenSubstitution>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub registration: Option<crate::media::Registration>,
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PenSubstitution {
    pub slot: u8,
    pub color: String,
    pub width_mm: Option<f64>,
    pub requested_color: String,
    pub requested_width_mm: Option<f64>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Observation {
    pub identity: String,
    pub observed_at: String,
    pub units_per_mm: f64,
    pub limits: [i32; 4],
}

// Keep the original refusal, with enough physical context to diagnose paper
// size, orientation and fixed-placement mismatches. No fit rule is relaxed.
pub fn explain_fit(error: anyhow::Error, context: &str) -> anyhow::Error {
    let message = error.to_string();
    if message.contains("does not fit")
        || message.contains("footprint exceeds")
        || message.contains("Invalid configured device window")
    {
        anyhow::anyhow!("{message}. {context}")
    } else {
        error
    }
}
impl Setup {
    pub fn fit_context(&self, job: &Job, observation: Option<&Observation>) -> String {
        fn size([w, h]: [f64; 2]) -> String {
            format!("{:.2} × {:.2} in ({w:.1} × {h:.1} mm)", w / 25.4, h / 25.4)
        }
        let mut drawing = [job.summary.width_mm, job.summary.height_mm];
        if self.registration.is_none() && self.rotation % 180 != 0 {
            drawing.swap(0, 1);
        }
        let sheet = [self.paper_x_mm, self.paper_y_mm];
        let mut context = if self.registration.is_some() {
            format!("Document sheet {}", size(sheet))
        } else if self.window == "reported" {
            "Sheet edges unknown".into()
        } else {
            format!("Planned sheet {}", size(sheet))
        };
        write!(
            context,
            "; drawing {}. Dimensions are along feed × across carriage at original scale.",
            size(drawing)
        )
        .unwrap();
        if let Ok(p) = self.profile(if self.window == "reported" {
            observation
        } else {
            None
        }) {
            let area = [
                (p.limits[2] - p.limits[0]) as f64 / p.units_per_mm,
                (p.limits[3] - p.limits[1]) as f64 / p.units_per_mm,
            ];
            write!(
                context,
                " Selected drawing area {} after margins.",
                size(area)
            )
            .unwrap();
        }
        if let Some(o) = observation {
            write!(context, " Measured drawable area {} along feed × across carriage ({}); full sheet edges are unknown.",
                size([(o.limits[2]-o.limits[0]) as f64/o.units_per_mm,(o.limits[3]-o.limits[1]) as f64/o.units_per_mm]),o.observed_at).unwrap();
        }
        if let Some(o) = observation {
            let b = if self.registration.is_some() {
                job.summary.bounds.map(|n| n / job.units_per_mm)
            } else {
                let center = if self.window == "reported" {
                    [
                        (o.limits[0] as f64 + o.limits[2] as f64) / (2. * o.units_per_mm),
                        (o.limits[1] as f64 + o.limits[3] as f64) / (2. * o.units_per_mm),
                    ]
                } else {
                    [0., 0.]
                };
                [
                    center[0] - drawing[0] / 2.,
                    center[1] - drawing[1] / 2.,
                    center[0] + drawing[0] / 2.,
                    center[1] + drawing[1] / 2.,
                ]
            };
            let w = o.limits.map(|n| n as f64 / o.units_per_mm);
            let overflow = [w[0] - b[0], w[1] - b[1], b[2] - w[2], b[3] - w[3]];
            for (edge, mm) in ["back (−X)", "left (−Y)", "front (+X)", "right (+Y)"]
                .into_iter()
                .zip(overflow)
            {
                if mm > 0.001 {
                    write!(
                        context,
                        " Drawing bounds exceed {edge} by {:.3} in ({mm:.2} mm).",
                        mm / 25.4
                    )
                    .unwrap();
                }
            }
        }
        context.push_str(" Load the matching sheet and check plotter & paper again, or adjust paper/placement in the source Studio; scale is preserved.");
        context
    }
    pub fn new(job: &Job) -> Self {
        let mut setup = Self {
            paper_x_mm: 431.8,
            paper_y_mm: 279.4,
            margins_mm: [39., 15., 15., 15.],
            window: "planned".into(),
            rotation: 0,
            facing: "artwork".into(),
            pens: job.summary.pens.iter().map(|p| p.id).collect(),
            pen_map: job.summary.pens.iter().map(|p| (p.id, p.id)).collect(),
            ink_settings: Self::default_inks(job),
            carousel_revision: None,
            accepted_substitutions: BTreeMap::new(),
            registration: None,
        };
        if let Some(r) = job.registration() {
            let [w, h] = r.sheet_size_mm;
            let corners = [[0., 0.], [w, 0.], [0., h], [w, h]].map(|p| r.point(p));
            let lo = [
                corners.iter().map(|p| p[0]).fold(f64::INFINITY, f64::min),
                corners.iter().map(|p| p[1]).fold(f64::INFINITY, f64::min),
            ];
            let hi = [
                corners
                    .iter()
                    .map(|p| p[0])
                    .fold(f64::NEG_INFINITY, f64::max),
                corners
                    .iter()
                    .map(|p| p[1])
                    .fold(f64::NEG_INFINITY, f64::max),
            ];
            setup.paper_x_mm = (hi[0] - lo[0]) / 40.;
            setup.paper_y_mm = (hi[1] - lo[1]) / 40.;
            setup.margins_mm = [
                (r.limits[0] as f64 - lo[0]) / 40.,
                (hi[0] - r.limits[2] as f64) / 40.,
                (r.limits[1] as f64 - lo[1]) / 40.,
                (hi[1] - r.limits[3] as f64) / 40.,
            ];
            setup.rotation = r.rotation;
            setup.registration = Some(r.clone());
        }
        setup
    }
    fn default_inks(job: &Job) -> BTreeMap<u8, InkSettings> {
        job.summary
            .pens
            .iter()
            .map(|p| {
                (
                    p.id,
                    InkSettings {
                        width_mm: p.width_mm,
                        speed_cm_s: p.speed_cm_s.unwrap_or(5),
                        force: p.force.unwrap_or(4),
                    },
                )
            })
            .collect()
    }
    // Add fields absent in layouts saved by older versions, preserving choices.
    pub fn upgrade(&mut self, job: &Job) {
        for (id, ink) in Self::default_inks(job) {
            self.ink_settings.entry(id).or_insert(ink);
        }
    }
    pub fn validate(&self, job: &Job) -> Result<()> {
        let expected = Self::new(job);
        ensure!(
            self.registration == expected.registration,
            "This document's page registration is fixed"
        );
        if job.registration().is_some() {
            ensure!(
                self.rotation == expected.rotation
                    && self.window == "planned"
                    && self.paper_x_mm == expected.paper_x_mm
                    && self.paper_y_mm == expected.paper_y_mm
                    && self.margins_mm == expected.margins_mm,
                "This document has fixed paper, margins and placement"
            );
        }
        if job.calibration.is_some() {
            ensure!(
                self.pens == expected.pens && self.pen_map == expected.pen_map,
                "Calibration sheets must use every selected pen in its original slot; create a new sheet to change pens"
            );
        }
        ensure!(
            [0, 90, 180, 270].contains(&self.rotation),
            "Use a quarter-turn rotation"
        );
        ensure!(
            ["artwork", "plotter"].contains(&self.facing.as_str()),
            "Unknown viewing direction"
        );
        ensure!(
            ["planned", "reported"].contains(&self.window.as_str()),
            "Unknown paper mode"
        );
        ensure!(
            [self.paper_x_mm, self.paper_y_mm]
                .iter()
                .all(|n| n.is_finite() && (1.0..=5080.0).contains(n)),
            "Paper dimensions must be 1–5080 mm"
        );
        ensure!(
            self.margins_mm
                .iter()
                .all(|n| n.is_finite() && (0.0..=200.0).contains(n)),
            "Margins must be 0–200 mm"
        );
        ensure!(
            self.paper_x_mm > self.margins_mm[0] + self.margins_mm[1]
                && self.paper_y_mm > self.margins_mm[2] + self.margins_mm[3],
            "Margins leave no drawing area"
        );
        let present: BTreeSet<_> = job.summary.pens.iter().map(|p| p.id).collect();
        ensure!(
            self.pens.is_subset(&present),
            "An included ink is absent from this job"
        );
        ensure!(
            self.pen_map.keys().copied().collect::<BTreeSet<_>>() == present
                && self.pen_map.values().all(|p| (0..=8).contains(p)),
            "Map every source ink to slot 1–8, or 0 for unassigned"
        );
        ensure!(
            self.ink_settings.keys().copied().collect::<BTreeSet<_>>() == present,
            "Configure every source ink"
        );
        for ink in self.ink_settings.values() {
            ink.validate()?;
        }
        Ok(())
    }
    pub fn profile(&self, observation: Option<&Observation>) -> Result<Profile> {
        if let Some(r) = &self.registration {
            let mut limits = r.limits;
            if let Some(o) = observation {
                ensure!(
                    (o.units_per_mm - 40.).abs() < 1e-6,
                    "Unexpected plotter units/mm"
                );
                limits = [
                    limits[0].max(o.limits[0]),
                    limits[1].max(o.limits[1]),
                    limits[2].min(o.limits[2]),
                    limits[3].min(o.limits[3]),
                ];
            }
            return Ok(Profile {
                units_per_mm: 40.,
                limits,
                margin_mm: 0.,
                speed_cm_s: 5,
                force: 4,
            });
        }
        let (units, limits) = if self.window == "reported" {
            let o = observation
                .ok_or_else(|| anyhow::anyhow!("No saved device limits are available"))?;
            (o.units_per_mm, o.limits)
        } else {
            let x = (self.paper_x_mm - self.margins_mm[0] - self.margins_mm[1]) * 20.;
            let y = (self.paper_y_mm - self.margins_mm[2] - self.margins_mm[3]) * 20.;
            (
                40.,
                [
                    -x.round() as i32,
                    -y.round() as i32,
                    x.round() as i32,
                    y.round() as i32,
                ],
            )
        };
        Ok(Profile {
            units_per_mm: units,
            limits,
            margin_mm: 0.,
            speed_cm_s: 5,
            force: 4,
        })
    }
}
