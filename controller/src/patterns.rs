//! Shared Pattern/Maze document storage and immutable PDF/intent exports.
use crate::{store::Store, web::ApiError};
use anyhow::{Context, Result, ensure};
use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Path, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::get,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Write,
    path::{Path as FsPath, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

mod thumbnails {
    include!(concat!(env!("OUT_DIR"), "/pattern-thumbnails/assets.rs"));
}

#[derive(Clone)]
struct App {
    kind: &'static str,
    root: PathBuf,
    project: PathBuf,
    writes: Arc<Mutex<()>>,
}
fn bad(e: impl std::fmt::Display) -> ApiError {
    ApiError(StatusCode::UNPROCESSABLE_ENTITY, e.to_string())
}
fn missing(e: impl std::fmt::Display) -> ApiError {
    ApiError(StatusCode::NOT_FOUND, e.to_string())
}
fn authorize(h: &HeaderMap) -> Result<(), ApiError> {
    if h.get("x-plotline-request").and_then(|v| v.to_str().ok()) != Some("1") {
        return Err(ApiError(
            StatusCode::FORBIDDEN,
            "Missing X-Plotline-Request: 1".into(),
        ));
    }
    Ok(())
}
fn read(path: &FsPath) -> Result<Value> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}
fn write(path: &FsPath, value: &Value) -> Result<()> {
    fs::create_dir_all(path.parent().unwrap())?;
    let temp = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    let mut f = File::create(&temp)?;
    f.write_all(&serde_json::to_vec(value)?)?;
    f.sync_all()?;
    fs::rename(temp, path)?;
    File::open(path.parent().unwrap())?.sync_all()?;
    Ok(())
}
fn id(id: &str) -> Result<&str> {
    ensure!(
        Uuid::parse_str(id).is_ok_and(|v| v.to_string() == id),
        "Invalid document ID"
    );
    Ok(id)
}
fn hash_id(id: &str) -> Result<&str> {
    ensure!(
        id.len() == 64
            && id
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "Invalid source snapshot"
    );
    Ok(id)
}
fn recipe(kind: &str, value: &Value) -> Result<()> {
    ensure!(value["version"] == 1, "Unsupported drawing version");
    if kind == "mazes" {
        let options: crate::maze::Options = serde_json::from_value(value["params"].clone())?;
        ensure!(
            ["square", "hexagon", "triangle"].contains(&value["family"].as_str().unwrap_or("")),
            "Unknown maze geometry"
        );
        ensure!(
            (2..=160).contains(&options.columns) && (2..=160).contains(&options.rows),
            "Invalid maze dimensions"
        );
    } else {
        ensure!(
            [
                "koch",
                "dragon",
                "hilbert",
                "plant",
                "sierpinski",
                "apollonian",
                "julia",
                "menger",
                "spirograph",
                "lissajous",
                "harmonograph",
                "phyllotaxis",
                "penrose",
                "truchet",
                "mandelbrot",
                "burning_ship",
                "newton",
                "lorenz",
                "rossler",
                "barnsley",
                "crystal",
                "woven",
                "gosper",
                "dla",
                "tetrahedron",
                "levy",
                "minkowski",
                "carpet",
                "clifford",
                "dejong",
                "peano",
                "nested_polygons",
                "pythagoras",
                "lightning",
                "inversion",
                "ammann",
                "colonization",
                "architecture",
                "starweave",
                "guilloche",
                "wave_contours",
                "reaction",
                "flowers",
                "schottky",
                "pinwheel",
                "hatched_solids",
                "lsystem"
            ]
            .contains(&value["family"].as_str().unwrap_or("")),
            "Unknown pattern family"
        );
    }
    let name = value["name"].as_str().context("Name required")?;
    ensure!(
        !name.trim().is_empty()
            && name.chars().count() <= 100
            && !name.chars().any(char::is_control),
        "Use a name of 1–100 characters"
    );
    ensure!(
        value["params"].is_object()
            && value["media"].is_object()
            && value["inks"].is_array()
            && value["roles"].is_array(),
        "Incomplete drawing recipe"
    );
    Ok(())
}
fn export_record(app: &App, hash: &str) -> Result<Value> {
    let dir = app.root.join("exports").join(hash_id(hash)?);
    let mut result = read(&dir.join("snapshot.json"))?;
    result["intent"] = read(&dir.join("intent.json"))?;
    result["pdf_url"] = json!(format!("/api/{}/exports/{hash}/drawing.pdf", app.kind));
    Ok(result)
}
async fn list(State(app): State<App>) -> Result<Json<Value>, ApiError> {
    tokio::task::spawn_blocking(move || -> Result<Json<Value>> {
        let _admission = crate::maintenance::lease()?;
        let root = app.root.join("documents");
        let mut rows = vec![];
        if root.exists() {
            for entry in fs::read_dir(root)? {
                let path = entry?.path();
                if path.extension().is_some_and(|v| v == "json") {
                    let mut row = read(&path)?;
                    row.as_object_mut().unwrap().remove("recipe");
                    rows.push(row);
                }
            }
        }
        rows.sort_by_key(|v| std::cmp::Reverse(v["updated_at"].as_u64().unwrap_or(0)));
        Ok(Json(json!({"documents":rows})))
    })
    .await
    .map_err(bad)?
    .map_err(bad)
}
async fn document(
    State(app): State<App>,
    Path(key): Path<String>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(
        read(
            &app.root
                .join("documents")
                .join(format!("{}.json", id(&key).map_err(bad)?)),
        )
        .map_err(missing)?,
    ))
}
fn save_document(app: &App, request: Value) -> Result<Value, ApiError> {
    recipe(app.kind, &request["recipe"]).map_err(bad)?;
    let thumbnail = request["thumbnail"].as_str().unwrap_or("");
    if !thumbnail.is_empty()
        && (!thumbnail.starts_with("data:image/png;base64,") || thumbnail.len() > 500000)
    {
        return Err(bad("Invalid thumbnail"));
    }
    let key = request["id"]
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    id(&key).map_err(bad)?;
    let _guard = app.writes.lock().unwrap();
    let path = app.root.join("documents").join(format!("{key}.json"));
    let previous = if path.exists() {
        Some(read(&path).map_err(bad)?)
    } else {
        None
    };
    if let Some(ref old) = previous {
        if request["revision"] != old["revision"] {
            return Err(ApiError(
                StatusCode::CONFLICT,
                "This drawing changed in another tab. Open the saved version or use Save As."
                    .into(),
            ));
        }
    } else if request["id"].is_string() {
        return Err(missing("Saved drawing no longer exists"));
    }
    let row = json!({"id":key,"name":request["recipe"]["name"],"recipe":request["recipe"],"thumbnail":thumbnail,
        "revision":previous.map_or(1,|v|v["revision"].as_u64().unwrap_or(0)+1),"updated_at":SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()});
    write(&path, &row).map_err(bad)?;
    Ok(row)
}
async fn save(
    State(app): State<App>,
    headers: HeaderMap,
    Json(request): Json<Value>,
) -> Result<Json<Value>, ApiError> {
    authorize(&headers)?;
    tokio::task::spawn_blocking(move || {
        let _admission = crate::maintenance::lease().map_err(bad)?;
        save_document(&app, request).map(Json)
    })
    .await
    .map_err(bad)?
}
async fn snapshot(
    State(app): State<App>,
    Path(hash): Path<String>,
) -> Result<Json<Value>, ApiError> {
    hash_id(&hash).map_err(bad)?;
    Ok(Json(export_record(&app, &hash).map_err(missing)?))
}
async fn artifact(
    State(app): State<App>,
    Path((hash, file)): Path<(String, String)>,
) -> Result<Response, ApiError> {
    hash_id(&hash).map_err(bad)?;
    let mime = match file.as_str() {
        "drawing.pdf" => "application/pdf",
        "intent.json" => "application/json",
        _ => return Err(missing("Unknown artifact")),
    };
    Ok((
        [(header::CONTENT_TYPE, mime)],
        fs::read(app.root.join("exports").join(hash).join(file)).map_err(missing)?,
    )
        .into_response())
}
// One resource allowance shared by browser generation and both export stages.
const LIMITS: &str = include_str!("../../patterns/web/limits.json");
fn check_geometry_size(paths: usize, points: usize) -> Result<()> {
    ensure!(paths > 0, "Drawing has no paths");
    let limits: Value = serde_json::from_str(LIMITS)?;
    let bytes = (paths as u64)
        .saturating_mul(limits["bytes_per_path"].as_u64().unwrap())
        .saturating_add(
            (points as u64).saturating_mul(limits["bytes_per_point"].as_u64().unwrap()),
        );
    let allowance = limits["working_memory_bytes"].as_u64().unwrap();
    ensure!(
        bytes <= allowance,
        "Drawing needs about {} MiB of working memory ({} paths, {} points); allowance is {} MiB",
        bytes.div_ceil(1048576),
        paths,
        points,
        allowance / 1048576
    );
    Ok(())
}

async fn export(
    State(app): State<App>,
    headers: HeaderMap,
    Json(mut scene): Json<Value>,
) -> Result<Json<Value>, ApiError> {
    authorize(&headers)?;
    recipe(app.kind, &scene["recipe"]).map_err(bad)?;
    if let Some(key) = scene["document_id"].as_str() {
        id(key).map_err(bad)?;
    }
    let paths = scene["paths"]
        .as_array()
        .ok_or_else(|| bad("Paths required"))?;
    let points = paths.iter().try_fold(0usize, |sum, p| {
        let count = p["points"]
            .as_array()
            .ok_or_else(|| bad("Path points required"))?
            .len();
        sum.checked_add(count)
            .ok_or_else(|| bad("Point count overflow"))
    })?;
    check_geometry_size(paths.len(), points).map_err(bad)?;
    let hash = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&scene).map_err(bad)?)
    );
    scene["hash"] = json!(hash);
    tokio::task::spawn_blocking(move || -> Result<Json<Value>> {
        let _admission = crate::maintenance::lease()?;
        let _guard = app.writes.lock().unwrap();
        let root = app.root.join("exports");
        fs::create_dir_all(&root)?;
        let target = root.join(&hash);
        if !target.exists() {
            let stage = root.join(format!(".export-{}", Uuid::new_v4()));
            fs::create_dir(&stage)?;
            write(&stage.join("scene.json"), &scene)?;
            let output = Command::new(app.project.join(".venv/bin/python"))
                .arg(app.project.join("tools/pattern_pdf.py"))
                .arg("--kind")
                .arg(app.kind)
                .arg("--scene")
                .arg(stage.join("scene.json"))
                .arg("--directory")
                .arg(&stage)
                .stdin(Stdio::null())
                .output()?;
            if !output.status.success() {
                let detail = String::from_utf8_lossy(&output.stderr).to_string();
                fs::remove_dir_all(&stage)?;
                anyhow::bail!("Drawing export failed: {detail}");
            }
            write(
                &stage.join("snapshot.json"),
                &json!({"hash":hash,"recipe":scene["recipe"],"document_id":scene["document_id"]}),
            )?;
            for file in ["scene.json", "snapshot.json", "drawing.pdf", "intent.json"] {
                File::open(stage.join(file))?.sync_all()?;
            }
            File::open(&stage)?.sync_all()?;
            fs::rename(stage, &target)?;
            File::open(&root)?.sync_all()?;
        }
        Ok(Json(export_record(&app, &hash)?))
    })
    .await
    .map_err(bad)?
    .map_err(bad)
}
async fn asset(Path(name): Path<String>) -> Response {
    let (mime, body) = match name.as_str() {
        "thumbnails.js" => ("text/javascript", thumbnails::MANIFEST),
        "limits.json" => ("application/json", LIMITS),
        "preview.js" => (
            "text/javascript",
            include_str!("../../patterns/web/preview.js"),
        ),
        "app.js" => ("text/javascript", include_str!("../../patterns/web/app.js")),
        "generators.js" => (
            "text/javascript",
            include_str!("../../patterns/web/generators.js"),
        ),
        "complex.js" => (
            "text/javascript",
            include_str!("../../patterns/web/complex.js"),
        ),
        "collection.js" => (
            "text/javascript",
            include_str!("../../patterns/web/collection.js"),
        ),
        "botanical.js" => (
            "text/javascript",
            include_str!("../../patterns/web/botanical.js"),
        ),
        "schottky.js" => (
            "text/javascript",
            include_str!("../../patterns/web/schottky.js"),
        ),
        "pinwheel.js" => (
            "text/javascript",
            include_str!("../../patterns/web/pinwheel.js"),
        ),
        "hatched-solids.js" => (
            "text/javascript",
            include_str!("../../patterns/web/hatched-solids.js"),
        ),
        "groups.js" => (
            "text/javascript",
            include_str!("../../patterns/web/groups.js"),
        ),
        "gallery.js" => (
            "text/javascript",
            include_str!("../../patterns/web/gallery.js"),
        ),
        "textile.js" => (
            "text/javascript; charset=utf-8",
            include_str!("../../patterns/web/textile.js"),
        ),
        "wave-fields.js" => (
            "text/javascript; charset=utf-8",
            include_str!("../../patterns/web/wave-fields.js"),
        ),
        "ornament.js" => (
            "text/javascript",
            include_str!("../../patterns/web/ornament.js"),
        ),
        "recursive-art.js" => (
            "text/javascript; charset=utf-8",
            include_str!("../../patterns/web/recursive-art.js"),
        ),
        "subdivision.js" => (
            "text/javascript",
            include_str!("../../patterns/web/subdivision.js"),
        ),
        "density.js" => (
            "text/javascript",
            include_str!("../../patterns/web/density.js"),
        ),
        "spatial.js" => (
            "text/javascript",
            include_str!("../../patterns/web/spatial.js"),
        ),
        "natural.js" => (
            "text/javascript",
            include_str!("../../patterns/web/natural.js"),
        ),
        "adjacent.js" => (
            "text/javascript",
            include_str!("../../patterns/web/adjacent.js"),
        ),
        "worker.js" => (
            "text/javascript",
            include_str!("../../patterns/web/worker.js"),
        ),
        "style.css" => ("text/css", include_str!("../../patterns/web/style.css")),
        _ => return StatusCode::NOT_FOUND.into_response(),
    };
    ([(header::CONTENT_TYPE, mime)], body).into_response()
}
async fn thumbnail_asset(Path(name): Path<String>) -> Response {
    let Some((_, png)) = thumbnails::IMAGES.iter().find(|(key, _)| *key == name) else {
        return StatusCode::NOT_FOUND.into_response();
    };
    (
        axum::Extension(crate::web::ImmutableAsset),
        [
            (header::CONTENT_TYPE, "image/png"),
            (header::CACHE_CONTROL, "public, max-age=31536000, immutable"),
        ],
        *png,
    )
        .into_response()
}
pub fn router(store: Store) -> Router {
    document_router(store, "patterns")
        .route(
            "/patterns",
            get(|| async { axum::response::Html(include_str!("../../patterns/web/index.html")) }),
        )
        .route("/patterns/{name}", get(asset))
        .route("/patterns/thumbnails/{name}", get(thumbnail_asset))
        .route(
            "/authoring-print.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript")],
                    include_str!("../../shared/web/authoring-print.js"),
                )
            }),
        )
}
pub fn document_router(store: Store, kind: &'static str) -> Router {
    let project = std::env::var_os("PLOTLINE_PDF_PROJECT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .to_owned()
        });
    Router::new()
        .route(&format!("/api/{kind}/documents"), get(list).post(save))
        .route(&format!("/api/{kind}/documents/{{id}}"), get(document))
        .route(&format!("/api/{kind}/snapshots/{{hash}}"), get(snapshot))
        .route(&format!("/api/{kind}/exports"), axum::routing::post(export))
        .route(
            &format!("/api/{kind}/exports/{{hash}}/{{file}}"),
            get(artifact),
        )
        .layer(DefaultBodyLimit::max(
            serde_json::from_str::<Value>(LIMITS).unwrap()["scene_bytes"]
                .as_u64()
                .unwrap() as usize,
        ))
        .with_state(App {
            kind,
            root: store.root.join(kind),
            project,
            writes: Arc::new(Mutex::new(())),
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn large_drawings_share_the_memory_allowance() {
        assert!(check_geometry_size(178316, 356632).is_ok());
        assert!(check_geometry_size(1, 800000).is_ok());
        assert!(
            check_geometry_size(1000000, 2000000)
                .unwrap_err()
                .to_string()
                .contains("MiB of working memory")
        );
    }
    #[test]
    fn saved_patterns_require_exact_revision_and_safe_identity() {
        let temp = tempfile::tempdir().unwrap();
        let app = App {
            kind: "patterns",
            root: temp.path().into(),
            project: temp.path().into(),
            writes: Arc::new(Mutex::new(())),
        };
        let r = json!({"version":1,"name":"Snowflake","family":"koch","params":{},"media":{},"inks":[],"roles":[]});
        let a = save_document(&app, json!({"recipe":r})).unwrap_or_else(|e| panic!("{}", e.1));
        let b = save_document(&app, json!({"id":a["id"],"revision":1,"recipe":r}))
            .unwrap_or_else(|e| panic!("{}", e.1));
        assert_eq!(b["revision"], 2);
        assert!(matches!(
            save_document(&app, json!({"id":a["id"],"revision":1,"recipe":r})),
            Err(ApiError(StatusCode::CONFLICT, _))
        ));
        assert!(id("../outside").is_err());
        assert!(hash_id("../outside").is_err());
        assert_eq!(
            read(
                &app.root
                    .join("documents")
                    .join(format!("{}.json", a["id"].as_str().unwrap()))
            )
            .unwrap()["revision"],
            2
        );
    }
}
