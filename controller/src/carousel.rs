//! The operator's physical inventory, shared by all documents and browsers.
use crate::{
    layout::{InkSettings, Setup},
    store::Job,
};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::Write,
    path::PathBuf,
    sync::Mutex,
};

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Slot {
    pub label: String,
    pub state: String,
    pub color: Option<String>,
    /// Scan-derived appearance; color remains the stable authoring/matching swatch.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub measured_color: Option<String>,
    pub width_mm: Option<f64>,
    pub speed_cm_s: u8,
    pub force: u8,
}
impl Default for Slot {
    fn default() -> Self {
        Self {
            label: String::new(),
            state: "empty".into(),
            color: None,
            measured_color: None,
            width_mm: None,
            speed_cm_s: 5,
            force: 4,
        }
    }
}
impl Slot {
    pub fn matches_color(&self, color: &str) -> bool {
        [&self.color, &self.measured_color].iter().any(|value| {
            value
                .as_ref()
                .is_some_and(|c| c.eq_ignore_ascii_case(color))
        })
    }
    pub fn settings(&self) -> InkSettings {
        InkSettings {
            width_mm: self.width_mm,
            speed_cm_s: self.speed_cm_s,
            force: self.force,
        }
    }
    pub fn usable(&self) -> bool {
        self.state == "loaded" && self.color.is_some()
    }
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Carousel {
    pub revision: u64,
    pub slots: BTreeMap<u8, Slot>,
}
impl Default for Carousel {
    fn default() -> Self {
        Self {
            revision: 0,
            slots: (1..=8).map(|n| (n, Slot::default())).collect(),
        }
    }
}
impl Carousel {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.slots.keys().copied().eq(1..=8),
            "Configure all eight carousel slots"
        );
        for slot in self.slots.values() {
            ensure!(
                slot.label.len() <= 80 && !slot.label.chars().any(char::is_control),
                "Pen name must be at most 80 bytes without control characters"
            );
            ensure!(
                ["empty", "loaded", "unavailable"].contains(&slot.state.as_str()),
                "Unknown slot state"
            );
            ensure!(
                [&slot.color, &slot.measured_color]
                    .iter()
                    .all(|value| value.as_ref().is_none_or(|c| c.len() == 7
                        && c.starts_with('#')
                        && c[1..].bytes().all(|b| b.is_ascii_hexdigit()))),
                "Use a six-digit hex pen color"
            );
            ensure!(
                slot.state != "loaded" || slot.color.is_some(),
                "Choose a color for each loaded pen"
            );
            slot.settings().validate()?;
        }
        Ok(())
    }
    /// Resolve actual settings on the service, never trusting a browser's cached widths.
    pub fn resolve(&self, setup: &mut Setup, job: &Job) -> Result<BTreeMap<u8, String>> {
        if let Some(calibration) = &job.calibration {
            for pen in &job.summary.pens {
                ensure!(
                    self.slots.get(&pen.id) == calibration.carousel.slots.get(&pen.id),
                    "Slot {} changed since this calibration sheet was created. Create a new calibration sheet for the current pens",
                    pen.id
                );
            }
        }
        ensure!(
            setup.carousel_revision.is_none_or(|r| r == self.revision),
            "Carousel changed in another window. Refresh the carousel and review the preview again"
        );
        let mut colors = BTreeMap::new();
        for pen in &job.summary.pens {
            let n = setup.pen_map[&pen.id];
            let Some(slot) = self.slots.get(&n) else {
                ensure!(
                    !setup.pens.contains(&pen.id),
                    "Ink {} is unassigned. Select a loaded pen in the paired pen display",
                    pen.id
                );
                continue;
            };
            if setup.pens.contains(&pen.id) {
                ensure!(
                    slot.usable(),
                    "Slot {n}: {} · {}. Choose a loaded pen in Ink & carousel",
                    slot.label,
                    slot.state
                );
                if job.pdf.is_some() {
                    let matches = slot
                        .width_mm
                        .zip(pen.width_mm)
                        .is_some_and(|(a, b)| (a - b).abs() < 1e-6)
                        && slot.matches_color(&pen.color);
                    let accepted = setup.accepted_substitutions.get(&pen.id).is_some_and(|a| {
                        a.slot == n
                            && a.color
                                .eq_ignore_ascii_case(slot.color.as_deref().unwrap_or(""))
                            && a.width_mm == slot.width_mm
                            && a.requested_color.eq_ignore_ascii_case(&pen.color)
                            && a.requested_width_mm == pen.width_mm
                    });
                    ensure!(
                        matches || accepted,
                        "Ink {} → slot {n} differs from the prepared pen. Review this ink and choose Use existing paths or Rebuild upstream",
                        pen.id
                    );
                }
            }
            setup.ink_settings.insert(pen.id, slot.settings());
            if let Some(color) = &slot.color {
                colors.insert(pen.id, color.clone());
            }
        }
        setup.carousel_revision = Some(self.revision);
        Ok(colors)
    }
}

pub struct Inventory {
    path: PathBuf,
    gate: Mutex<()>,
}
impl Inventory {
    pub fn new(path: PathBuf) -> Self {
        Self {
            path,
            gate: Mutex::new(()),
        }
    }
    fn read(&self) -> Result<Carousel> {
        let value = if self.path.exists() {
            serde_json::from_slice(&fs::read(&self.path)?)?
        } else {
            Carousel::default()
        };
        value.validate()?;
        Ok(value)
    }
    pub fn get(&self) -> Result<Carousel> {
        let _guard = self.gate.lock().unwrap();
        self.read()
    }
    fn write(&self, value: &Carousel) -> Result<()> {
        let temp = self
            .path
            .with_extension(format!("{}.tmp", uuid::Uuid::new_v4()));
        let result = (|| {
            let mut f = File::create(&temp)?;
            f.write_all(&serde_json::to_vec_pretty(value)?)?;
            f.sync_all()?;
            fs::rename(&temp, &self.path)?;
            File::open(self.path.parent().unwrap())?.sync_all()?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(temp);
        }
        result
    }
    pub fn save(&self, mut value: Carousel) -> Result<Carousel> {
        value.validate()?;
        let _guard = self.gate.lock().unwrap();
        ensure!(
            value.revision == self.read()?.revision,
            "Carousel changed in another window. Refresh it before saving"
        );
        value.revision += 1;
        self.write(&value)?;
        Ok(value)
    }
    /// One-time import of the old connection-file labels. Unknown widths stay unknown.
    pub fn migrate(&self, legacy: &serde_json::Value) -> Result<()> {
        let _guard = self.gate.lock().unwrap();
        if self.path.exists() {
            return Ok(());
        }
        let mut value = Carousel::default();
        for (n, slot) in &mut value.slots {
            if let Some(old) = legacy.get(n.to_string()) {
                slot.label = old["label"].as_str().unwrap_or("").to_owned();
                let name = slot.label.to_lowercase();
                slot.color = [
                    ("black", "#000000"),
                    ("hot pink", "#ff1493"),
                    ("red", "#ff0000"),
                    ("green", "#008000"),
                    ("purple", "#800080"),
                ]
                .iter()
                .find(|(label, _)| name.contains(label))
                .map(|(_, c)| (*c).to_owned());
                slot.state = if old["available"] == true && slot.color.is_some() {
                    "loaded"
                } else {
                    "unavailable"
                }
                .into();
            }
        }
        value.revision = 1;
        value.validate()?;
        self.write(&value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn substitution_acceptance_is_specific_to_requirement_slot_and_actual_pen() {
        let dir = tempfile::tempdir().unwrap();
        let store = crate::store::Store::open(dir.path().to_owned()).unwrap();
        let mut job = store
            .import(
                "test.hpgl",
                b"IN;CO\"pen id=1 r=0 g=0 b=0 width_mm=0.3\";SP1;PU0,0;PD40,0;PU;SP0;",
                40.,
            )
            .unwrap();
        job.pdf = Some(crate::store::PdfSource {
            raster_preview: false,
            document_id: "fixture".into(),
            conversion_id: "fixture".into(),
            page_index: 0,
            registration: crate::media::Registration::for_media(
                &crate::media::profile([431.8, 279.4]).unwrap(),
            )
            .unwrap(),
        });
        let mut carousel = Carousel::default();
        carousel.slots.insert(
            7,
            Slot {
                state: "loaded".into(),
                color: Some("#0000ff".into()),
                width_mm: Some(0.5),
                ..Slot::default()
            },
        );
        let mut setup = Setup::new(&job);
        setup.pen_map.insert(1, 0);
        assert!(
            carousel
                .resolve(&mut setup, &job)
                .unwrap_err()
                .to_string()
                .contains("unassigned")
        );
        setup.pen_map.insert(1, 7);
        assert!(carousel.resolve(&mut setup, &job).is_err());
        setup.accepted_substitutions.insert(
            1,
            crate::layout::PenSubstitution {
                slot: 7,
                color: "#0000ff".into(),
                width_mm: Some(0.5),
                requested_color: "#000000".into(),
                requested_width_mm: Some(0.3),
            },
        );
        assert_eq!(carousel.resolve(&mut setup, &job).unwrap()[&1], "#0000ff");
        assert_eq!(setup.ink_settings[&1].width_mm, Some(0.5));
        carousel.slots.get_mut(&7).unwrap().width_mm = Some(0.7);
        assert!(carousel.resolve(&mut setup, &job).is_err());
        carousel.slots.get_mut(&7).unwrap().width_mm = Some(0.5);
        job.summary.pens[0].width_mm = Some(0.4);
        assert!(carousel.resolve(&mut setup, &job).is_err());
        job.summary.pens[0].width_mm = Some(0.3);
        carousel.slots.get_mut(&7).unwrap().state = "unavailable".into();
        assert!(carousel.resolve(&mut setup, &job).is_err());
        // Older conversions may carry the scan swatch as their requested
        // color. Separating it from the design swatch is not a pen swap.
        let pen = carousel.slots.get_mut(&7).unwrap();
        pen.state = "loaded".into();
        pen.measured_color = Some("#000000".into());
        pen.width_mm = Some(0.3);
        setup.accepted_substitutions.clear();
        assert!(carousel.resolve(&mut setup, &job).is_ok());
        carousel.slots.get_mut(&7).unwrap().width_mm = Some(0.4);
        assert!(
            carousel.resolve(&mut setup, &job).is_err(),
            "Appearance alias never excuses a width substitution"
        );
    }
    #[test]
    fn inventory_persists_and_rejects_stale_or_invalid_edits() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("carousel.json");
        let inventory = Inventory::new(path.clone());
        let mut value = inventory.get().unwrap();
        value.slots.insert(
            6,
            Slot {
                label: "purple".into(),
                state: "loaded".into(),
                color: Some("#800080".into()),
                width_mm: Some(0.7),
                ..Slot::default()
            },
        );
        let saved = inventory.save(value.clone()).unwrap();
        assert!(inventory.save(value).is_err());
        assert_eq!(Inventory::new(path).get().unwrap(), saved);
        let mut invalid = saved.clone();
        invalid.slots.get_mut(&6).unwrap().width_mm = Some(-1.);
        assert!(inventory.save(invalid).is_err());
        inventory
            .migrate(&serde_json::json!({"6":{"label":"red","available":true}}))
            .unwrap();
        assert_eq!(inventory.get().unwrap(), saved);
    }
}
