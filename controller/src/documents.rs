//! Durable PDF originals and immutable review conversions. No plotter access.
use crate::store::{MAX_BYTES, Store};
use anyhow::{Context, Result, ensure};
use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Multipart, Path, Query, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::get,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs::{self, File},
    io::Write,
    path::{Path as FsPath, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::sync::Semaphore;
use uuid::Uuid;

// Detailed maps carry one explicit ink binding per PDF feature layer. Seattle
// already needs 2.5 MiB, independent of its small, fixed pen palette.
const MAX_INTENT_BYTES: usize = 32 * 1024 * 1024;
const REQUEST_ENVELOPE_BYTES: usize = 64 * 1024;

#[derive(Clone)]
struct App {
    store: Store,
    project: PathBuf,
    work: Arc<Semaphore>,
    publication: Arc<Mutex<()>>,
    uploads: Arc<Mutex<()>>,
    scheduled: Arc<Mutex<HashSet<String>>>,
}
struct ScheduledWork(Arc<Mutex<HashSet<String>>>, String);
impl Drop for ScheduledWork {
    fn drop(&mut self) {
        self.0.lock().unwrap().remove(&self.1);
    }
}
#[derive(Debug)]
struct Error(StatusCode, String);
impl IntoResponse for Error {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":self.1}))).into_response()
    }
}
fn bad(error: impl std::fmt::Display) -> Error {
    Error(StatusCode::UNPROCESSABLE_ENTITY, error.to_string())
}
fn storage_error(error: impl std::fmt::Display) -> Error {
    Error(StatusCode::INTERNAL_SERVER_ERROR, error.to_string())
}
fn write_request(headers: &HeaderMap) -> Result<(), Error> {
    if headers
        .get("x-plotline-request")
        .and_then(|h| h.to_str().ok())
        != Some("1")
    {
        return Err(Error(
            StatusCode::FORBIDDEN,
            "Missing X-Plotline-Request: 1".into(),
        ));
    }
    Ok(())
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}
fn digest(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}
fn write(path: &FsPath, bytes: &[u8]) -> Result<()> {
    let temp = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    let mut file = File::create(&temp)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    fs::rename(&temp, path)?;
    File::open(path.parent().unwrap())?.sync_all()?;
    Ok(())
}
fn save(path: &FsPath, value: &Value) -> Result<()> {
    write(path, &serde_json::to_vec_pretty(value)?)
}
fn read(path: &FsPath) -> Result<Value> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}

fn draft_path(app: &App, id: &str) -> Result<PathBuf> {
    Ok(id_path(&app.store.root, "documents", id)?.join("draft.json"))
}
fn draft(app: &App, id: &str) -> Result<Value> {
    let path = draft_path(app, id)?;
    Ok(if path.exists() {
        read(&path)?
    } else {
        json!({"status":"none","jobs":[]})
    })
}
fn default_intent(app: &App, document: &Value) -> Result<Value> {
    let carousel = app.store.carousel.get()?;
    let inks: Vec<Value>=carousel.slots.iter().filter(|(_,s)|s.usable() && s.width_mm.is_some() && s.color.as_deref()!=Some("#ffffff"))
        .map(|(slot,s)|json!({"id":format!("inventory-{}-{slot}",carousel.revision),"label":if s.label.is_empty(){s.color.as_deref().unwrap_or("Pen")}else{&s.label},"color_srgb":s.color,"measured_color_srgb":s.measured_color,"width_mm":s.width_mm,
            "source":{"kind":"plotline-carousel","revision":carousel.revision,"slot":slot}})).collect();
    ensure!(
        !inks.is_empty(),
        "Set a diameter for at least one loaded pen, then choose the palette in PDF studio and Update plot"
    );
    let mut pages = vec![];
    for page in document["inspection"]["pages"]
        .as_array()
        .context("Missing inspected PDF pages")?
    {
        let size = serde_json::from_value(page["source_size_mm"].clone())?;
        pages.push(json!({"page_index":page["page_index"],"source_size_mm":size,
            "placement":{"scale":1,"rotation_deg":0,"offset_mm":[0,0]},"target_media":crate::media::review_profile(size)?}));
    }
    Ok(
        json!({"schema":"plotline.print-intent/1","source_sha256":document["source_sha256"],"title":document["name"],"pages":pages,
        "palette":{"schema":"plotline.palette/1","id":"loaded-pens","label":"Loaded pens with known diameters","inks":inks},
        "policies":{"fill":{"mode":"crosshatch","spacing_mm":2,"angle_deg":45},"stroke_width_tolerance":0.75,"thin_detail_thickening":0.75,"stroke_width_mode":"preserve",
            "raster":"placeholder","gradients":"omit","transparency":"ignore-alpha","blend_modes":"normal","soft_masks":"reject"}}),
    )
}

// Serialize publication requests, including validation, so an automatic default
// can never replace settings the operator already submitted.
fn request_conversion(app: &App, id: &str, intent: Value, publish: bool) -> Result<Value> {
    let _guard = app.publication.lock().unwrap();
    request_conversion_unlocked(app, id, intent, publish)
}
fn start_default(app: &App, record: &Value) -> Result<Option<Value>> {
    let _guard = app.publication.lock().unwrap();
    let id = record["id"].as_str().context("Missing document")?;
    if draft(app, id)?["status"] != "none" {
        return Ok(None);
    }
    let result = default_intent(app, record)
        .and_then(|intent| request_conversion_unlocked(app, id, intent, true));
    match result {
        Ok(row) => Ok(Some(row)),
        Err(error) => {
            save(
                &draft_path(app, id)?,
                &json!({"status":"error","error":error.to_string(),"jobs":[]}),
            )?;
            Err(error)
        }
    }
}

fn request_conversion_unlocked(app: &App, id: &str, intent: Value, publish: bool) -> Result<Value> {
    let document = id_path(&app.store.root, "documents", id)?;
    ensure!(
        read(&document.join("record.json"))?["status"] == "ready",
        "Document inspection must finish before converting"
    );
    fs::create_dir_all(app.store.root.join("conversions"))?;
    let cid = Uuid::new_v4().to_string();
    let stage = app
        .store
        .root
        .join("conversions")
        .join(format!(".request-{cid}"));
    fs::create_dir(&stage)?;
    let result = (|| -> Result<Value> {
        save(&stage.join("intent.json"), &intent)?;
        // A complete document palette is independent of today's carousel.
        // Physical assignment belongs to draft/plot preparation below.
        worker(
            app,
            "validate",
            &document.join("source.pdf"),
            &stage,
            &[("--intent", &stage.join("intent.json"))],
        )?;
        let effective = read(&stage.join("effective-intent.json"))?;
        let row = json!({"id":cid,"document_id":id,"status":"queued","created_at":now(),"source_sha256":effective["source_sha256"],
            "settings_sha256":digest(&serde_json::to_vec(&effective)?),"converter_sha256":fingerprint(app)?,"intent":effective,"publish":publish,
            "status_url":format!("/api/v1/conversions/{cid}"),"review_url":format!("/pdf?document={id}&conversion={cid}")});
        save(&stage.join("record.json"), &row)?;
        fs::rename(&stage, id_path(&app.store.root, "conversions", &cid)?)?;
        File::open(stage.parent().unwrap())?.sync_all()?;
        if publish {
            let mut d = draft(app, id)?;
            d["requested_conversion_id"] = json!(cid);
            d["status"] = json!("queued");
            d["error"] = Value::Null;
            save(&draft_path(app, id)?, &d)?;
        }
        Ok(row)
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(stage);
    }
    result
}

fn publish_conversion(app: &App, cid: &str, explicit: bool) -> Result<Value> {
    let _guard = app.publication.lock().unwrap();
    let dir = id_path(&app.store.root, "conversions", cid)?;
    let conversion = read(&dir.join("record.json"))?;
    ensure!(
        conversion["status"] == "ready",
        "Conversion must finish before updating the plot"
    );
    let id = conversion["document_id"]
        .as_str()
        .context("Missing document ID")?;
    let mut d = draft(app, id)?;
    if !explicit && d["requested_conversion_id"] != cid {
        return Ok(d);
    }
    let record = read(&id_path(&app.store.root, "documents", id)?.join("record.json"))?;
    let pages = conversion["intent"]["pages"]
        .as_array()
        .context("Missing page selections")?;
    let existing = app.store.list_all()?;
    let carousel = app.store.carousel.get()?;
    let mut jobs = vec![];
    for page in pages {
        let index = page["page_index"].as_u64().context("Missing page index")? as u32;
        if conversion["result"]["pages"]
            .as_array()
            .is_some_and(|rows| {
                rows.iter()
                    .any(|p| p["page_index"] == index && p["metrics"]["paths"] == 0)
            })
        {
            jobs.push(json!({"page_index":index,"status":"empty","url":format!("/pdf?document={id}&conversion={cid}&page={index}")}));
            continue;
        }
        let job = if let Some(job) = existing.iter().find(|j| {
            j.pdf
                .as_ref()
                .is_some_and(|p| p.conversion_id == cid && p.page_index == index)
        }) {
            job.clone()
        } else {
            let registration = crate::media::Registration::for_media(&page["target_media"])?;
            let source =
                crate::storage::read_to_string(dir.join(format!("page-{index}/plot.hpgl")))?;
            let registered = crate::prepare::register_sheet(&source, &registration)?;
            let mut title: String = record["name"]
                .as_str()
                .unwrap_or("PDF document")
                .chars()
                .filter(|c| !c.is_control() && *c != '/' && *c != '\\')
                .take(100)
                .collect();
            while title.len() > 140 {
                title.pop();
            }
            let name = format!(
                "{} · page {}.hpgl",
                title.trim_end_matches(".pdf"),
                index + 1
            );
            app.store.import_pdf(
                &name,
                registered.as_bytes(),
                crate::store::PdfSource {
                    document_id: id.into(),
                    conversion_id: cid.into(),
                    page_index: index,
                    raster_preview: conversion["result"]["pages"]
                        .as_array()
                        .is_some_and(|pages| {
                            pages.iter().any(|p| {
                                p["page_index"] == index
                                    && p["raster_images"].as_array().is_some_and(|v| !v.is_empty())
                            })
                        }),
                    registration,
                },
            )?
        };
        // Only initialize a new draft's carousel assignment. Reopening/retrying
        // a published revision preserves the operator's chosen slots and speeds.
        if !app.store.path(&job.id)?.join("setup.json").exists() {
            let mut setup = crate::layout::Setup::new(&job);
            for ink in &job.summary.pens {
                let slot = carousel
                    .slots
                    .iter()
                    .find(|(_, s)| {
                        s.usable()
                            && s.matches_color(&ink.color)
                            && s.width_mm
                                .zip(ink.width_mm)
                                .is_some_and(|(a, b)| (a - b).abs() < 1e-6)
                    })
                    .map(|(n, _)| *n)
                    .unwrap_or(0);
                setup.pen_map.insert(ink.id, slot);
            }
            app.store.save_setup(&job.id, &setup)?;
        }
        jobs.push(json!({"page_index":index,"job_id":job.id,"url":format!("/hpgl#{}",job.id)}));
    }
    // A single page update preserves other pages' current drafts.
    if let Some(previous) = d["jobs"].as_array() {
        for p in previous {
            if !jobs.iter().any(|j| j["page_index"] == p["page_index"]) {
                jobs.push(p.clone());
            }
        }
    }
    jobs.sort_by_key(|p| p["page_index"].as_u64());
    d = json!({"status":"ready","requested_conversion_id":cid,"current_conversion_id":cid,"jobs":jobs,"updated_at":now()});
    save(&draft_path(app, id)?, &d)?;
    Ok(d)
}
fn id_path(root: &FsPath, kind: &str, id: &str) -> Result<PathBuf> {
    ensure!(
        Uuid::parse_str(id).is_ok_and(|value| value.to_string() == id),
        "Invalid ID"
    );
    Ok(root.join(kind).join(id))
}
fn list(root: &FsPath, kind: &str) -> Result<Vec<Value>> {
    let path = root.join(kind);
    if !path.exists() {
        return Ok(vec![]);
    }
    let mut values = vec![];
    for item in fs::read_dir(path)? {
        let entry = item?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if Uuid::parse_str(&name).is_ok() {
            values.push(read(&entry.path().join("record.json"))?);
        }
    }
    values.sort_by(|a, b| {
        b["created_at"]
            .as_u64()
            .cmp(&a["created_at"].as_u64())
            .then_with(|| b["id"].as_str().cmp(&a["id"].as_str()))
    });
    Ok(values)
}
fn sync_tree(path: &FsPath) -> Result<()> {
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        if entry.file_type()?.is_dir() {
            sync_tree(&entry.path())?;
        } else {
            File::open(entry.path())?.sync_all()?;
        }
    }
    File::open(path)?.sync_all()?;
    Ok(())
}

fn fingerprint(app: &App) -> Result<String> {
    let mut hash = Sha256::new();
    for file in [
        "target/release/plotline",
        "tools/pdf_driver.py",
        "tools/pdf_preview.py",
        "tools/pdf_inks.py",
        "tools/pdf_raster.py",
        "raster_lab/pdf_render.py",
        "raster_lab/combined.py",
        "raster_lab/engine.py",
        "raster_lab/focus.py",
        "raster_lab/study.py",
        "raster_lab/waves.py",
        "raster_lab/highlights.py",
        "raster_lab/lettering.py",
        "raster_lab/fill_join.py",
        "raster_lab/paper.py",
        "raster_lab/palette.py",
        "tools/pdf_native.py",
        "tools/print_contract.py",
        "requirements-native.txt",
        "requirements-contracts.txt",
    ] {
        hash.update(fs::read(app.project.join(file))?);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn worker(
    app: &App,
    command: &str,
    pdf: &FsPath,
    output: &FsPath,
    extra: &[(&str, &FsPath)],
) -> Result<()> {
    let log = File::create(output.join(format!("{command}.log")))?;
    let mut process = Command::new(app.project.join(".venv/bin/python"));
    process
        .arg(app.project.join("tools/pdf_driver.py"))
        .args([command, "--pdf"])
        .arg(pdf)
        .arg("--directory")
        .arg(output)
        .arg("--converter")
        .arg(app.project.join("target/release/plotline"))
        .stdin(Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log);
    for (flag, path) in extra {
        process.arg(flag).arg(path);
    }
    let mut child = process
        .spawn()
        .context("Start PDF worker (see PDF review setup in controller/README.md)")?;
    let started = Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            if status.success() {
                return Ok(());
            }
            if ["validate", "import-image", "import-svg"].contains(&command)
                && let Ok(failure) = read(&output.join("failure.json"))
                && failure["status"] == "invalid_request"
                && let Some(message) = failure["error"].as_str().filter(|s| !s.is_empty())
            {
                anyhow::bail!("{message}");
            }
            let log = fs::read_to_string(output.join(format!("{command}.log")))?;
            anyhow::bail!("{}", worker_failure(status, &log));
        }
        if started.elapsed() > Duration::from_secs(900) {
            child.kill()?;
            child.wait()?;
            anyhow::bail!("PDF worker exceeded 15 minutes; original retained")
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn worker_failure(status: std::process::ExitStatus, log: &str) -> String {
    // A killed process may leave only unrelated startup warnings in its log.
    // Preserve the actual exit reason rather than presenting those as the cause.
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(signal) = status.signal() {
            return format!(
                "PDF processing was interrupted (signal {signal}). Preview pen strokes again; your original and settings are retained."
            );
        }
    }
    // Terminal formatting is not useful in a browser error. Keep the raw log
    // on disk for diagnostics and retain real traceback/error text here.
    let mut clean = String::new();
    let mut chars = log.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\u{1b}' && chars.peek() == Some(&'[') {
            chars.next();
            for c in chars.by_ref() {
                if ('@'..='~').contains(&c) {
                    break;
                }
            }
        } else if !c.is_control() || c == '\n' || c == '\t' {
            clean.push(c);
        }
    }
    let detail: String = clean
        .chars()
        .rev()
        .take(6000)
        .collect::<String>()
        .chars()
        .rev()
        .collect();
    if detail.trim().is_empty() {
        format!(
            "PDF processing failed ({status}) without an error message. Preview pen strokes again; your original and settings are retained."
        )
    } else {
        format!("PDF processing failed ({status}): {}", detail.trim())
    }
}
async fn blocking<T: Send + 'static>(
    app: App,
    operation: impl FnOnce(App) -> Result<T> + Send + 'static,
) -> Result<T, Error> {
    tokio::task::spawn_blocking(move || {
        let _admission = crate::maintenance::lease()?;
        operation(app)
    })
    .await
    .map_err(bad)?
    .map_err(bad)
}
fn run_later(app: App, kind: &'static str, id: String) {
    let key = format!("{kind}/{id}");
    if !app.scheduled.lock().unwrap().insert(key.clone()) {
        return;
    }
    let reservation = ScheduledWork(app.scheduled.clone(), key);
    tokio::spawn(async move {
        let _reservation = reservation;
        let _admission = match crate::maintenance::wait().await {
            Ok(lease) => lease,
            Err(error) => {
                eprintln!("PDF admission lock: {error}");
                return;
            }
        };
        let Ok(_permit) = app.work.clone().acquire_owned().await else {
            return;
        };
        let reported = blocking(app, move |app| {
            let directory = id_path(&app.store.root, kind, &id)?;
            let path = directory.join("record.json");
            let mut record = read(&path)?;
            // A lookup may have observed "inspecting" just before the prior
            // worker completed. Do not restart an already terminal inspection.
            if kind == "documents"
                && !["received", "inspecting"].contains(&record["status"].as_str().unwrap_or(""))
            {
                return Ok(());
            }
            record["status"] = json!(if kind == "documents" {
                "inspecting"
            } else {
                "running"
            });
            save(&path, &record)?;
            let operation = (|| -> Result<()> {
                if kind == "documents" {
                    let original = record["original_format"].as_str();
                    let input = directory.join(original.map_or_else(
                        || "source.pdf".to_owned(),
                        |format| format!("original.{format}"),
                    ));
                    worker(
                        &app,
                        match original {
                            Some("svg") => "import-svg",
                            Some(_) => "import-image",
                            None => "inspect",
                        },
                        &input,
                        &directory,
                        &[],
                    )?;
                    if original.is_some() {
                        let pdf = directory.join("source.pdf");
                        File::open(&pdf)?.sync_all()?;
                        record["source_sha256"] = json!(digest(&fs::read(pdf)?));
                    }
                    record["inspection"] = read(&directory.join("inspection.json"))?;
                    if let Some(pages) = record["inspection"]["pages"].as_array_mut() {
                        for page in pages {
                            if let Ok(size) = serde_json::from_value(page["source_size_mm"].clone())
                                && let Ok(media) = crate::media::review_profile(size)
                            {
                                page["default_media"] = media;
                            }
                        }
                    }
                    if let Some(intent) = record["inspection"].get("intent") {
                        record["intent"] = intent.clone();
                    }
                    if let Some(error) = record["inspection"].get("intent_error") {
                        record["intent_error"] = error.clone();
                    }
                } else {
                    let source = id_path(
                        &app.store.root,
                        "documents",
                        record["document_id"]
                            .as_str()
                            .context("Missing document ID")?,
                    )?
                    .join("source.pdf");
                    ensure!(
                        fingerprint(&app)? == record["converter_sha256"],
                        "Converter changed while queued; submit a new revision"
                    );
                    worker(
                        &app,
                        "convert",
                        &source,
                        &directory,
                        &[("--intent", &directory.join("effective-intent.json"))],
                    )?;
                    ensure!(
                        fingerprint(&app)? == record["converter_sha256"],
                        "Converter changed during processing; submit a new revision"
                    );
                    record["result"] = read(&directory.join("result.json"))?;
                    if let Some(pages) = record["result"]["pages"].as_array_mut() {
                        for page in pages {
                            let index =
                                page["page_index"].as_u64().context("Missing page index")?;
                            for (key, file) in [
                                ("source", "source.svg"),
                                ("logical", "logical.svg"),
                                ("pen", "pen.svg"),
                                ("hpgl", "plot.hpgl"),
                            ] {
                                page["artifacts"][key] =
                                    json!(format!("/api/v1/conversions/{id}/pages/{index}/{file}"));
                            }
                            for (key, file) in [
                                ("summary", "summary.json"),
                                ("report", "report.json"),
                                ("detail_loss", "loss.svg"),
                            ] {
                                if directory.join(format!("page-{index}")).join(file).is_file() {
                                    page["artifacts"][key] = json!(format!(
                                        "/api/v1/conversions/{id}/pages/{index}/{file}"
                                    ));
                                }
                            }
                        }
                    }
                }
                sync_tree(&directory)?;
                Ok(())
            })();
            match operation {
                Ok(()) => record["status"] = json!("ready"),
                Err(error) => {
                    record["status"] = json!("error");
                    record["error"] = json!(error.to_string());
                    if let Ok(failure) = read(&directory.join("failure.json")) {
                        if kind == "conversions" {
                            record["status"] = failure["status"].clone();
                        }
                        record["error"] = failure["error"].clone();
                    }
                }
            }
            save(&path, &record)?;
            if kind == "documents"
                && record["status"] == "ready"
                && record["auto_draft"] == true
                && let Some(row) = start_default(&app, &record)?
            {
                run_later(
                    app.clone(),
                    "conversions",
                    row["id"].as_str().unwrap().into(),
                );
            }

            if kind == "conversions" && record["publish"] == true {
                let doc_id = record["document_id"].as_str().context("Missing document")?;
                let result = if record["status"] == "ready" {
                    publish_conversion(&app, &id, false).map(|_| ())
                } else {
                    Err(anyhow::anyhow!(
                        "{}",
                        record["error"].as_str().unwrap_or("Conversion failed")
                    ))
                };
                if let Err(error) = result {
                    let _guard = app.publication.lock().unwrap();
                    let mut d = draft(&app, doc_id)?;
                    if d["requested_conversion_id"] == id {
                        d["status"] = json!("error");
                        d["error"] = json!(error.to_string());
                        save(&draft_path(&app, doc_id)?, &d)?;
                    }
                }
            }
            Ok(())
        })
        .await;
        if let Err(error) = reported {
            eprintln!("PDF background job: {}", error.1);
        }
    });
}

pub fn recover(store: &Store) -> Result<()> {
    for kind in ["documents", "conversions"] {
        fs::create_dir_all(store.root.join(kind))?;
        for mut row in list(&store.root, kind)? {
            if ["received", "inspecting", "queued", "running"]
                .contains(&row["status"].as_str().unwrap_or(""))
            {
                if kind == "documents"
                    && (row["capture_id"].is_string() || row["idempotency_key"].is_string())
                {
                    row["status"] = json!("received");
                    save(
                        &id_path(&store.root, kind, row["id"].as_str().unwrap())?
                            .join("record.json"),
                        &row,
                    )?;
                    continue;
                }
                row["status"] = json!("error");
                row["error"] = json!(
                    "Server restarted during processing; original and settings retained. Re-upload if document inspection was interrupted, or submit a new conversion."
                );
                save(
                    &id_path(&store.root, kind, row["id"].as_str().unwrap())?.join("record.json"),
                    &row,
                )?;
                if kind == "conversions" && row["publish"] == true {
                    let path = id_path(
                        &store.root,
                        "documents",
                        row["document_id"].as_str().context("Missing document")?,
                    )?
                    .join("draft.json");
                    if path.exists() {
                        let mut d = read(&path)?;
                        if d["requested_conversion_id"] == row["id"] {
                            d["status"] = json!("error");
                            d["error"] = row["error"].clone();
                            save(&path, &d)?;
                        }
                    }
                }
            }
        }
    }
    Ok(())
}

/// Inbox is read-only to this process. Durable receipts live with the studio's
/// originals, so restarts/offline periods cannot lose a capture or import twice.
fn intake(app: &App, inbox: &FsPath) -> Result<Vec<String>> {
    fs::create_dir_all(app.store.root.join("intake"))?;
    let mut started = vec![];
    if !inbox.exists() {
        return Ok(started);
    }
    for entry in fs::read_dir(inbox)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.len() > 100 || !name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-') {
            continue;
        }
        let path = entry.path();
        let Ok(capture) = read(&path.join("capture.json")) else {
            continue;
        };
        if !matches!(
            capture["destination"].as_str(),
            Some("plotline" | "squidprint")
        ) {
            continue;
        }
        let imported = (|| -> Result<Option<String>> {
            let receipt = app.store.root.join("intake").join(format!("{name}.json"));
            let identity = if receipt.exists() {
                read(&receipt)?
            } else {
                let value = json!({"document_id":Uuid::new_v4().to_string(),"capture_id":name,"source_sha256":capture["sha256"]});
                save(&receipt, &value)?;
                value
            };
            let id = identity["document_id"]
                .as_str()
                .context("Missing intake document ID")?;
            let document = id_path(&app.store.root, "documents", id)?;
            if document.join("record.json").exists() {
                return Ok(None);
            }
            let bytes = fs::read(path.join("source.pdf"))?;
            ensure!(
                digest(&bytes) == identity["source_sha256"]
                    && capture["sha256"] == identity["source_sha256"],
                "IPP capture checksum changed: {name}"
            );
            let stage = app
                .store
                .root
                .join("documents")
                .join(format!(".intake-{id}"));
            fs::create_dir_all(&stage)?;
            write(&stage.join("source.pdf"), &bytes)?;
            save(&stage.join("print-ticket.json"), &capture)?;
            let title = capture["ipp"]["IPP_JOB_NAME"]
                .as_str()
                .unwrap_or("IPP document.pdf");
            let row = json!({"id":id,"name":title,"status":"received","created_at":now(),"source_sha256":identity["source_sha256"],"bytes":bytes.len(),
            "auto_draft":false,"capture_id":name,"print_ticket":capture["ipp"],"status_url":format!("/api/v1/documents/{id}"),"review_url":format!("/pdf?document={id}")});
            save(&stage.join("record.json"), &row)?;
            sync_tree(&stage)?;
            fs::rename(&stage, &document)?;
            File::open(document.parent().unwrap())?.sync_all()?;
            Ok(Some(id.to_owned()))
        })();
        match imported {
            Ok(Some(id)) => started.push(id),
            Ok(None) => {}
            Err(error) => eprintln!("IPP capture {name}: {error:#}"),
        }
    }
    Ok(started)
}
fn start_intake(app: App) {
    let inbox = std::env::var_os("PLOTLINE_IPP_INBOX").map(PathBuf::from);
    // Snapshot before the router accepts uploads, so a new upload cannot also
    // be scheduled by startup recovery.
    let recovered = list(&app.store.root, "documents");
    tokio::spawn(async move {
        // Only pure PDF inspection is replayed automatically. Interrupted
        // conversions retain an explicit error and can be regenerated in studio.
        if let Ok(rows) = recovered {
            let Ok(_admission) = crate::maintenance::wait().await else {
                return;
            };
            for row in rows {
                let id = row["id"].as_str().unwrap().to_owned();
                if let Ok(d) = draft(&app, &id)
                    && d["status"] == "queued"
                    && let Some(cid) = d["requested_conversion_id"].as_str()
                    && let Err(error) = publish_conversion(&app, cid, false)
                {
                    eprintln!("Recover PDF handoff {cid}: {error:#}");
                }
                if !row["capture_id"].is_string() && !row["idempotency_key"].is_string() {
                    continue;
                }
                if row["status"] == "received" {
                    run_later(app.clone(), "documents", id);
                } else if row["status"] == "ready"
                    && row["auto_draft"] == true
                    && draft(&app, &id).is_ok_and(|d| d["status"] == "none")
                {
                    let copy = app.clone();
                    let result =
                        tokio::task::spawn_blocking(move || start_default(&copy, &row)).await;
                    if let Ok(Ok(Some(r))) = result {
                        run_later(app.clone(), "conversions", r["id"].as_str().unwrap().into());
                    }
                }
            }
        }
        let Some(inbox) = inbox else { return };
        loop {
            let copy = app.clone();
            let directory = inbox.clone();
            let Ok(admission) = crate::maintenance::wait().await else {
                return;
            };
            match tokio::task::spawn_blocking(move || {
                let _admission = admission;
                intake(&copy, &directory)
            })
            .await
            {
                Ok(Ok(ids)) => {
                    for id in ids {
                        run_later(app.clone(), "documents", id);
                    }
                }
                error => eprintln!("IPP inbox: {error:?}"),
            }
            tokio::time::sleep(Duration::from_secs(2)).await;
        }
    });
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
        work: Arc::new(Semaphore::new(crate::maintenance::workers(
            "PLOTLINE_CONVERSION_WORKERS",
            2,
        ))),
        publication: Arc::new(Mutex::new(())),
        uploads: Arc::new(Mutex::new(())),
        scheduled: Arc::new(Mutex::new(HashSet::new())),
    };
    start_intake(app.clone());
    crate::storage::start(app.store.root.clone(), app.project.clone());
    Router::new()
        .route(
            "/pdf",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
                    include_str!("../web/pdf.html"),
                )
            }),
        )
        .route(
            "/pdf.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/pdf.js"),
                )
            }),
        )
        .route(
            "/pdf-pens.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/pdf-pens.js"),
                )
            }),
        )
        .route(
            "/pdf-recipe.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/pdf-recipe.js"),
                )
            }),
        )
        .route(
            "/pdf-paper.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/pdf-paper.js"),
                )
            }),
        )
        .route(
            "/pdf.css",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/css; charset=utf-8")],
                    include_str!("../web/pdf.css"),
                )
            }),
        )
        .route("/api/v1/capabilities", get(capabilities))
        .route("/api/v1/documents", get(documents).post(upload))
        .route("/api/v1/submissions/{key}", get(submission))
        .route("/api/v1/documents/{id}", get(document))
        .route("/api/v1/documents/{id}/source.pdf", get(source))
        .route("/api/v1/documents/{id}/original", get(original))
        .route(
            "/api/v1/documents/{id}/recipes",
            get(recipes).post(save_recipe).layer(DefaultBodyLimit::max(
                MAX_INTENT_BYTES + REQUEST_ENVELOPE_BYTES,
            )),
        )
        .route("/api/v1/documents/{id}/recipes/{recipe}", get(recipe))
        .route("/api/v1/documents/{id}/source-styles", get(source_styles))
        .route("/api/v1/documents/{id}/pages/{page}", get(source_preview))
        .route(
            "/api/v1/documents/{id}/conversions",
            axum::routing::post(convert).layer(DefaultBodyLimit::max(MAX_INTENT_BYTES)),
        )
        .route("/api/v1/conversions/{id}", get(conversion))
        .route(
            "/api/v1/documents/{id}/draft",
            axum::routing::post(update_draft).layer(DefaultBodyLimit::max(MAX_INTENT_BYTES)),
        )
        .route(
            "/api/v1/conversions/{id}/publish",
            axum::routing::post(publish),
        )
        .route(
            "/api/v1/conversions/{id}/pages/{page}/{artifact}",
            get(artifact),
        )
        .layer(DefaultBodyLimit::max(
            MAX_BYTES + MAX_INTENT_BYTES + REQUEST_ENVELOPE_BYTES,
        ))
        .with_state(app)
}
async fn source_styles(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, Error> {
    blocking(app, move |app| {
        let _guard = app.publication.lock().unwrap();
        let dir = id_path(&app.store.root, "documents", &id)?;
        let inspection = read(&dir.join("inspection.json"))?;
        if inspection["pages"]
            .as_array()
            .is_some_and(|pages| pages.iter().all(|p| p.get("source_styles").is_some()))
        {
            return Ok(Json(inspection["pages"].clone()));
        }
        let cached = dir.join("source-styles.json");
        if !cached.exists() {
            worker(&app, "styles", &dir.join("source.pdf"), &dir, &[])?;
        }
        Ok(Json(read(&cached)?))
    })
    .await
}
async fn capabilities(State(app): State<App>) -> Result<Json<Value>, Error> {
    blocking(app,|app| {
        let carousel=app.store.carousel.get()?;
        let inks:Vec<_>=carousel.slots.iter().filter(|(_,s)|s.usable()).map(|(slot,s)|json!({
            "id":format!("inventory-{}-{slot}",carousel.revision),"label":if s.label.is_empty(){s.color.as_deref().unwrap_or("Pen")}else{&s.label},"color_srgb":s.color,"width_mm":s.width_mm,
            "source":{"kind":"plotline-carousel","revision":carousel.revision,"slot":slot}
        })).collect();
        let profiles=crate::media::catalog()?["media_profiles"].clone();
        Ok(Json(json!({"schema":"plotline.capabilities/1","inventory_revision":carousel.revision,
            "available_inks":inks,"media_profiles":profiles,"implemented":{"pdf_upload":true,"per_ink_widths":true,"pdf_handoff":true,"pdf_submission_idempotency":true,"pen_workflow":true,"thin_detail_thickening":true,
            "raster_policies":["reject","omit","reserve","placeholder","dither"],"fill_modes":["none","outline","hatch","crosshatch","feathered"]}})))
    }).await
}
#[derive(serde::Deserialize, Default)]
struct ListOptions {
    #[serde(default)]
    summary: bool,
}
async fn documents(
    State(app): State<App>,
    Query(options): Query<ListOptions>,
) -> Result<Json<Value>, Error> {
    blocking(app, move |app| {
        let mut rows = list(&app.store.root, "documents")?;
        for row in &mut rows {
            row["draft"] = draft(&app, row["id"].as_str().unwrap())?;
            row["recipes"] = json!(recipe_list(&app, row["id"].as_str().unwrap())?);
            if options.summary {
                row.as_object_mut().unwrap().retain(|key, _| {
                    [
                        "id",
                        "name",
                        "status",
                        "created_at",
                        "draft",
                        "auto_draft",
                        "recipes",
                        "review_url",
                        "error",
                    ]
                    .contains(&key.as_str())
                });
            }
        }
        Ok(Json(json!({"documents":rows})))
    })
    .await
}

// Match exact source bytes, never a filename. Ticket dimensions are provenance,
// not permission to resize a PDF. Return all matches rather than picking an
// arbitrary ticket if identical bytes were submitted with different media.
fn matching_captures(project: &FsPath, sha256: &str) -> Result<Vec<Value>> {
    let root = project.join("state/print-sink/captures");
    if !root.exists() {
        return Ok(vec![]);
    }
    fn dimension(collection: &str, name: &str) -> Option<f64> {
        let rest = collection.split_once(&format!("{name}="))?.1;
        let digits: String = rest.chars().take_while(char::is_ascii_digit).collect();
        let value = digits.parse::<u32>().ok()?;
        (value > 0).then_some(value as f64 / 100.)
    }
    let mut matches = vec![];
    for entry in fs::read_dir(root)? {
        let path = entry?.path().join("capture.json");
        // A capture still being written must not prevent opening a document.
        let Ok(capture) = read(&path) else { continue };
        if capture["sha256"] != sha256 {
            continue;
        }
        let collection = capture["ipp"]["IPP_MEDIA_COL"].as_str().unwrap_or("");
        let requested = dimension(collection, "x-dimension")
            .zip(dimension(collection, "y-dimension"))
            .map(|(w, h)| [w, h]);
        matches.push(
            json!({"id":capture["id"], "received_at":capture["received_at"],
            "job_name":capture["ipp"]["IPP_JOB_NAME"], "requested_size_mm":requested}),
        );
    }
    matches.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
    Ok(matches)
}

async fn document(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Value>, Error> {
    blocking(app, move |app| {
        let mut value = read(&id_path(&app.store.root, "documents", &id)?.join("record.json"))?;
        value["draft"] = draft(&app, &id)?;
        value["recipes"] = json!(recipe_list(&app, &id)?);
        value["captures"] = json!(if let Some(hash) = value["source_sha256"].as_str() {
            matching_captures(&app.project, hash)?
        } else {
            Vec::new() // Image wrapper PDF has not been generated yet.
        });
        value["conversions"] = json!(
            list(&app.store.root, "conversions")?
                .into_iter()
                .filter(|c| c["document_id"] == id)
                .map(|c| conversion_status(&id_path(
                    &app.store.root,
                    "conversions",
                    c["id"].as_str().context("Missing conversion ID")?
                )?))
                .collect::<Result<Vec<_>>>()?
        );
        Ok(Json(value))
    })
    .await
}
fn submission_key(key: &str) -> Result<&str, Error> {
    if !Uuid::parse_str(key).is_ok_and(|v| v.to_string() == key) {
        return Err(Error(
            StatusCode::BAD_REQUEST,
            "Idempotency-Key must be a lowercase, hyphenated UUID".into(),
        ));
    }
    Ok(key)
}

fn submission_receipt(app: &App, key: &str) -> Result<Value, Error> {
    submission_key(key)?;
    let _guard = app.uploads.lock().unwrap();
    let path = app
        .store
        .root
        .join("documents")
        .join(key)
        .join("record.json");
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Err(Error(
                StatusCode::NOT_FOUND,
                "No committed submission for this key".into(),
            ));
        }
        Err(error) => return Err(storage_error(error)),
    };
    let row: Value = serde_json::from_slice(&bytes).map_err(storage_error)?;
    if row["idempotency_key"] != key {
        return Err(Error(
            StatusCode::NOT_FOUND,
            "No committed submission for this key".into(),
        ));
    }
    File::open(app.store.root.join("documents"))
        .and_then(|f| f.sync_all())
        .map_err(storage_error)?;
    Ok(row)
}

async fn submission(State(app): State<App>, Path(key): Path<String>) -> Result<Json<Value>, Error> {
    tokio::task::spawn_blocking(move || {
        let _admission = crate::maintenance::lease().map_err(storage_error)?;
        let row = submission_receipt(&app, &key)?;
        resume_inspection(app, &row);
        Ok(Json(row))
    })
    .await
    .map_err(storage_error)?
}

fn resume_inspection(app: App, row: &Value) {
    if row["status"] == "received" || row["status"] == "inspecting" {
        run_later(app, "documents", row["id"].as_str().unwrap().into());
    }
}

fn store_upload(
    app: &App,
    pdf: &[u8],
    intent: Option<&[u8]>,
    name: &str,
    key: Option<&str>,
) -> Result<(Value, bool), Error> {
    // The key is also the storage UUID. One atomic directory rename commits
    // both the original and its receipt: there is no separate index to tear.
    // A state directory has one owning Plotline Studios process; serialize its uploads.
    if let Some(key) = key {
        submission_key(key)?;
    }
    let source_hash = digest(pdf);
    let original = import_format(pdf, name);
    if original.is_some() && intent.is_some() {
        return Err(bad(
            "Print-intent sidecars apply to PDF uploads; choose SVG or image settings in PDF Studio",
        ));
    }
    let intent_hash = intent.map(digest);
    let _guard = app.uploads.lock().unwrap();
    let result = (|| -> Result<Result<(Value, bool), Error>> {
        let id = key
            .map(str::to_owned)
            .unwrap_or_else(|| Uuid::new_v4().to_string());
        let path = id_path(&app.store.root, "documents", &id)?;
        if path.exists() {
            let row = read(&path.join("record.json"))?;
            if key.is_some()
                && row["idempotency_key"] == key.unwrap()
                && row.get("upload_sha256").unwrap_or(&row["source_sha256"]) == &source_hash
                && row["upload_intent_sha256"] == json!(intent_hash)
            {
                // Also finish durability if a previous attempt lost its reply
                // between rename and the parent-directory fsync.
                File::open(path.parent().unwrap())?.sync_all()?;
                return Ok(Ok((row, false)));
            }
            return Ok(Err(Error(StatusCode::CONFLICT, "Idempotency-Key already belongs to a different document or intent; use the original submission or a new key for new artwork".into())));
        }
        fs::create_dir_all(path.parent().unwrap())?;
        // A crash before commit may leave a partial stage. A fresh attempt must
        // neither mistake that for acceptance nor overwrite retained input.
        let stage = path
            .parent()
            .unwrap()
            .join(format!(".upload-{id}-{}", Uuid::new_v4()));
        fs::create_dir(&stage)?;
        let mut row = json!({"id":id,"name":name,"status":"received","created_at":now(),"source_sha256":source_hash,"bytes":pdf.len(),
            "upload_intent_sha256":intent_hash,"status_url":format!("/api/v1/documents/{id}"),"review_url":format!("/pdf?document={id}")});
        if let Some(key) = key {
            row["idempotency_key"] = json!(key);
        }
        if let Some(format) = original {
            row["original_format"] = json!(format);
            row["upload_sha256"] = json!(source_hash);
            row["source_sha256"] = Value::Null; // Assigned after the wrapper PDF is ready.
            write(&stage.join(format!("original.{format}")), pdf)?;
        } else {
            write(&stage.join("source.pdf"), pdf)?;
        }
        if let Some(intent) = intent {
            write(&stage.join("intent.json"), intent)?;
        }
        save(&stage.join("record.json"), &row)?;
        sync_tree(&stage)?;
        fs::rename(&stage, &path)?;
        File::open(path.parent().unwrap())?.sync_all()?;
        Ok(Ok((row, true)))
    })();
    result.map_err(storage_error)?
}

fn import_format(bytes: &[u8], name: &str) -> Option<&'static str> {
    if bytes.starts_with(b"\xff\xd8\xff") {
        Some("jpg")
    } else if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        Some("png")
    } else if bytes.starts_with(b"%PDF-") {
        None
    } else if name.to_ascii_lowercase().ends_with(".svg")
        || bytes
            .strip_prefix(b"\xef\xbb\xbf")
            .unwrap_or(bytes)
            .trim_ascii_start()
            .starts_with(b"<")
        || bytes.starts_with(b"\xff\xfe")
        || bytes.starts_with(b"\xfe\xff")
    {
        // XML validity and resource references are checked by the SVG worker.
        Some("svg")
    } else {
        None
    }
}

async fn upload(
    State(app): State<App>,
    headers: HeaderMap,
    mut form: Multipart,
) -> Result<(StatusCode, Json<Value>), Error> {
    write_request(&headers)?;
    let keys = headers
        .get_all("idempotency-key")
        .iter()
        .collect::<Vec<_>>();
    if keys.len() > 1 {
        return Err(bad("Only one Idempotency-Key is allowed"));
    }
    let key = keys
        .first()
        .map(|v| {
            v.to_str()
                .map_err(bad)
                .and_then(submission_key)
                .map(str::to_owned)
        })
        .transpose()?;
    let mut pdf = None;
    let mut intent = None;
    let mut name = "document.pdf".to_owned();
    while let Some(field) = form.next_field().await.map_err(bad)? {
        match field.name() {
            Some("document") => {
                if pdf.is_some() {
                    return Err(bad("Only one document per upload"));
                }
                name = field
                    .file_name()
                    .unwrap_or("document.pdf")
                    .chars()
                    .filter(|c| !c.is_control())
                    .take(180)
                    .collect();
                pdf = Some(field.bytes().await.map_err(bad)?);
            }
            Some("intent") => {
                if intent.is_some() {
                    return Err(bad("Duplicate intent"));
                }
                intent = Some(field.bytes().await.map_err(bad)?);
            }
            _ => return Err(bad("Use multipart fields document and optional intent")),
        }
    }
    let pdf = pdf.ok_or_else(|| bad("Missing PDF, SVG, JPEG or PNG document"))?;
    if pdf.is_empty() || pdf.len() > MAX_BYTES {
        return Err(bad("Document must be between 1 byte and 64 MiB"));
    }
    if intent.as_ref().is_some_and(|i| i.len() > MAX_INTENT_BYTES) {
        return Err(bad("Intent exceeds 32 MiB"));
    }
    let row = tokio::task::spawn_blocking(move || {
        let _admission = crate::maintenance::lease().map_err(storage_error)?;
        let (row, _) = store_upload(&app, &pdf, intent.as_deref(), &name, key.as_deref())?;
        // Dispatch inside the blocking operation: dropping a disconnected
        // request's future cannot strand a committed upload before inspection.
        resume_inspection(app, &row);
        Ok::<_, Error>(row)
    })
    .await
    .map_err(storage_error)??;
    Ok((StatusCode::ACCEPTED, Json(row)))
}
// Saved recipes reference retained source bytes; conversion runs never save them.
fn recipe_root(app: &App, id: &str) -> Result<PathBuf> {
    let document = id_path(&app.store.root, "documents", id)?;
    ensure!(
        document.join("record.json").is_file(),
        "Unknown PDF document"
    );
    Ok(document.join("recipes"))
}
fn recipe_list(app: &App, id: &str) -> Result<Vec<Value>> {
    let root = recipe_root(app, id)?;
    if !root.exists() {
        return Ok(vec![]);
    }
    let mut rows = vec![];
    for entry in fs::read_dir(root)? {
        let file = entry?.path().join("record.json");
        if !file.is_file() {
            continue;
        }
        let mut row = read(&file)?;
        row.as_object_mut()
            .context("Invalid recipe record")?
            .remove("intent");
        rows.push(row);
    }
    rows.sort_by_key(|r| std::cmp::Reverse(r["updated_at"].as_u64().unwrap_or(0)));
    Ok(rows)
}
fn recipe_path(app: &App, id: &str, recipe: &str) -> Result<PathBuf> {
    ensure!(
        Uuid::parse_str(recipe).is_ok_and(|u| u.to_string() == recipe),
        "Invalid recipe ID"
    );
    Ok(recipe_root(app, id)?.join(recipe))
}
fn persist_recipe(app: &App, id: &str, request: &Value, intent: Value) -> Result<Value, Error> {
    let mut name = request["name"].as_str().unwrap_or("").trim().to_owned();
    if name.is_empty() || name.chars().count() > 160 {
        return Err(bad("Recipe name must contain 1–160 characters"));
    }
    if request.get("id").is_some_and(|v| !v.is_string()) {
        return Err(bad("Invalid recipe ID"));
    }
    let existing = request.get("id").and_then(Value::as_str);
    let key = existing
        .map(str::to_owned)
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    let directory = recipe_path(app, id, &key).map_err(bad)?;
    let previous = if existing.is_some() {
        Some(read(&directory.join("record.json")).map_err(bad)?)
    } else {
        None
    };
    if let Some(prior) = &previous {
        if request["expected_revision"] != prior["revision"] {
            return Err(Error(StatusCode::CONFLICT,"This recipe was saved elsewhere. Reopen it or use Save As to preserve your changes.".into()));
        }
    } else {
        let names: HashSet<_> = recipe_list(app, id)
            .map_err(bad)?
            .iter()
            .filter_map(|r| r["name"].as_str().map(str::to_lowercase))
            .collect();
        let base = name.clone();
        let mut number = 2;
        while names.contains(&name.to_lowercase()) {
            name = format!("{} ({number})", base.chars().take(150).collect::<String>());
            number += 1;
        }
    }
    let revision = previous
        .as_ref()
        .and_then(|p| p["revision"].as_u64())
        .unwrap_or(0)
        + 1;
    let created = previous
        .as_ref()
        .and_then(|p| p["created_at"].as_u64())
        .unwrap_or_else(now);
    let row = json!({"schema":"plotline.pdf-recipe/1","id":key,"document_id":id,"name":name,
        "revision":revision,"created_at":created,"updated_at":now(),"intent":intent,
        "review_url":format!("/pdf?document={id}&recipe={key}"),
        "thumbnail_url":format!("/api/v1/documents/{id}/pages/{}",intent["pages"][0]["page_index"].as_u64().unwrap_or(0))});
    fs::create_dir_all(&directory).map_err(storage_error)?;
    save(&directory.join(format!("{revision:08}.json")), &row).map_err(storage_error)?;
    save(&directory.join("record.json"), &row).map_err(storage_error)?;
    Ok(row)
}
async fn recipes(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Value>, Error> {
    blocking(app, move |app| Ok(Json(json!(recipe_list(&app, &id)?)))).await
}
async fn recipe(
    State(app): State<App>,
    Path((id, key)): Path<(String, String)>,
) -> Result<Json<Value>, Error> {
    blocking(app, move |app| {
        Ok(Json(read(
            &recipe_path(&app, &id, &key)?.join("record.json"),
        )?))
    })
    .await
}
async fn save_recipe(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(request): Json<Value>,
) -> Result<Json<Value>, Error> {
    write_request(&headers)?;
    tokio::task::spawn_blocking(move || {
        let _admission = crate::maintenance::lease().map_err(storage_error)?;
        let _guard = app.publication.lock().unwrap();
        let root = recipe_root(&app, &id).map_err(bad)?;
        let document = root.parent().unwrap();
        if read(&document.join("record.json")).map_err(bad)?["status"] != "ready" {
            return Err(bad("Wait for PDF inspection before saving its recipe"));
        }
        let stage = document.join(format!(".recipe-{}", Uuid::new_v4()));
        fs::create_dir(&stage).map_err(storage_error)?;
        let result = (|| {
            save(&stage.join("intent.json"), &request["intent"]).map_err(bad)?;
            worker(
                &app,
                "validate",
                &document.join("source.pdf"),
                &stage,
                &[("--intent", &stage.join("intent.json"))],
            )
            .map_err(bad)?;
            let intent = read(&stage.join("effective-intent.json")).map_err(bad)?;
            persist_recipe(&app, &id, &request, intent).map(Json)
        })();
        let _ = fs::remove_dir_all(stage);
        result
    })
    .await
    .map_err(storage_error)?
}

async fn convert(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(intent): Json<Value>,
) -> Result<(StatusCode, Json<Value>), Error> {
    write_request(&headers)?;
    let row = blocking(app.clone(), move |app| {
        request_conversion(&app, &id, intent, false)
    })
    .await?;
    run_later(app, "conversions", row["id"].as_str().unwrap().into());
    Ok((StatusCode::ACCEPTED, Json(row)))
}
async fn update_draft(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(intent): Json<Value>,
) -> Result<(StatusCode, Json<Value>), Error> {
    write_request(&headers)?;
    let row = blocking(app.clone(), move |app| {
        let intent = if intent.get("schema").is_some() {
            intent
        } else {
            let record = read(&id_path(&app.store.root, "documents", &id)?.join("record.json"))?;
            default_intent(&app, &record)?
        };
        request_conversion(&app, &id, intent, true)
    })
    .await?;
    run_later(app, "conversions", row["id"].as_str().unwrap().into());
    Ok((StatusCode::ACCEPTED, Json(row)))
}
async fn publish(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, Error> {
    write_request(&headers)?;
    blocking(app, move |app| {
        publish_conversion(&app, &id, true).map(Json)
    })
    .await
}
fn conversion_status(path: &FsPath) -> Result<Value> {
    let mut value = read(&path.join("record.json"))?;
    if let Some(pages) = value
        .get_mut("result")
        .and_then(|r| r.get_mut("pages"))
        .and_then(Value::as_array_mut)
    {
        for page in pages {
            let directory = path.join(format!("page-{}", page["page_index"]));
            page["preview_available"] = json!(
                ["source.svg", "logical.svg", "pen.svg"]
                    .iter()
                    .all(|file| crate::storage::exists(&directory.join(file)))
            );
            if !crate::storage::exists(&directory.join("report.json")) {
                page["artifacts"]
                    .as_object_mut()
                    .map(|a| a.remove("report"));
            }
            if crate::storage::exists(&directory.join("summary.json")) {
                page["artifacts"]["summary"] = json!(format!(
                    "/api/v1/conversions/{}/pages/{}/summary.json",
                    path.file_name().unwrap().to_string_lossy(),
                    page["page_index"]
                ));
            }
        }
    }
    if matches!(value["status"].as_str(), Some("queued" | "running")) {
        value["elapsed_seconds"] =
            json!(now().saturating_sub(value["created_at"].as_u64().unwrap_or_else(now)));
        // Progress is advisory; a missing/partial/bad snapshot cannot hide job status.
        let progress_path = path.join("progress.json");
        if fs::metadata(&progress_path).is_ok_and(|m| m.len() <= 16_384)
            && let Ok(progress) = read(&progress_path)
            && progress["version"] == 1
            && progress["label"].is_string()
        {
            value["progress"] = progress;
        }
    }
    Ok(value)
}
async fn conversion(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Value>, Error> {
    blocking(app, move |app| {
        Ok(Json(conversion_status(&id_path(
            &app.store.root,
            "conversions",
            &id,
        )?)?))
    })
    .await
}
async fn source(State(app): State<App>, Path(id): Path<String>) -> Result<Response, Error> {
    blocking(app, move |app| {
        Ok((
            [
                (header::CONTENT_TYPE, "application/pdf"),
                (
                    header::CONTENT_DISPOSITION,
                    "attachment; filename=source.pdf",
                ),
            ],
            fs::read(id_path(&app.store.root, "documents", &id)?.join("source.pdf"))?,
        )
            .into_response())
    })
    .await
}
async fn original(State(app): State<App>, Path(id): Path<String>) -> Result<Response, Error> {
    blocking(app, move |app| {
        let directory = id_path(&app.store.root, "documents", &id)?;
        let record = read(&directory.join("record.json"))?;
        let (file, mime, disposition) = match record["original_format"].as_str() {
            Some("svg") => (
                "original.svg",
                "image/svg+xml",
                "attachment; filename=original.svg",
            ),
            Some("jpg") => (
                "original.jpg",
                "image/jpeg",
                "attachment; filename=original.jpg",
            ),
            Some("png") => (
                "original.png",
                "image/png",
                "attachment; filename=original.png",
            ),
            _ => (
                "source.pdf",
                "application/pdf",
                "attachment; filename=source.pdf",
            ),
        };
        Ok((
            [
                (header::CONTENT_TYPE, mime),
                (header::CONTENT_DISPOSITION, disposition),
            ],
            fs::read(directory.join(file))?,
        )
            .into_response())
    })
    .await
}

async fn source_preview(
    State(app): State<App>,
    Path((id, page)): Path<(String, u32)>,
) -> Result<Response, Error> {
    blocking(app, move |app| {
        let directory = id_path(&app.store.root, "documents", &id)?;
        let record = read(&directory.join("record.json"))?;
        ensure!(
            record["inspection"]["pages"]
                .as_array()
                .is_some_and(|pages| pages.iter().any(|p| p["page_index"] == page)),
            "Unknown PDF page"
        );
        crate::storage::touch(&directory);
        // Versioned derived previews replace the old 108-dpi thumbnails lazily.
        // Keep this version in sync with tools/pdf_preview.py.
        let file = directory.join(format!("page-{page}-v2.png"));
        let bytes = if file.is_file() {
            fs::read(file)?
        } else {
            // Re-render just this thumbnail, without rewriting inspection/history.
            let output = std::process::Command::new(app.project.join(".venv/bin/python"))
                .arg(app.project.join("controller/scripts/render-page.py"))
                .arg(directory.join("source.pdf"))
                .arg(page.to_string())
                .output()?;
            ensure!(
                output.status.success(),
                "PDF thumbnail: {}",
                String::from_utf8_lossy(&output.stderr)
            );
            write(&file, &output.stdout)?;
            output.stdout
        };
        Ok(([(header::CONTENT_TYPE, "image/png")], bytes).into_response())
    })
    .await
}
async fn artifact(
    State(app): State<App>,
    Path((id, page, name)): Path<(String, u32, String)>,
) -> Result<Response, Error> {
    let mime = match name.as_str() {
        "source.svg" | "logical.svg" | "pen.svg" | "loss.svg" => "image/svg+xml",
        "report.json" | "summary.json" => "application/json",
        "plot.hpgl" => "application/vnd.hp-hpgl",
        _ => return Err(Error(StatusCode::NOT_FOUND, "Unknown artifact".into())),
    };
    blocking(app, move |app| {
        let path = id_path(&app.store.root, "conversions", &id)?;
        ensure!(
            read(&path.join("record.json"))?["status"] == "ready",
            "Conversion is not ready"
        );
        let file = path.join(format!("page-{page}")).join(name);
        crate::storage::touch(&path);
        if !crate::storage::exists(&file) {
            return Ok(Error(
                if mime == "image/svg+xml" {
                    StatusCode::GONE
                } else {
                    StatusCode::NOT_FOUND
                },
                "Artifact not retained; rebuild previews from the saved PDF and settings".into(),
            )
            .into_response());
        }
        Ok(([(header::CONTENT_TYPE, mime)], crate::storage::read(file)?).into_response())
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn svg_upload_retains_original_and_replays_after_normalization() {
        let root = tempfile::tempdir().unwrap();
        let app = app_at(root.path());
        let key = Uuid::new_v4().to_string();
        let bytes =
            b"\xef\xbb\xbf <?xml version=\"1.0\"?><svg xmlns=\"http://www.w3.org/2000/svg\"/>";
        let (mut row, created) = store_upload(&app, bytes, None, "art.SVG", Some(&key)).unwrap();
        assert!(created);
        assert_eq!(row["original_format"], "svg");
        assert!(row["source_sha256"].is_null());
        let path = id_path(&app.store.root, "documents", &key).unwrap();
        assert_eq!(fs::read(path.join("original.svg")).unwrap(), bytes);
        row["source_sha256"] = json!(digest(b"normalized PDF"));
        row["status"] = json!("ready");
        save(&path.join("record.json"), &row).unwrap();
        let (replayed, created) =
            store_upload(&app, bytes, None, "renamed.svg", Some(&key)).unwrap();
        assert!(!created);
        assert_eq!(row, replayed);
        assert_eq!(
            store_upload(&app, b"<svg/>", None, "other.svg", Some(&key))
                .unwrap_err()
                .0,
            StatusCode::CONFLICT
        );
        assert!(store_upload(&app, bytes, Some(b"{}"), "art.svg", None).is_err());
        assert_eq!(import_format(b"%PDF-1.7", "misnamed.svg"), None);
        assert_eq!(
            import_format(b"\x89PNG\r\n\x1a\n", "misnamed.svg"),
            Some("png")
        );
        assert_eq!(
            import_format(b"\n<!-- comment --><svg/>", "art"),
            Some("svg")
        );
    }

    #[test]
    fn image_upload_retains_original_and_replays_after_pdf_normalization() {
        let root = tempfile::tempdir().unwrap();
        let app = app_at(root.path());
        let key = Uuid::new_v4().to_string();
        let bytes = b"\x89PNG\r\n\x1a\nretained pixels";
        let (mut row, created) = store_upload(&app, bytes, None, "art.png", Some(&key)).unwrap();
        assert!(created);
        assert!(row["source_sha256"].is_null());
        let path = id_path(&app.store.root, "documents", &key).unwrap();
        assert_eq!(fs::read(path.join("original.png")).unwrap(), bytes);
        assert!(!path.join("source.pdf").exists());
        row["source_sha256"] = json!(digest(b"normalized PDF"));
        row["status"] = json!("ready");
        save(&path.join("record.json"), &row).unwrap();
        let (replayed, created) =
            store_upload(&app, bytes, None, "new name.png", Some(&key)).unwrap();
        assert!(!created);
        assert_eq!(row, replayed);
        assert_eq!(
            store_upload(&app, b"\x89PNG\r\n\x1a\nnew", None, "art.png", Some(&key))
                .unwrap_err()
                .0,
            StatusCode::CONFLICT
        );
        assert_eq!(
            store_upload(&app, bytes, Some(b"{}"), "art.png", None)
                .unwrap_err()
                .0,
            StatusCode::UNPROCESSABLE_ENTITY
        );
    }

    #[test]
    #[cfg(unix)]
    fn worker_failure_reports_termination_instead_of_startup_warning() {
        use std::os::unix::process::ExitStatusExt;
        let warning =
            "\u{1b}[0;93m[W:onnxruntime:Default] Failed to persist telemetry device ID\u{1b}[m\n";
        let message = worker_failure(std::process::ExitStatus::from_raw(15), warning);
        assert!(message.contains("interrupted (signal 15)"));
        assert!(message.contains("Preview pen strokes again"));
        assert!(!message.contains("telemetry"));
        let message = worker_failure(
            std::process::ExitStatus::from_raw(1 << 8),
            "\u{1b}[31mValueError: invalid image\u{1b}[m\n",
        );
        assert!(message.contains("exit status: 1"));
        assert!(message.contains("ValueError: invalid image"));
        assert!(!message.contains('\u{1b}'));
        assert!(
            worker_failure(std::process::ExitStatus::from_raw(1 << 8), "")
                .contains("without an error message")
        );
    }

    fn app_at(root: &FsPath) -> App {
        App {
            store: Store::open(root.into()).unwrap(),
            project: root.into(),
            work: Arc::new(Semaphore::new(2)),
            publication: Arc::new(Mutex::new(())),
            uploads: Arc::new(Mutex::new(())),
            scheduled: Arc::new(Mutex::new(HashSet::new())),
        }
    }
    #[test]
    fn pending_conversion_status_does_not_invent_result_pages() {
        let temp = tempfile::tempdir().unwrap();
        for status in ["queued", "running", "error"] {
            let row = json!({"status":status,"created_at":now()});
            save(&temp.path().join("record.json"), &row).unwrap();
            let actual = conversion_status(temp.path()).unwrap();
            assert!(actual.get("result").is_none());
            assert_eq!(read(&temp.path().join("record.json")).unwrap(), row);
        }
    }

    #[test]
    fn saved_recipes_share_original_and_reject_stale_updates() {
        let temp = tempfile::tempdir().unwrap();
        let app = app_at(temp.path());
        let (source, _) = store_upload(&app, b"original PDF", None, "map.pdf", None).unwrap();
        let id = source["id"].as_str().unwrap();
        let first = persist_recipe(
            &app,
            id,
            &json!({"name":"Map"}),
            json!({"pages":[{"page_index":0}],"choice":1}),
        )
        .unwrap();
        let key = first["id"].as_str().unwrap();
        let second = persist_recipe(
            &app,
            id,
            &json!({"name":"Map","id":key,"expected_revision":1}),
            json!({"pages":[{"page_index":1}],"choice":2}),
        )
        .unwrap();
        assert_eq!(second["revision"], 2);
        for expected in [Value::Null, json!(1)] {
            assert_eq!(
                persist_recipe(
                    &app,
                    id,
                    &json!({"name":"stale","id":key,"expected_revision":expected}),
                    json!({})
                )
                .unwrap_err()
                .0,
                StatusCode::CONFLICT
            );
        }
        assert_eq!(
            read(&recipe_path(&app, id, key).unwrap().join("record.json")).unwrap(),
            second
        );
        assert_eq!(
            read(&recipe_path(&app, id, key).unwrap().join("00000001.json")).unwrap(),
            first
        );
        let copy =
            persist_recipe(&app, id, &json!({"name":"Map"}), first["intent"].clone()).unwrap();
        assert_ne!(copy["id"], first["id"]);
        assert_eq!(copy["name"], "Map (2)");
        assert_eq!(copy["revision"], 1);
        assert_eq!(recipe_list(&app, id).unwrap().len(), 2);
        assert!(
            recipe_list(&app, id)
                .unwrap()
                .iter()
                .all(|row| row.get("intent").is_none())
        );
        for bad_request in [
            json!({"name":""}),
            json!({"name":"Map","id":"../escape"}),
            json!({"name":"Map","id":5}),
        ] {
            assert!(persist_recipe(&app, id, &bad_request, json!({})).is_err());
        }
        assert_eq!(
            fs::read(
                id_path(temp.path(), "documents", id)
                    .unwrap()
                    .join("source.pdf")
            )
            .unwrap(),
            b"original PDF"
        );
        assert_eq!(list(temp.path(), "documents").unwrap().len(), 1);
        // Reopening the store finds both saved recipes, independently of conversions.
        assert_eq!(recipe_list(&app_at(temp.path()), id).unwrap().len(), 2);
    }

    #[test]
    fn keyed_upload_replays_exact_bytes_and_rejects_reuse() {
        let temp = tempfile::tempdir().unwrap();
        let app = app_at(temp.path());
        let key = Uuid::new_v4().to_string();
        assert_eq!(
            submission_receipt(&app, &key).unwrap_err().0,
            StatusCode::NOT_FOUND
        );
        let (first, created) =
            store_upload(&app, b"pdf", Some(b"{}"), "original.pdf", Some(&key)).unwrap();
        assert!(created);
        let (replay, created) =
            store_upload(&app, b"pdf", Some(b"{}"), "renamed.pdf", Some(&key)).unwrap();
        assert!(!created);
        assert_eq!(first, replay);
        assert_eq!(submission_receipt(&app, &key).unwrap(), first);
        for (pdf, intent) in [
            (b"other".as_slice(), Some(b"{}".as_slice())),
            (b"pdf", Some(b"{ }")),
            (b"pdf", None),
        ] {
            assert_eq!(
                store_upload(&app, pdf, intent, "new.pdf", Some(&key))
                    .unwrap_err()
                    .0,
                StatusCode::CONFLICT
            );
        }
        let path = id_path(temp.path(), "documents", first["id"].as_str().unwrap()).unwrap();
        assert_eq!(fs::read(path.join("source.pdf")).unwrap(), b"pdf");
        assert_eq!(fs::read(path.join("intent.json")).unwrap(), b"{}");
        assert_eq!(list(temp.path(), "documents").unwrap().len(), 1);
        assert_eq!(
            submission_receipt(&app, "../escape").unwrap_err().0,
            StatusCode::BAD_REQUEST
        );
    }

    #[test]
    fn concurrent_uploads_commit_one_original_and_unkeyed_uploads_remain_independent() {
        let temp = tempfile::tempdir().unwrap();
        let app = app_at(temp.path());
        let key = Uuid::new_v4().to_string();
        let handles: Vec<_> = (0..8)
            .map(|_| {
                let app = app.clone();
                let key = key.clone();
                std::thread::spawn(move || {
                    store_upload(&app, b"pdf", None, "map.pdf", Some(&key)).unwrap()
                })
            })
            .collect();
        let results: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
        assert_eq!(results.iter().filter(|(_, created)| *created).count(), 1);
        assert!(
            results
                .iter()
                .all(|(row, _)| row["id"] == results[0].0["id"])
        );
        let (one, _) = store_upload(&app, b"pdf", None, "map.pdf", None).unwrap();
        let (two, _) = store_upload(&app, b"pdf", None, "map.pdf", None).unwrap();
        assert_ne!(one["id"], two["id"]);
        // A key cannot take ownership of a previous unkeyed upload.
        assert_eq!(
            store_upload(&app, b"pdf", None, "map.pdf", one["id"].as_str())
                .unwrap_err()
                .0,
            StatusCode::CONFLICT
        );
        assert_eq!(
            submission_receipt(&app, one["id"].as_str().unwrap())
                .unwrap_err()
                .0,
            StatusCode::NOT_FOUND
        );
    }

    #[test]
    fn keyed_upload_survives_crashes_before_and_after_commit() {
        let temp = tempfile::tempdir().unwrap();
        let app = app_at(temp.path());
        let key = Uuid::new_v4().to_string();
        let stage = temp
            .path()
            .join("documents")
            .join(format!(".upload-{key}-interrupted"));
        fs::create_dir_all(&stage).unwrap();
        write(&stage.join("source.pdf"), b"partial input").unwrap();
        assert_eq!(
            submission_receipt(&app, &key).unwrap_err().0,
            StatusCode::NOT_FOUND
        );
        let (mut row, _) =
            store_upload(&app, b"complete pdf", Some(b"{}"), "map.pdf", Some(&key)).unwrap();
        let path = id_path(temp.path(), "documents", row["id"].as_str().unwrap()).unwrap();
        row["status"] = json!("inspecting");
        save(&path.join("record.json"), &row).unwrap();
        recover(&app.store).unwrap();
        let reopened = app_at(temp.path());
        let (replay, created) = store_upload(
            &reopened,
            b"complete pdf",
            Some(b"{}"),
            "map.pdf",
            Some(&key),
        )
        .unwrap();
        assert!(!created);
        assert_eq!(replay["status"], "received"); // Startup replays pure inspection.
        assert_eq!(replay["id"], row["id"]);
        assert_eq!(list(temp.path(), "documents").unwrap().len(), 1);
        assert_eq!(
            fs::read(stage.join("source.pdf")).unwrap(),
            b"partial input"
        );
    }

    #[test]
    fn unreadable_receipt_is_not_mistaken_for_an_absent_submission() {
        let temp = tempfile::tempdir().unwrap();
        let app = app_at(temp.path());
        let key = Uuid::new_v4().to_string();
        let path = id_path(temp.path(), "documents", &key).unwrap();
        fs::create_dir_all(&path).unwrap();
        write(&path.join("record.json"), b"{").unwrap();
        assert_eq!(
            submission_receipt(&app, &key).unwrap_err().0,
            StatusCode::INTERNAL_SERVER_ERROR
        );
        assert_eq!(
            store_upload(&app, b"pdf", None, "map.pdf", Some(&key))
                .unwrap_err()
                .0,
            StatusCode::INTERNAL_SERVER_ERROR
        );
    }
    #[test]
    fn progress_is_advisory_and_terminal_status_wins() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path();
        let record = json!({"status":"running","created_at":now()-7});
        save(&path.join("record.json"), &record).unwrap();
        assert!(conversion_status(path).unwrap()["progress"].is_null());
        fs::write(path.join("progress.json"), b"{").unwrap();
        assert_eq!(conversion_status(path).unwrap()["status"], "running");
        save(
            &path.join("progress.json"),
            &json!({"version":1,"label":"Resolving paint visibility","completed":7,"total":100}),
        )
        .unwrap();
        let status = conversion_status(path).unwrap();
        assert_eq!(status["progress"]["completed"], 7);
        assert!(status["elapsed_seconds"].as_u64().unwrap() >= 7);
        for state in ["ready", "error", "unsupported"] {
            save(&path.join("record.json"), &json!({"status":state})).unwrap();
            assert!(conversion_status(path).unwrap()["progress"].is_null());
        }
    }
    #[test]
    fn default_palette_uses_full_carousel_and_skips_unusable_slots() {
        let temp = tempfile::tempdir().unwrap();
        let app = app_at(temp.path());
        let document =
            json!({"inspection":{"pages":[{"page_index":0,"source_size_mm":[215.9,279.4]}]}});
        let mut inventory = app.store.carousel.get().unwrap();
        for (slot, pen) in &mut inventory.slots {
            pen.state = "loaded".into();
            pen.color = Some("#000000".into());
            pen.width_mm = Some(f64::from(*slot) / 10.);
        }
        inventory = app.store.carousel.save(inventory).unwrap();
        let intent = default_intent(&app, &document).unwrap();
        assert_eq!(intent["policies"]["thin_detail_thickening"], 0.75);
        assert_eq!(intent["policies"]["stroke_width_mode"], "preserve");
        let palette = intent["palette"]["inks"].clone();
        assert_eq!(palette.as_array().unwrap().len(), 8);
        assert_eq!(palette[7]["source"]["slot"], 8);
        assert_eq!(palette[7]["width_mm"], 0.8);
        // The damaged carousel still offers all seven working positions.
        inventory.slots.get_mut(&3).unwrap().state = "unavailable".into();
        inventory = app.store.carousel.save(inventory).unwrap();
        let palette = default_intent(&app, &document).unwrap()["palette"]["inks"].clone();
        assert_eq!(palette.as_array().unwrap().len(), 7);
        assert!(
            palette
                .as_array()
                .unwrap()
                .iter()
                .all(|ink| ink["source"]["slot"] != 3)
        );
        // Empty positions and unmeasured pens cannot supply automatic geometry.
        inventory.slots.get_mut(&1).unwrap().state = "empty".into();
        inventory.slots.get_mut(&5).unwrap().width_mm = None;
        app.store.carousel.save(inventory).unwrap();
        let palette = default_intent(&app, &document).unwrap()["palette"]["inks"].clone();
        let slots: Vec<_> = palette
            .as_array()
            .unwrap()
            .iter()
            .map(|ink| ink["source"]["slot"].as_u64().unwrap())
            .collect();
        assert_eq!(slots, vec![2, 4, 6, 7, 8]);
    }
    #[test]
    fn draft_replacement_is_atomic_immutable_and_latest_request_wins() {
        let temp = tempfile::tempdir().unwrap();
        let app = app_at(temp.path());
        let id = Uuid::new_v4().to_string();
        let dir = id_path(temp.path(), "documents", &id).unwrap();
        fs::create_dir_all(&dir).unwrap();
        let document = json!({"id":id,"status":"ready","name":"ruler.pdf"});
        save(&dir.join("record.json"), &document).unwrap();
        let old = Uuid::new_v4().to_string();
        let new = Uuid::new_v4().to_string();
        let bad = Uuid::new_v4().to_string();
        for cid in [&old, &new, &bad] {
            let path = id_path(temp.path(), "conversions", cid).unwrap();
            fs::create_dir_all(path.join("page-0")).unwrap();
            save(&path.join("record.json"),&json!({"id":cid,"document_id":id,"status":"ready",
                "intent":{"pages":[{"page_index":0,"target_media":crate::media::profile([431.8,279.4]).unwrap()}]}})).unwrap();
            if cid != &bad {
                write(&path.join("page-0/plot.hpgl"),b"IN;CO\"pen id=1 r=0 g=0 b=0 width_mm=0.3\";SP1;PU2400,9776;PD6400,9776;PU;SP0;").unwrap();
            }
        }
        save(
            &draft_path(&app, &id).unwrap(),
            &json!({"status":"queued","requested_conversion_id":new,"jobs":[]}),
        )
        .unwrap();
        assert_eq!(
            publish_conversion(&app, &old, false).unwrap()["status"],
            "queued"
        );
        assert!(
            app.store.list_all().unwrap().is_empty(),
            "Old completion must not create a job"
        );
        let first = publish_conversion(&app, &new, false).unwrap();
        assert_eq!(app.store.list().unwrap().len(), 1);
        assert_eq!(
            publish_conversion(&app, &new, true).unwrap()["jobs"],
            first["jobs"],
            "Idempotent retry"
        );
        // The successful retry may cross a wall-clock second and refresh its
        // timestamp. A subsequent failure must preserve that latest receipt.
        let before_failure = draft(&app, &id).unwrap();
        assert!(publish_conversion(&app, &bad, true).is_err());
        assert_eq!(
            draft(&app, &id).unwrap(),
            before_failure,
            "Failed replacement keeps the current draft"
        );
        assert!(
            start_default(&app, &document).unwrap().is_none(),
            "Automatic defaults cannot override an operator request"
        );
        let second = publish_conversion(&app, &old, true).unwrap();
        assert_ne!(second["jobs"], first["jobs"]);
        assert_eq!(app.store.list().unwrap().len(), 1);
        assert_eq!(
            app.store.list_all().unwrap().len(),
            2,
            "Superseded immutable jobs retained"
        );
        let earlier = app
            .store
            .get(first["jobs"][0]["job_id"].as_str().unwrap())
            .unwrap();
        assert!(!app.store.is_current(&earlier).unwrap());
    }
    #[test]
    fn intake_retains_exact_bytes_and_bad_capture_does_not_block_good_capture() {
        let temp = tempfile::tempdir().unwrap();
        let app = app_at(&temp.path().join("store"));
        fs::create_dir_all(app.store.root.join("documents")).unwrap();
        let inbox = temp.path().join("inbox");
        for (name, hash) in [
            ("bad", String::from("wrong")),
            ("good", digest(b"exact PDF")),
        ] {
            let dir = inbox.join(name);
            fs::create_dir_all(&dir).unwrap();
            write(&dir.join("source.pdf"), b"exact PDF").unwrap();
            save(
                &dir.join("capture.json"),
                &json!({"destination":if name=="good" {"squidprint"} else {"plotline"},"sha256":hash}),
            )
            .unwrap();
        }
        let ids = intake(&app, &inbox).unwrap();
        assert_eq!(ids.len(), 1);
        let path = id_path(&app.store.root, "documents", &ids[0]).unwrap();
        assert_eq!(fs::read(path.join("source.pdf")).unwrap(), b"exact PDF");
        let mut record = read(&path.join("record.json")).unwrap();
        assert_eq!(record["auto_draft"], false);
        // Inspection can resume after restart without opting the capture into
        // automatic pen interpretation or HPGL publication.
        record["status"] = json!("inspecting");
        save(&path.join("record.json"), &record).unwrap();
        recover(&app.store).unwrap();
        assert_eq!(
            read(&path.join("record.json")).unwrap()["status"],
            "received"
        );
        assert_eq!(
            read(&path.join("record.json")).unwrap()["auto_draft"],
            false
        );
        assert!(intake(&app, &inbox).unwrap().is_empty());
        assert_eq!(list(&app.store.root, "documents").unwrap().len(), 1);
    }
    #[test]
    fn capture_dimensions_are_provenance_for_exact_bytes_and_all_matching_tickets() {
        let temp = tempfile::tempdir().unwrap();
        for (id, hash, media) in [
            (
                "large",
                "same",
                "{media-size={x-dimension=91440 y-dimension=160428}}",
            ),
            (
                "letter",
                "same",
                "{media-size={x-dimension=21590 y-dimension=27940}}",
            ),
            (
                "unrelated",
                "different",
                "{media-size={x-dimension=50000 y-dimension=50000}}",
            ),
            (
                "unknown",
                "same",
                "{media-size={x-dimension=-1 y-dimension=0}}",
            ),
        ] {
            let dir = temp.path().join("state/print-sink/captures").join(id);
            fs::create_dir_all(&dir).unwrap();
            save(
                &dir.join("capture.json"),
                &json!({"id":id,"sha256":hash,
                "ipp":{"IPP_MEDIA_COL":media,"IPP_JOB_NAME":"same filename"}}),
            )
            .unwrap();
        }
        let rows = matching_captures(temp.path(), "same").unwrap();
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0]["requested_size_mm"], json!([914.4, 1604.28]));
        assert_eq!(rows[1]["requested_size_mm"], json!([215.9, 279.4]));
        assert!(rows[2]["requested_size_mm"].is_null());
        assert!(matching_captures(temp.path(), "absent").unwrap().is_empty());
    }
    #[test]
    fn recover_retains_original_and_marks_interrupted_revision() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path().into()).unwrap();
        let id = Uuid::new_v4().to_string();
        let path = id_path(&store.root, "documents", &id).unwrap();
        fs::create_dir_all(&path).unwrap();
        write(&path.join("source.pdf"), b"exact original").unwrap();
        save(
            &path.join("record.json"),
            &json!({"id":id,"status":"running","created_at":1}),
        )
        .unwrap();
        recover(&store).unwrap();
        assert_eq!(
            fs::read(path.join("source.pdf")).unwrap(),
            b"exact original"
        );
        assert_eq!(read(&path.join("record.json")).unwrap()["status"], "error");
        assert!(id_path(&store.root, "documents", "../escape").is_err());
    }
}
