//! Durable pen calibration targets, scan analysis and explicit inventory updates.
use crate::{
    carousel::Carousel,
    media::Registration,
    store::Store,
    web::{ApiError, check_write},
};
use anyhow::{Context, Result, ensure};
use axum::{
    Json, Router,
    body::Bytes,
    extract::{DefaultBodyLimit, Path, State},
    http::{HeaderMap, StatusCode, header},
    response::IntoResponse,
    routing::{get, post},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Write,
    path::{Path as FsPath, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Source {
    pub id: String,
    pub registration: Registration,
    pub carousel: Carousel,
}
#[derive(Clone)]
struct App {
    store: Store,
    project: PathBuf,
    analysis: Arc<Mutex<()>>,
    edits: Arc<Mutex<()>>,
}
fn error(e: impl std::fmt::Display) -> ApiError {
    ApiError(StatusCode::UNPROCESSABLE_ENTITY, e.to_string())
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}
fn read(path: impl AsRef<FsPath>) -> Result<Value> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}
fn write(path: impl AsRef<FsPath>, value: &Value) -> Result<()> {
    let path = path.as_ref();
    let temp = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    let mut f = File::create(&temp)?;
    f.write_all(&serde_json::to_vec_pretty(value)?)?;
    f.sync_all()?;
    fs::rename(temp, path)?;
    File::open(path.parent().unwrap())?.sync_all()?;
    Ok(())
}
/// Recover the design swatches that the original calibration API overwrote.
/// Only an exact match to a recorded application is eligible; subsequent pen
/// edits are preserved. The journal makes inventory + receipt updates resumable.
pub fn migrate_color_measurements(store: &Store) -> Result<()> {
    let root = store.root.join("calibrations");
    if !root.exists() {
        return Ok(());
    }
    let marker = root.join("color-appearance-migration.json");
    let mut migration = if marker.exists() {
        read(&marker)?
    } else {
        let before = store.carousel.get()?;
        let mut after = before.clone();
        let mut receipts = vec![];
        for target_dir in fs::read_dir(&root)?.flatten() {
            let directory = target_dir.path();
            let Ok(target) = read(directory.join("target.json")) else {
                continue;
            };
            let original: Carousel = serde_json::from_value(target["carousel"].clone())?;
            let Ok(scans) = fs::read_dir(directory.join("scans")) else {
                continue;
            };
            for scan in scans.flatten() {
                let file = scan.path().join("applied.json");
                let Ok(mut receipt) = read(&file) else {
                    continue;
                };
                let saved: Carousel = serde_json::from_value(receipt["after"].clone())?;
                let report = read(scan.path().join("report.json"))?;
                let mut changed = false;
                for row in report["rows"]
                    .as_array()
                    .context("Missing measurement rows")?
                {
                    let Some(slot) = row["slot"].as_u64().and_then(|n| u8::try_from(n).ok()) else {
                        continue;
                    };
                    let Some(pen) = after.slots.get_mut(&slot) else {
                        continue;
                    };
                    if pen.measured_color.is_some()
                        || Some(&*pen) != saved.slots.get(&slot)
                        || row["valid"] != true
                        || pen.color.as_deref() != row["color_srgb"].as_str()
                    {
                        continue;
                    }
                    let Some(design) = original.slots.get(&slot) else {
                        continue;
                    };
                    pen.measured_color = pen.color.clone();
                    pen.color = design.color.clone();
                    receipt["after"]["slots"][slot.to_string()] = serde_json::to_value(&*pen)?;
                    changed = true;
                }
                if changed {
                    receipt["color_model"] = json!("design-and-measured-appearance");
                    receipts.push(json!({"path":file.strip_prefix(&store.root)?,"value":receipt}));
                }
            }
        }
        after.validate()?;
        if after != before {
            after.revision += 1;
        }
        let journal = json!({"before":before,"after":after,"receipts":receipts,"complete":false});
        write(&marker, &journal)?;
        journal
    };
    if migration["complete"] == true {
        return Ok(());
    }
    let current = store.carousel.get()?;
    let expected: Carousel = serde_json::from_value(migration["after"].clone())?;
    if current != expected {
        ensure!(
            serde_json::to_value(&current)? == migration["before"],
            "Carousel changed during color migration; retained journal needs review"
        );
        let mut next = expected.clone();
        next.revision = current.revision;
        ensure!(
            store.carousel.save(next)? == expected,
            "Unexpected migrated carousel revision"
        );
    }
    for receipt in migration["receipts"]
        .as_array()
        .context("Missing migration receipts")?
    {
        write(
            store
                .root
                .join(receipt["path"].as_str().context("Missing receipt path")?),
            &receipt["value"],
        )?;
    }
    migration["complete"] = json!(true);
    write(marker, &migration)
}
fn path(app: &App, id: &str) -> Result<PathBuf> {
    ensure!(
        Uuid::parse_str(id).is_ok_and(|u| u.to_string() == id),
        "Invalid calibration ID"
    );
    Ok(app.store.root.join("calibrations").join(id))
}
fn scan_path(app: &App, id: &str, scan: &str) -> Result<PathBuf> {
    ensure!(
        Uuid::parse_str(scan).is_ok_and(|u| u.to_string() == scan),
        "Invalid scan ID"
    );
    let p = path(app, id)?;
    ensure!(
        p.join("target.json").exists(),
        "Calibration sheet not found"
    );
    Ok(p.join("scans").join(scan))
}
fn worker(app: &App, command: &str, directory: &FsPath) -> Result<()> {
    let python = std::env::var_os("PLOTLINE_CALIBRATION_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| app.project.join("build/calibration-venv/bin/python"));
    let log = File::create(directory.join("analysis.log"))?;
    let mut child = Command::new(python)
        .arg(app.project.join("tools/pen_calibration.py"))
        .arg(command)
        .arg(directory)
        .stdin(Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log)
        .spawn()
        .context("Install the calibration environment described in controller/README.md")?;
    let started = std::time::Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            ensure!(
                status.success(),
                "{}",
                read(directory.join("error.json"))
                    .ok()
                    .and_then(|v| v["error"].as_str().map(str::to_owned))
                    .unwrap_or_else(|| "Scan analysis failed; see analysis.log".into())
            );
            return Ok(());
        }
        if started.elapsed() > Duration::from_secs(180) {
            child.kill()?;
            child.wait()?;
            anyhow::bail!("Scan analysis exceeded three minutes. Try a smaller scan.");
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}
fn scan_info(directory: &FsPath) -> Result<Value> {
    let mut value = read(directory.join("status.json"))?;
    for (name, file) in [
        ("image", "image.json"),
        ("progress", "progress.json"),
        ("report", "report.json"),
        ("applied", "applied.json"),
    ] {
        if let Ok(data) = read(directory.join(file)) {
            value[name] = data;
        }
    }
    if let Ok(meta) = fs::metadata(directory.join("original.scan")) {
        value["uploaded_at"] = json!(meta.modified()?.duration_since(UNIX_EPOCH)?.as_millis());
    }
    Ok(value)
}
fn schedule(app: App, directory: PathBuf) {
    std::thread::spawn(move || {
        let _guard = app.analysis.lock().unwrap();
        let result = (|| -> Result<()> {
            write(directory.join("status.json"), &json!({"status":"running"}))?;
            worker(&app, "analyze", &directory)
        })();
        let status = match result {
            Ok(()) => json!({"status":"ready"}),
            Err(e) => json!({"status":"error","error":e.to_string()}),
        };
        let _ = write(directory.join("status.json"), &status);
    });
}
async fn blocking<T: Send + 'static>(
    operation: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T, ApiError> {
    tokio::task::spawn_blocking(operation)
        .await
        .map_err(error)?
        .map_err(error)
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Create {
    slots: Vec<u8>,
    #[serde(default)]
    marker_slot: Option<u8>,
    revision: u64,
    #[serde(default)]
    paper: String,
}
fn letter_registration() -> Result<Registration> {
    // Letter must load with 8.5 inches along feed (X), 11 across the carriage
    // (Y). The generic media default puts the long dimension along feed.
    // Keep this loading explicit: merely swapping page width/height would
    // still leave the 39 mm back margin on a short edge of the sheet.
    Registration::for_media(&json!({
        "sheet_size_mm": [279.4, 215.9],
        "calibration": {
            "device": "HP7585B", "units_per_mm": 40.,
            "sheet_to_device_units": [0., 40., 40., 0., -4798., -5588.],
            "hard_clip_units": [-3238, -4988, 3238, 4988],
            "evidence": "Letter loaded 8.5 inches along feed, 11 across carriage; nominal back/front/left/right margins 39/15/15/15 mm. Loaded limits checked at Plot."
        }
    }))
}
async fn create(
    State(app): State<App>,
    headers: HeaderMap,
    Json(request): Json<Create>,
) -> Result<Json<Value>, ApiError> {
    check_write(&headers)?;
    blocking(move||{
        let carousel=app.store.carousel.get()?;
        ensure!(carousel.revision==request.revision,"Carousel changed. Refresh before creating the sheet.");
        let mut slots=request.slots;slots.sort();slots.dedup();
        ensure!(!slots.is_empty()&&slots.len()<=8&&slots.iter().all(|n|carousel.slots.get(n).is_some_and(|p|p.usable())),"Select loaded pens for calibration");
        ensure!(request.marker_slot.is_none_or(|n|carousel.slots.get(&n).is_some_and(|p|p.usable())),"Choose a loaded pen for registration marks");
        ensure!(request.paper.len()<=200,"Paper description must be at most 200 bytes");
        let id=Uuid::new_v4().to_string();let directory=path(&app,&id)?;fs::create_dir_all(&directory)?;
        let registration=letter_registration()?;
        let code=100000+(Uuid::new_v4().as_u128()%900000) as u64;
        let target=json!({"id":id,"sheet_code":code,"created_at":now(),"slots":slots,"marker_slot":request.marker_slot,"carousel":carousel,"paper":request.paper,"registration":registration});
        write(directory.join("target.json"),&target)?;worker(&app,"generate",&directory)?;
        let job=app.store.import_calibration(&format!("Pen calibration {code}.hpgl"),&fs::read(directory.join("target.hpgl"))?,Source{id:id.clone(),registration,carousel})?;
        let mut target=read(directory.join("target.json"))?;target["job_id"]=json!(job.id);write(directory.join("target.json"),&target)?;
        Ok(Json(target))
    }).await
}
async fn list(State(app): State<App>) -> Result<Json<Value>, ApiError> {
    blocking(move || {
        let root = app.store.root.join("calibrations");
        let mut rows = vec![];
        if root.exists() {
            for d in fs::read_dir(root)? {
                let d = d?;
                if let Ok(t) = read(d.path().join("target.json"))
                    && t.get("job_id").is_some()
                {
                    rows.push(t);
                }
            }
        }
        rows.sort_by_key(|t| std::cmp::Reverse(t["created_at"].as_u64().unwrap_or(0)));
        Ok(Json(json!(rows)))
    })
    .await
}
async fn target(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Value>, ApiError> {
    blocking(move || {
        let p = path(&app, &id)?;
        let mut t = read(p.join("target.json"))?;
        let mut scans = vec![];
        if p.join("scans").exists() {
            for d in fs::read_dir(p.join("scans"))? {
                let d = d?;
                let mut s = scan_info(&d.path())?;
                s["id"] = json!(d.file_name().to_string_lossy());
                scans.push(s);
            }
        }
        scans.sort_by_key(|s| s["uploaded_at"].as_u64().unwrap_or(0));
        t["scans"] = json!(scans);
        Ok(Json(t))
    })
    .await
}
async fn upload(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Json<Value>, ApiError> {
    check_write(&headers)?;
    let scheduled = app.clone();
    let (directory, scan) = blocking(move || {
        ensure!(!body.is_empty(), "Choose a PNG or TIFF scan");
        let scan = Uuid::new_v4().to_string();
        let directory = scan_path(&app, &id, &scan)?;
        fs::create_dir_all(&directory)?;
        let mut file = File::create(directory.join("original.scan"))?;
        file.write_all(&body)?;
        file.sync_all()?;
        write(directory.join("options.json"), &json!({}))?;
        write(directory.join("status.json"), &json!({"status":"queued"}))?;
        Ok((directory, scan))
    })
    .await?;
    schedule(scheduled, directory);
    Ok(Json(json!({"id":scan,"status":"queued"})))
}
async fn scan(
    State(app): State<App>,
    Path((id, scan)): Path<(String, String)>,
) -> Result<Json<Value>, ApiError> {
    blocking(move || Ok(Json(scan_info(&scan_path(&app, &id, &scan)?)?))).await
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MarkOptions {
    marks: Option<[[f64; 2]; 4]>,
}
async fn retry(
    State(app): State<App>,
    Path((id, scan)): Path<(String, String)>,
    headers: HeaderMap,
    Json(options): Json<MarkOptions>,
) -> Result<Json<Value>, ApiError> {
    check_write(&headers)?;
    let scheduled = app.clone();
    let directory=blocking(move||{
        let _guard=app.edits.lock().unwrap();let p=scan_path(&app,&id,&scan)?;let old=scan_info(&p)?;
        ensure!(old["status"]=="error","Only failed scans can be retried; upload another scan to retain a completed measurement");
        if let Some(marks)=options.marks{ensure!(marks.iter().flatten().all(|v|v.is_finite()&&(0. ..=1.).contains(v)),"Invalid cross positions");}
        write(p.join("options.json"),&json!({"marks":options.marks}))?;
        for file in ["report.json","error.json"]{let _=fs::remove_file(p.join(file));}
        write(p.join("status.json"),&json!({"status":"queued"}))?;Ok(p)
    }).await?;
    schedule(scheduled, directory);
    Ok(Json(json!({"status":"queued"})))
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Selection {
    slot: u8,
    width: bool,
    color: bool,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Apply {
    revision: u64,
    selections: Vec<Selection>,
}
async fn apply(
    State(app): State<App>,
    Path((id, scan)): Path<(String, String)>,
    headers: HeaderMap,
    Json(request): Json<Apply>,
) -> Result<Json<Value>, ApiError> {
    check_write(&headers)?;
    blocking(move || {
        let _guard = app.edits.lock().unwrap();
        let p = scan_path(&app, &id, &scan)?;
        let target = read(path(&app, &id)?.join("target.json"))?;
        let status = scan_info(&p)?;
        ensure!(status["status"] == "ready", "Wait for a completed measurement");
        let before = app.store.carousel.get()?;
        let key = format!("{:x}", Sha256::digest(serde_json::to_vec(&request)?));
        let receipt = p.join(format!("apply-{key}.json"));
        let pending = p.join(format!("apply-request-{key}.json"));
        let latest = p.join("applied.json");
        if receipt.exists() {
            return Ok(Json(json!({"carousel":before,"applied":read(receipt)?,"already_applied":true})));
        }
        // Recover a response lost after the atomic carousel replacement. The
        // journal contains both complete inventory states before mutation.
        if let Ok(journal) = read(&pending)
            && serde_json::to_value(&before)? == journal["after"] {
                write(&receipt, &journal)?; write(&latest, &journal)?;
                return Ok(Json(json!({"carousel":before,"applied":journal,"already_applied":true})));
        }
        ensure!(before.revision == request.revision, "Carousel changed. Refresh and review before applying.");
        let expected: Carousel = serde_json::from_value(read(&latest).ok().map(|v|v["after"].clone()).unwrap_or_else(||target["carousel"].clone()))?;
        let mut after = before.clone();
        let mut changed = std::collections::BTreeSet::new();
        for s in &request.selections {
            if !s.width && !s.color { continue; }
            ensure!(changed.insert(s.slot), "Duplicate selected slot");
            ensure!(before.slots.get(&s.slot) == expected.slots.get(&s.slot), "Slot {} has changed since this pen was measured. Create a new sheet for the current pen.",s.slot);
            let row = status["report"]["rows"].as_array().context("Missing measurements")?.iter()
                .find(|r|r["slot"] == s.slot && r["valid"] == true).context("This pen has no reliable measurement")?;
            let pen = after.slots.get_mut(&s.slot).context("Unknown slot")?;
            if s.width { pen.width_mm = Some(row["width_mm"].as_f64().context("Missing width")?); }
            if s.color { pen.measured_color = Some(row["color_srgb"].as_str().context("Missing color")?.to_owned()); }
        }
        ensure!(!changed.is_empty(), "Select widths or colors to apply");
        after.validate()?;
        if after == before { return Ok(Json(json!({"carousel":before,"already_applied":true}))); }
        let mut expected_after = after.clone(); expected_after.revision += 1;
        let journal = json!({"target":id,"scan":scan,"at":now(),"request":request,"before":before,"after":expected_after,"slots":changed});
        write(&pending, &journal)?;
        let saved = app.store.carousel.save(after)?;
        write(&receipt, &journal)?; write(&latest, &journal)?;
        Ok(Json(json!({"carousel":saved,"applied":journal})))
    }).await
}
async fn target_svg(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, ApiError> {
    let data = blocking(move || Ok(fs::read(path(&app, &id)?.join("target.svg"))?)).await?;
    Ok(([(header::CONTENT_TYPE, "image/svg+xml")], data))
}
async fn image(
    State(app): State<App>,
    Path((id, scan, file)): Path<(String, String, String)>,
) -> Result<impl IntoResponse, ApiError> {
    let mime = match file.as_str() {
        "preview.jpg" | "overlay.jpg" => "image/jpeg",
        "original.scan" => "application/octet-stream",
        name if (1..=8).any(|n| name == format!("slot-{n}.png")) => "image/png",
        _ => return Err(error("Unknown scan artifact")),
    };
    let data = blocking(move || Ok(fs::read(scan_path(&app, &id, &scan)?.join(file))?)).await?;
    Ok(([(header::CONTENT_TYPE, mime)], data))
}
pub fn router(store: Store) -> Router {
    let project = std::env::var_os("PLOTLINE_PDF_PROJECT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .to_owned()
        });
    let app = App {
        store,
        project,
        analysis: Arc::new(Mutex::new(())),
        edits: Arc::new(Mutex::new(())),
    };
    // A restarted service never pretends that an orphaned worker is progressing.
    if let Ok(targets) = fs::read_dir(app.store.root.join("calibrations")) {
        for t in targets.flatten() {
            if let Ok(scans) = fs::read_dir(t.path().join("scans")) {
                for s in scans.flatten() {
                    let p = s.path().join("status.json");
                    if let Ok(v) = read(&p)
                        && (v["status"] == "running" || v["status"] == "queued")
                    {
                        let _ = write(
                            p,
                            &json!({"status":"error","error":"Analysis interrupted by a service restart. Retry this scan."}),
                        );
                    }
                }
            }
        }
    }
    Router::new()
        .route(
            "/calibration",
            get(|| async { axum::response::Html(include_str!("../web/calibration.html")) }),
        )
        .route(
            "/calibration.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript")],
                    include_str!("../web/calibration.js"),
                )
            }),
        )
        .route(
            "/calibration.css",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/css")],
                    include_str!("../web/calibration.css"),
                )
            }),
        )
        .route("/api/calibrations", get(list).post(create))
        .route("/api/calibrations/{id}", get(target))
        .route("/api/calibrations/{id}/target.svg", get(target_svg))
        .route(
            "/api/calibrations/{id}/scans",
            post(upload).layer(DefaultBodyLimit::max(512 * 1024 * 1024)),
        )
        .route("/api/calibrations/{id}/scans/{scan}", get(scan))
        .route("/api/calibrations/{id}/scans/{scan}/retry", post(retry))
        .route("/api/calibrations/{id}/scans/{scan}/apply", post(apply))
        .route("/api/calibrations/{id}/scans/{scan}/{file}", get(image))
        .with_state(app)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{carousel::Slot, layout::Setup};
    fn fixture() -> (tempfile::TempDir, App, String, String) {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(temp.path().to_owned()).unwrap();
        let mut c = store.carousel.get().unwrap();
        c.slots.insert(
            2,
            Slot {
                label: "Blue".into(),
                state: "loaded".into(),
                color: Some("#0000ff".into()),
                width_mm: None,
                ..Slot::default()
            },
        );
        store.carousel.save(c).unwrap();
        let app = App {
            store,
            project: PathBuf::new(),
            analysis: Arc::new(Mutex::new(())),
            edits: Arc::new(Mutex::new(())),
        };
        let id = Uuid::new_v4().to_string();
        let scan = Uuid::new_v4().to_string();
        let p = path(&app, &id).unwrap();
        fs::create_dir_all(&p).unwrap();
        write(
            p.join("target.json"),
            &json!({"carousel":app.store.carousel.get().unwrap()}),
        )
        .unwrap();
        let sp = scan_path(&app, &id, &scan).unwrap();
        fs::create_dir_all(&sp).unwrap();
        write(sp.join("status.json"), &json!({"status":"ready"})).unwrap();
        write(
            sp.join("report.json"),
            &json!({"rows":[{"slot":2,"valid":true,"width_mm":0.43,"color_srgb":"#182ca1"}]}),
        )
        .unwrap();
        (temp, app, id, scan)
    }
    fn headers() -> HeaderMap {
        let mut h = HeaderMap::new();
        h.insert("x-plotline-request", "1".parse().unwrap());
        h
    }
    #[test]
    fn legacy_scan_colors_migrate_once_with_recovery_and_preserve_manual_edits() {
        for manual_edit in [false, true] {
            let (_temp, app, id, scan) = fixture();
            let before = app.store.carousel.get().unwrap();
            let mut old = before.clone();
            old.slots.get_mut(&2).unwrap().color = Some("#182ca1".into());
            old.slots.get_mut(&2).unwrap().width_mm = Some(0.43);
            let applied = app.store.carousel.save(old).unwrap();
            let p = scan_path(&app, &id, &scan).unwrap();
            write(
                p.join("applied.json"),
                &json!({"before":before,"after":applied}),
            )
            .unwrap();
            if manual_edit {
                let mut edited = applied.clone();
                edited.slots.get_mut(&2).unwrap().color = Some("#123456".into());
                app.store.carousel.save(edited).unwrap();
            }
            let previous = app.store.carousel.get().unwrap();
            migrate_color_measurements(&app.store).unwrap();
            let current = app.store.carousel.get().unwrap();
            if manual_edit {
                assert_eq!(current, previous);
            } else {
                assert_eq!(current.slots[&2].color, before.slots[&2].color);
                assert_eq!(current.slots[&2].measured_color, applied.slots[&2].color);
                assert_eq!(current.slots[&2].width_mm, Some(0.43));
                assert_eq!(current.revision, previous.revision + 1);
                let receipt: Carousel =
                    serde_json::from_value(read(p.join("applied.json")).unwrap()["after"].clone())
                        .unwrap();
                assert_eq!(receipt.slots[&2], current.slots[&2]);
            }
            // Recover an interruption after the inventory/receipt writes.
            let journal = app
                .store
                .root
                .join("calibrations/color-appearance-migration.json");
            let mut pending = read(&journal).unwrap();
            pending["complete"] = json!(false);
            write(&journal, &pending).unwrap();
            migrate_color_measurements(&app.store).unwrap();
            migrate_color_measurements(&app.store).unwrap();
            assert_eq!(app.store.carousel.get().unwrap(), current);
        }
    }
    #[tokio::test]
    async fn applying_color_only_preserves_width_and_has_idempotent_durable_receipt() {
        let (_temp, app, id, scan) = fixture();
        let before = app.store.carousel.get().unwrap();
        let request = || Apply {
            revision: before.revision,
            selections: vec![Selection {
                slot: 2,
                width: false,
                color: true,
            }],
        };
        let result = apply(
            State(app.clone()),
            Path((id.clone(), scan.clone())),
            headers(),
            Json(request()),
        )
        .await;
        assert!(result.is_ok());
        let after = app.store.carousel.get().unwrap();
        assert_eq!(after.slots[&2].width_mm, None);
        assert_eq!(after.slots[&2].color, before.slots[&2].color);
        assert_eq!(after.slots[&2].measured_color.as_deref(), Some("#182ca1"));
        assert_eq!(after.slots[&2].speed_cm_s, before.slots[&2].speed_cm_s);
        assert_eq!(after.slots[&1], before.slots[&1]);
        assert!(
            scan_path(&app, &id, &scan)
                .unwrap()
                .join("applied.json")
                .exists()
        );
        // Simulate interruption after inventory replacement but before receipt
        // publication; the prewritten journal must recover without a new edit.
        let directory = scan_path(&app, &id, &scan).unwrap();
        for entry in fs::read_dir(&directory).unwrap().flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.starts_with("apply-") && !name.starts_with("apply-request-") {
                fs::remove_file(entry.path()).unwrap();
            }
        }
        fs::remove_file(directory.join("applied.json")).unwrap();
        assert!(
            apply(
                State(app.clone()),
                Path((id.clone(), scan.clone())),
                headers(),
                Json(request())
            )
            .await
            .is_ok()
        );
        assert_eq!(app.store.carousel.get().unwrap(), after);
        // Width can be applied later from the same measurement; the prior
        // color update is recognized as ours, not as a replacement pen.
        let later = Apply {
            revision: after.revision,
            selections: vec![Selection {
                slot: 2,
                width: true,
                color: false,
            }],
        };
        assert!(
            apply(State(app.clone()), Path((id, scan)), headers(), Json(later))
                .await
                .is_ok()
        );
        let final_ink = app.store.carousel.get().unwrap().slots[&2].clone();
        assert_eq!(final_ink.width_mm, Some(0.43));
        assert_eq!(final_ink.color, after.slots[&2].color);
    }
    #[tokio::test]
    async fn changed_inventory_and_failed_scans_cannot_be_applied() {
        let (_temp, app, id, scan) = fixture();
        let mut c = app.store.carousel.get().unwrap();
        c.slots.get_mut(&2).unwrap().color = Some("#00ff00".into());
        let c = app.store.carousel.save(c).unwrap();
        let request = Apply {
            revision: c.revision,
            selections: vec![Selection {
                slot: 2,
                width: true,
                color: true,
            }],
        };
        assert!(
            apply(
                State(app.clone()),
                Path((id, scan)),
                headers(),
                Json(request)
            )
            .await
            .is_err()
        );
        assert_eq!(app.store.carousel.get().unwrap(), c);
    }
    #[test]
    fn calibration_registration_is_fixed_and_unknown_width_reserves_clearance() {
        let (_temp, app, id, _scan) = fixture();
        let c = app.store.carousel.get().unwrap();
        let registration = letter_registration().unwrap();
        let job = app
            .store
            .import_calibration(
                "target.hpgl",
                b"IN;SP2;PU0,0;PD400,400;PU;SP0;",
                Source {
                    id,
                    registration: registration.clone(),
                    carousel: c.clone(),
                },
            )
            .unwrap();
        let mut setup = Setup::new(&job);
        assert_eq!(registration.sheet_size_mm, [279.4, 215.9]);
        assert!((setup.paper_x_mm - 215.9).abs() < 1e-9);
        assert!((setup.paper_y_mm - 279.4).abs() < 1e-9);
        for (actual, expected) in setup.margins_mm.iter().zip([39., 15., 15., 15.]) {
            assert!((actual - expected).abs() < 1e-9);
        }
        assert_eq!(setup.registration, Some(registration));
        setup.validate(&job).unwrap();
        let colors = c.resolve(&mut setup, &job).unwrap();
        assert!(setup.ink_settings[&2].width_mm.is_none());
        crate::prepare::prepare_registered(
            "IN;SP2;PU0,0;PD400,400;PU;SP0;",
            40.,
            setup.profile(None).unwrap(),
            setup.rotation,
            setup.pens.clone(),
            setup.pen_map.clone(),
            setup.ink_settings.clone(),
            colors,
            true,
            Some(5.),
        )
        .unwrap();
        setup.pen_map.insert(2, 5);
        assert!(setup.validate(&job).is_err());
        setup = Setup::new(&job);
        setup.rotation = 0;
        assert!(setup.validate(&job).is_err());
        setup = Setup::new(&job);
        let mut changed = c;
        changed.slots.get_mut(&2).unwrap().force = 5;
        assert!(changed.resolve(&mut setup, &job).is_err());
    }
}
