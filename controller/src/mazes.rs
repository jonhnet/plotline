//! Maze Studio endpoints; generation never sends anything to the plotter.
use crate::{maze, patterns, store::Store, web::ApiError};
use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Multipart, Path, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Write,
    path::PathBuf,
    sync::Arc,
};
#[derive(Clone)]
struct App {
    root: PathBuf,
    gate: Arc<tokio::sync::Semaphore>,
}
fn bad(e: impl std::fmt::Display) -> ApiError {
    ApiError(StatusCode::UNPROCESSABLE_ENTITY, e.to_string())
}
fn authorize(headers: &HeaderMap) -> Result<(), ApiError> {
    if headers
        .get("x-plotline-request")
        .and_then(|h| h.to_str().ok())
        != Some("1")
    {
        return Err(ApiError(
            StatusCode::FORBIDDEN,
            "Missing X-Plotline-Request: 1".into(),
        ));
    }
    Ok(())
}
#[derive(Deserialize)]
struct Request {
    family: String,
    params: maze::Options,
}
async fn generate(
    State(app): State<App>,
    headers: HeaderMap,
    Json(request): Json<Request>,
) -> Result<Json<maze::Drawing>, ApiError> {
    authorize(&headers)?;
    let permit = app.gate.try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "Maze generator is busy; try again in a moment".into(),
        )
    })?;
    tokio::task::spawn_blocking(move || {
        let _admission = crate::maintenance::lease().map_err(bad)?;
        let _permit = permit;
        maze::generate(&request.family, &request.params)
            .map(Json)
            .map_err(bad)
    })
    .await
    .map_err(bad)?
}
async fn mask(
    State(app): State<App>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> Result<Json<Value>, ApiError> {
    authorize(&headers)?;
    let field = multipart
        .next_field()
        .await
        .map_err(bad)?
        .ok_or_else(|| bad("Choose a PNG or JPEG mask"))?;
    let bytes = field.bytes().await.map_err(bad)?;
    if bytes.len() > 8_000_000
        || !(bytes.starts_with(b"\x89PNG\r\n\x1a\n") || bytes.starts_with(b"\xff\xd8\xff"))
    {
        return Err(bad("Choose a PNG or JPEG mask smaller than 8 MB"));
    }
    let hash = format!("{:x}", Sha256::digest(&bytes));
    let saved = hash.clone();
    tokio::task::spawn_blocking(move || -> anyhow::Result<()> {
        let _admission = crate::maintenance::lease()?;
        let root = app.root.join("masks");
        fs::create_dir_all(&root)?;
        let target = root.join(&hash);
        if !target.exists() {
            let temp = root.join(format!(".{}", uuid::Uuid::new_v4()));
            let mut f = File::create(&temp)?;
            f.write_all(&bytes)?;
            f.sync_all()?;
            fs::rename(temp, target)?;
            File::open(root)?.sync_all()?;
        }
        Ok(())
    })
    .await
    .map_err(bad)?
    .map_err(bad)?;
    Ok(Json(json!({"hash":saved})))
}
async fn asset(Path(name): Path<String>) -> Response {
    let (mime, body) = match name.as_str() {
        "app.js" => ("text/javascript", include_str!("../../mazes/web/app.js")),
        "geometry.js" => (
            "text/javascript",
            include_str!("../../mazes/web/geometry.js"),
        ),
        "style.css" => ("text/css", include_str!("../../mazes/web/style.css")),
        _ => return StatusCode::NOT_FOUND.into_response(),
    };
    ([(header::CONTENT_TYPE, mime)], body).into_response()
}
pub fn router(store: Store) -> Router {
    let app = App {
        root: store.root.join("mazes"),
        gate: Arc::new(tokio::sync::Semaphore::new(2)),
    };
    patterns::document_router(store, "mazes").merge(
        Router::new()
            .route(
                "/mazes",
                get(|| async { axum::response::Html(include_str!("../../mazes/web/index.html")) }),
            )
            .route("/mazes/{name}", get(asset))
            .route("/api/mazes/generate", post(generate))
            .route("/api/mazes/masks", post(mask))
            .layer(DefaultBodyLimit::max(8_100_000))
            .with_state(app),
    )
}
