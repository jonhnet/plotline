use crate::hpgl::{self, Summary};
use crate::layout::{Observation, Setup};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

pub const MAX_BYTES: usize = 64 * 1024 * 1024;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PdfSource {
    pub document_id: String,
    pub conversion_id: String,
    pub page_index: u32,
    pub registration: crate::media::Registration,
    #[serde(default)]
    pub raster_preview: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Job {
    pub schema: u32,
    pub id: String,
    pub name: String,
    pub created_at: u64,
    pub bytes: usize,
    pub sha256: String,
    pub units_per_mm: f64,
    pub summary: Summary,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pdf: Option<PdfSource>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub calibration: Option<crate::calibration::Source>,
}

impl Job {
    pub fn registration(&self) -> Option<&crate::media::Registration> {
        self.pdf
            .as_ref()
            .map(|p| &p.registration)
            .or_else(|| self.calibration.as_ref().map(|c| &c.registration))
    }
}

#[derive(Clone)]
pub struct Store {
    pub root: PathBuf,
    pub carousel: std::sync::Arc<crate::carousel::Inventory>,
}

fn durable_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = File::create(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

impl Store {
    pub fn open(root: PathBuf) -> Result<Self> {
        fs::create_dir_all(&root)?;
        let store = Self {
            carousel: std::sync::Arc::new(crate::carousel::Inventory::new(
                root.join("carousel.json"),
            )),
            root: root.canonicalize()?,
        };
        crate::calibration::migrate_color_measurements(&store)?;
        Ok(store)
    }

    pub fn path(&self, id: &str) -> Result<PathBuf> {
        ensure!(
            Uuid::parse_str(id).is_ok_and(|uuid| uuid.to_string() == id),
            "Invalid job ID"
        );
        Ok(self.root.join(id))
    }

    pub fn get(&self, id: &str) -> Result<Job> {
        Ok(serde_json::from_slice(&fs::read(
            self.path(id)?.join("job.json"),
        )?)?)
    }

    pub fn list(&self) -> Result<Vec<Job>> {
        let mut visible = vec![];
        for job in self.list_all()? {
            if self.is_current(&job)? {
                visible.push(job);
            }
        }
        Ok(visible)
    }
    pub fn is_current(&self, job: &Job) -> Result<bool> {
        let Some(pdf) = &job.pdf else { return Ok(true) };
        let path = self
            .root
            .join("documents")
            .join(&pdf.document_id)
            .join("draft.json");
        if !path.exists() {
            return Ok(false);
        }
        let draft: serde_json::Value = serde_json::from_slice(&fs::read(path)?)?;
        Ok(draft["jobs"]
            .as_array()
            .is_some_and(|rows| rows.iter().any(|row| row["job_id"] == job.id)))
    }
    pub fn list_all(&self) -> Result<Vec<Job>> {
        let mut jobs = vec![];
        for entry in fs::read_dir(&self.root)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            if Uuid::parse_str(&name).is_err() {
                continue;
            }
            // Do not turn damaged saved metadata into an apparently empty library.
            jobs.push(self.get(&name)?);
        }
        jobs.sort_by(|a, b| b.created_at.cmp(&a.created_at).then(b.id.cmp(&a.id)));
        Ok(jobs)
    }

    pub fn observation(&self) -> Result<Option<Observation>> {
        let path = self.root.join("device-observation.json");
        if !path.exists() {
            return Ok(None);
        }
        Ok(Some(serde_json::from_slice(&fs::read(path)?)?))
    }

    pub fn setup(&self, id: &str) -> Result<Setup> {
        let job = self.get(id)?;
        let path = self.path(id)?.join("setup.json");
        let mut setup = if path.exists() {
            serde_json::from_slice(&fs::read(path)?)?
        } else {
            Setup::new(&job)
        };
        setup.upgrade(&job);
        setup.validate(&job)?;
        // A newly opened document uses today's physical inventory. The browser
        // then carries this revision until it refreshes its preview.
        setup.carousel_revision = Some(self.carousel.get()?.revision);
        Ok(setup)
    }

    pub fn save_setup(&self, id: &str, setup: &Setup) -> Result<()> {
        setup.validate(&self.get(id)?)?;
        let dir = self.path(id)?;
        let temp = dir.join(format!(".setup-{}", Uuid::new_v4()));
        let result = (|| -> Result<()> {
            durable_write(&temp, &serde_json::to_vec_pretty(setup)?)?;
            fs::rename(&temp, dir.join("setup.json"))?;
            File::open(&dir)?.sync_all()?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(temp);
        }
        result
    }

    pub fn import(&self, name: &str, source: &[u8], units_per_mm: f64) -> Result<Job> {
        self.import_inner(name, source, units_per_mm, None, None)
    }
    pub fn import_pdf(&self, name: &str, source: &[u8], pdf: PdfSource) -> Result<Job> {
        self.import_inner(name, source, 40., Some(pdf), None)
    }
    pub fn import_calibration(
        &self,
        name: &str,
        source: &[u8],
        calibration: crate::calibration::Source,
    ) -> Result<Job> {
        self.import_inner(name, source, 40., None, Some(calibration))
    }
    fn import_inner(
        &self,
        name: &str,
        source: &[u8],
        units_per_mm: f64,
        pdf: Option<PdfSource>,
        calibration: Option<crate::calibration::Source>,
    ) -> Result<Job> {
        ensure!(
            !source.is_empty() && source.len() <= MAX_BYTES,
            "HPGL file must be between 1 byte and 64 MiB"
        );
        ensure!(
            !name.trim().is_empty()
                && name.len() <= 180
                && !name
                    .chars()
                    .any(|c| c.is_control() || c == '/' || c == '\\'),
            "Use a filename of at most 180 bytes without path separators"
        );
        let text = std::str::from_utf8(source)?;
        let drawing = hpgl::parse(text, units_per_mm)?;
        let job = Job {
            schema: 1,
            id: Uuid::new_v4().to_string(),
            name: name.into(),
            created_at: SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
            bytes: source.len(),
            sha256: format!("{:x}", Sha256::digest(source)),
            units_per_mm,
            summary: drawing.summary.clone(),
            pdf,
            calibration,
        };
        let staging = self.root.join(format!(".import-{}", job.id));
        fs::create_dir(&staging)?;
        let result = (|| -> Result<()> {
            durable_write(&staging.join("source.hpgl"), source)?;
            durable_write(&staging.join("preview.svg"), drawing.svg().as_bytes())?;
            durable_write(&staging.join("job.json"), &serde_json::to_vec_pretty(&job)?)?;
            File::open(&staging)?.sync_all()?;
            fs::rename(&staging, self.path(&job.id)?)?;
            File::open(&self.root)?.sync_all()?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_dir_all(&staging);
        }
        result?;
        Ok(job)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn durable_import_is_exact_and_invalid_input_leaves_no_job() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path().to_owned()).unwrap();
        let bytes = b"IN;\r\nSP1;PU10,20;PD50,60;PU;SP0;\r\n";
        let job = store.import("a <drawing>.plt", bytes, 40.0).unwrap();
        let reopened = Store::open(dir.path().to_owned()).unwrap();
        assert_eq!(reopened.list().unwrap().len(), 1);
        assert_eq!(
            fs::read(reopened.path(&job.id).unwrap().join("source.hpgl")).unwrap(),
            bytes
        );
        assert!(store.import("../escape", bytes, 40.0).is_err());
        assert!(store.import("bad.hpgl", b"IN;SC0,1,0,1;", 40.0).is_err());
        assert!(store.path("../escape").is_err());
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 1);
    }
    #[test]
    fn registered_setup_survives_json_roundtrips_without_moving_paper() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path().to_owned()).unwrap();
        // Seattle's portrait Tabloid map, wide margin at the bottom. These
        // fractional results exposed precision loss in the JSON parser.
        let registration = crate::media::Registration {
            sheet_size_mm: [279.4, 431.8],
            sheet_to_device_units: [0., -40., -40., 0., 8156., 5511.999999999999],
            limits: [-7539, -5071, 7539, 5071],
            rotation: 270,
            evidence: "roundtrip fixture".into(),
        };
        let job = store
            .import_pdf(
                "Seattle.hpgl",
                b"IN;SP1;PU0,0;PD40,40;PU;SP0;",
                PdfSource {
                    document_id: Uuid::new_v4().to_string(),
                    conversion_id: Uuid::new_v4().to_string(),
                    page_index: 0,
                    registration,
                    raster_preview: false,
                },
            )
            .unwrap();
        let setup = Setup::new(&job);
        setup.validate(&job).unwrap();
        store.save_setup(&job.id, &setup).unwrap();
        let serialized = serde_json::to_vec(&setup).unwrap();
        let parsed: Setup = serde_json::from_slice(&serialized).unwrap();
        assert_eq!(
            parsed.margins_mm, setup.margins_mm,
            "JSON must preserve the exact computed margins"
        );
        let reopened = Store::open(dir.path().to_owned()).unwrap();
        let saved = reopened.setup(&job.id).unwrap();
        assert_eq!(saved.registration, setup.registration);
        assert_eq!(saved.margins_mm, setup.margins_mm);
        // The fix must preserve the lock, not loosen its comparison. Reject
        // actual paper/placement edits and leave the saved recipe untouched.
        let saved_path = reopened.path(&job.id).unwrap().join("setup.json");
        let original = fs::read(&saved_path).unwrap();
        for field in 0..9 {
            let mut changed = saved.clone();
            match field {
                0..=3 => changed.margins_mm[field] += 0.001,
                4 => changed.paper_x_mm += 0.001,
                5 => changed.paper_y_mm += 0.001,
                6 => changed.rotation = 90,
                7 => changed.window = "reported".into(),
                _ => changed.registration.as_mut().unwrap().sheet_to_device_units[4] += 1.,
            }
            assert!(reopened.save_setup(&job.id, &changed).is_err());
            assert_eq!(fs::read(&saved_path).unwrap(), original);
        }
        // View and pen choices remain independent of the fixed registration.
        let mut choices = saved;
        choices.facing = "plotter".into();
        choices.pen_map.insert(1, 7);
        choices.ink_settings.get_mut(&1).unwrap().speed_cm_s = 9;
        let through_api: Setup =
            serde_json::from_str(&serde_json::to_string(&choices).unwrap()).unwrap();
        reopened.save_setup(&job.id, &through_api).unwrap();
        let restored = reopened.setup(&job.id).unwrap();
        assert_eq!(restored.facing, "plotter");
        assert_eq!(restored.pen_map[&1], 7);
        assert_eq!(restored.ink_settings[&1].speed_cm_s, 9);
        assert_eq!(restored.registration, setup.registration);
    }

    #[test]
    fn layout_survives_reopening_and_invalid_updates_preserve_it() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path().to_owned()).unwrap();
        let bytes = b"IN;SP1;PU0,0;PD40,40;PU;SP0;";
        let job = store.import("layout.hpgl", bytes, 40.).unwrap();
        let mut setup = store.setup(&job.id).unwrap();
        setup.rotation = 270;
        setup.pen_map.insert(1, 7);
        store.save_setup(&job.id, &setup).unwrap();
        // A layout from before per-ink controls must retain its rotation/slots.
        let path = store.path(&job.id).unwrap().join("setup.json");
        let mut legacy = serde_json::to_value(&setup).unwrap();
        legacy.as_object_mut().unwrap().remove("ink_settings");
        fs::write(&path, serde_json::to_vec(&legacy).unwrap()).unwrap();
        setup.rotation = 45;
        assert!(store.save_setup(&job.id, &setup).is_err());
        let reopened = Store::open(dir.path().to_owned()).unwrap();
        let mut saved = reopened.setup(&job.id).unwrap();
        assert_eq!(saved.rotation, 270);
        assert_eq!(saved.pen_map[&1], 7);
        assert_eq!(saved.ink_settings[&1].width_mm, None);
        assert_eq!(saved.ink_settings[&1].speed_cm_s, 5);
        assert_eq!(saved.ink_settings[&1].force, 4);
        assert_eq!(
            saved.profile(None).unwrap().limits,
            [-7556, -4988, 7556, 4988]
        );
        saved.window = "reported".into();
        assert!(saved.profile(None).is_err());
        fs::write(dir.path().join("device-observation.json"), br#"{"identity":"7585B","observed_at":"2026-10-29","units_per_mm":40,"limits":[-7000,-5000,8000,6000]}"#).unwrap();
        assert_eq!(
            saved
                .profile(reopened.observation().unwrap().as_ref())
                .unwrap()
                .limits,
            [-7000, -5000, 8000, 6000]
        );
        assert_eq!(
            fs::read(reopened.path(&job.id).unwrap().join("source.hpgl")).unwrap(),
            bytes
        );
        assert_eq!(reopened.list().unwrap().len(), 1);
    }
}
