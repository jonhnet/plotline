use crate::layout::Setup;
use crate::store::{Job, MAX_BYTES, Store};
use axum::{
    Json, Router,
    body::Bytes,
    extract::{DefaultBodyLimit, Path, Query, State},
    http::{HeaderMap, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
};
use serde::Deserialize;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

#[derive(Clone)]
struct App {
    store: Store,
    work: Arc<Semaphore>,
    plotter: Option<Arc<crate::plot::Plotter>>,
    device_work: Arc<Semaphore>,
}

pub(crate) struct ApiError(pub(crate) StatusCode, pub(crate) String);
// Opt in only for embedded, content-addressed public build artifacts. Ordinary
// assets, user documents, errors and proxied responses retain no-store.
#[derive(Clone)]
pub(crate) struct ImmutableAsset;
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":self.1}))).into_response()
    }
}

async fn security(request: axum::extract::Request, next: Next) -> Response {
    let _admission = if request.method() == axum::http::Method::GET
        && matches!(
            request.uri().path(),
            "/api/status" | "/api/maintenance" | "/map/api/maintenance"
        ) {
        None
    } else {
        match crate::maintenance::lease() {
            Ok(lease) => lease,
            Err(_) => {
                return ApiError(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "Plotline is being updated. Retry in a moment.".into(),
                )
                .into_response();
            }
        }
    };
    let mut response = next.run(request).await;
    let immutable = response.extensions().get::<ImmutableAsset>().is_some();
    let headers = response.headers_mut();
    headers.entry(header::CONTENT_SECURITY_POLICY).or_insert("default-src 'self'; worker-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'".parse().unwrap());
    headers.insert(header::X_CONTENT_TYPE_OPTIONS, "nosniff".parse().unwrap());
    if !immutable {
        headers.insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    }
    response
}

#[cfg(test)]
pub fn router(store: Store) -> Router {
    router_with_plotter(store, None)
}

pub fn router_with_plotter(store: Store, plotter: Option<Arc<crate::plot::Plotter>>) -> Router {
    let calibration = crate::calibration::router(store.clone());
    let pdf = crate::documents::router(store.clone());
    let patterns = crate::patterns::router(store.clone());
    let mazes = crate::mazes::router(store.clone());
    Router::new()
        .route(
            "/paper-units.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../../shared/web/paper-units.js"),
                )
            }),
        )
        .route(
            "/studio.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../../shared/web/studio.js"),
                )
            }),
        )
        .route(
            "/recipe-files.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../../shared/web/recipe-files.js"),
                )
            }),
        )
        .route(
            "/studio.css",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/css; charset=utf-8")],
                    include_str!("../../shared/web/studio.css"),
                )
            }),
        )
        .route(
            "/preview-navigation.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../../shared/web/preview-navigation.js"),
                )
            }),
        )
        .route(
            "/inksets.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../../shared/web/inksets.js"),
                )
            }),
        )
        .route(
            "/inksets.css",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/css; charset=utf-8")],
                    include_str!("../../shared/web/inksets.css"),
                )
            }),
        )
        .route(
            "/hpgl",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
                    include_str!("../web/index.html"),
                )
            }),
        )
        .route(
            "/app.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/app.js"),
                )
            }),
        )
        .route(
            "/hpgl-workspace.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/hpgl-workspace.js"),
                )
            }),
        )
        .route(
            "/style.css",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/css; charset=utf-8")],
                    include_str!("../web/style.css"),
                )
            }),
        )
        .route(
            "/geometry.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/geometry.js"),
                )
            }),
        )
        .route(
            "/plot-controls.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/plot-controls.js"),
                )
            }),
        )
        .route(
            "/render-worker.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/render-worker.js"),
                )
            }),
        )
        .route("/api/device", get(device_observation))
        .route("/api/carousel", get(carousel).put(save_carousel))
        .route(
            "/carousel.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/carousel.js"),
                )
            }),
        )
        .route("/api/plotter", get(plotter_status))
        .route(
            "/recovery.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/recovery.js"),
                )
            }),
        )
        .route(
            "/recovery-worker.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/recovery-worker.js"),
                )
            }),
        )
        .route("/api/jobs/{id}/attempts", get(plot_history))
        .route(
            "/api/plots/{id}/recovery",
            get(recovery_preview).post(recovery_submit),
        )
        .route("/api/plots/{id}/program.hpgl", get(plot_program))
        .route("/api/plotter/check-paper", axum::routing::post(check_paper))
        .route("/api/jobs/{id}/plot", axum::routing::post(plot_submit))
        .route(
            "/api/jobs/{id}/estimate",
            axum::routing::post(plot_estimate),
        )
        .route("/api/plots/{id}", get(plot_lookup))
        .route("/api/plots/{id}/progress", get(plot_progress))
        .route(
            "/progress.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/progress.js"),
                )
            }),
        )
        .route(
            "/api/jobs/{id}/plot-plan",
            axum::routing::post(retired_plot_review),
        )
        .route(
            "/api/plots/{id}/start",
            axum::routing::post(retired_plot_review),
        )
        .route("/api/plotter/{action}", axum::routing::post(plot_control))
        .route("/api/jobs/{id}/setup", get(setup).put(save_setup))
        .route(
            "/api/jobs/{id}/recipes",
            get(hpgl_recipes)
                .post(save_hpgl_recipe)
                .layer(DefaultBodyLimit::max(1024 * 1024)),
        )
        .route("/api/jobs/{id}/recipes/{recipe}", get(hpgl_recipe))
        .route(
            "/api/jobs/{id}/prepared.hpgl",
            axum::routing::post(prepared),
        )
        .route("/api/status", get(service_status))
        .route("/api/maintenance", get(service_status))
        .route("/api/jobs", get(list).post(upload))
        .route("/api/jobs/{id}", get(job))
        .route("/api/jobs/{id}/preview.svg", get(preview))
        .route("/api/jobs/{id}/source.hpgl", get(source))
        .layer(DefaultBodyLimit::max(MAX_BYTES))
        .layer(middleware::from_fn(security))
        .with_state(App {
            store,
            work: Arc::new(Semaphore::new(2)),
            plotter,
            device_work: Arc::new(Semaphore::new(2)),
        })
        .merge(crate::studios::router().layer(middleware::from_fn(security)))
        .merge(pdf.layer(middleware::from_fn(security)))
        .merge(patterns.layer(middleware::from_fn(security)))
        .merge(mazes.layer(middleware::from_fn(security)))
        .merge(calibration.layer(middleware::from_fn(security)))
}

async fn service_status(State(app): State<App>) -> Json<serde_json::Value> {
    Json(
        json!({"name":"Plotline Studios", "version":env!("CARGO_PKG_VERSION"),
        "mode": if app.plotter.is_some() { "controller" } else { "preview" },
        "hardware_enabled":app.plotter.is_some(),"device":"HP 7585","max_upload_bytes":MAX_BYTES,
        "admission_protocol":1,"source_revision":std::env::var("PLOTLINE_REVISION").ok()}),
    )
}

async fn device_blocking<T: Send + 'static>(
    app: App,
    operation: impl FnOnce(Arc<crate::plot::Plotter>, Store) -> anyhow::Result<T> + Send + 'static,
) -> Result<Json<T>, ApiError> {
    let plotter = app.plotter.ok_or_else(|| {
        ApiError(
            StatusCode::SERVICE_UNAVAILABLE,
            "This server is preview-only; no plotter connection configured".into(),
        )
    })?;
    let permit = app
        .device_work
        .acquire_owned()
        .await
        .map_err(|_| ApiError(StatusCode::SERVICE_UNAVAILABLE, "Server stopping".into()))?;
    tokio::task::spawn_blocking(move || {
        let _admission = crate::maintenance::lease()?;
        let _permit = permit;
        operation(plotter, app.store)
    })
    .await
    .map_err(|e| ApiError(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
    .map(Json)
    .map_err(|e| ApiError(StatusCode::UNPROCESSABLE_ENTITY, e.to_string()))
}
async fn plotter_status(State(app): State<App>) -> Result<Json<serde_json::Value>, ApiError> {
    if app.plotter.is_none() {
        return Ok(Json(json!({"configured":false,"connected":false})));
    }
    device_blocking(app, |p, _| p.status()).await
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PaperCheck {
    job_id: Option<String>,
    setup: Option<Setup>,
}
async fn check_paper(
    State(app): State<App>,
    headers: HeaderMap,
    Json(request): Json<PaperCheck>,
) -> Result<Json<serde_json::Value>, ApiError> {
    check_write(&headers)?;
    device_blocking(app, move |p, s| {
        anyhow::ensure!(
            request.job_id.is_some() == request.setup.is_some(),
            "Supply both drawing and settings, or neither"
        );
        if let (Some(id), Some(setup)) = (&request.job_id, &request.setup) {
            setup.validate(&s.get(id)?)?;
        }
        let observation = p.check_paper(&s)?;
        let fit_error = if let (Some(id), Some(setup)) = (&request.job_id, request.setup) {
            let context = setup.fit_context(&s.get(id)?, Some(&observation));
            (|| -> anyhow::Result<()> {
                let plan = preview_plan_observed(&s, id, setup, Some(&observation))?;
                crate::prepare::check_loaded_window(&plan.commands, observation.limits)
                    .map_err(|error| crate::layout::explain_fit(error, &context))
            })()
            .err()
            .map(|e| e.to_string())
        } else {
            None
        };
        Ok(json!({"observation":observation,"job_id":request.job_id,"fit_error":fit_error}))
    })
    .await
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Submission {
    attempt: String,
    setup: Setup,
}
async fn plot_submit(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(request): Json<Submission>,
) -> Result<(StatusCode, Json<crate::plot::Ticket>), ApiError> {
    check_write(&headers)?;
    Ok((
        StatusCode::ACCEPTED,
        device_blocking(app, move |p, s| {
            p.submit(s, id, request.attempt, request.setup)
        })
        .await?,
    ))
}
async fn plot_lookup(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, ApiError> {
    device_blocking(app, move |p, _| Ok(json!({"submission":p.lookup(&id)?}))).await
}
async fn plot_history(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<Json<Vec<crate::plot::Ticket>>, ApiError> {
    device_blocking(app, move |p, s| {
        s.get(&id)?;
        p.history(&id)
    })
    .await
}
async fn recovery_preview(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, ApiError> {
    device_blocking(app, move |p, _| p.recovery_preview(&id)).await
}
async fn plot_program(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<Response, ApiError> {
    let Json(program) = device_blocking(app, move |p, _| p.program(&id)).await?;
    Ok((
        [(header::CONTENT_TYPE, "text/plain; charset=utf-8")],
        program,
    )
        .into_response())
}
async fn recovery_submit(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(request): Json<crate::recovery::Request>,
) -> Result<(StatusCode, Json<crate::plot::Ticket>), ApiError> {
    check_write(&headers)?;
    Ok((
        StatusCode::ACCEPTED,
        device_blocking(app, move |p, s| p.recover(s, id, request)).await?,
    ))
}
async fn retired_plot_review(headers: HeaderMap) -> Result<(), ApiError> {
    check_write(&headers)?;
    Err(ApiError(
        StatusCode::GONE,
        "Refresh Plotline Studios to use the new Plot button".into(),
    ))
}
async fn plot_progress(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<Json<crate::progress::Progress>, ApiError> {
    device_blocking(app, move |p, _| p.progress(&id)).await
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Control {
    attempt: String,
}
async fn plot_control(
    State(app): State<App>,
    Path(action): Path<String>,
    headers: HeaderMap,
    Json(request): Json<Control>,
) -> Result<Json<serde_json::Value>, ApiError> {
    check_write(&headers)?;
    device_blocking(app, move |p, _| p.control(&action, &request.attempt)).await
}

async fn blocking<T: Send + 'static>(
    app: App,
    f: impl FnOnce(Store) -> anyhow::Result<T> + Send + 'static,
    status: StatusCode,
) -> Result<T, ApiError> {
    let permit = app
        .work
        .acquire_owned()
        .await
        .map_err(|_| ApiError(StatusCode::SERVICE_UNAVAILABLE, "Server stopping".into()))?;
    tokio::task::spawn_blocking(move || {
        let _admission = crate::maintenance::lease()?;
        let _permit = permit;
        f(app.store)
    })
    .await
    .map_err(|e| {
        eprintln!("Worker failure: {e}");
        ApiError(StatusCode::INTERNAL_SERVER_ERROR, "Worker failed".into())
    })?
    .map_err(|e| {
        eprintln!("Request failed: {e:#}");
        ApiError(
            status,
            if status == StatusCode::INTERNAL_SERVER_ERROR {
                "Could not read the job library; see server log".into()
            } else {
                e.to_string()
            },
        )
    })
}

#[derive(Default, Deserialize)]
struct LibraryQuery {
    #[serde(default)]
    library: bool,
}
async fn list(
    State(app): State<App>,
    Query(query): Query<LibraryQuery>,
) -> Result<Json<serde_json::Value>, ApiError> {
    Ok(Json(
        blocking(
            app,
            move |s| {
                if !query.library {
                    return Ok(json!(s.list()?));
                }
                let mut rows = vec![];
                for job in s.list_all()? {
                    let recipes = crate::hpgl_recipes::list(&s, &job.id)?;
                    if s.is_current(&job)? || !recipes.is_empty() {
                        let mut row = json!(job);
                        row["recipes"] = json!(recipes);
                        rows.push(row);
                    }
                }
                Ok(json!(rows))
            },
            StatusCode::INTERNAL_SERVER_ERROR,
        )
        .await?,
    ))
}
async fn hpgl_recipes(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<Json<Vec<crate::hpgl_recipes::Recipe>>, ApiError> {
    Ok(Json(
        blocking(
            app,
            move |s| crate::hpgl_recipes::list(&s, &id),
            StatusCode::NOT_FOUND,
        )
        .await?,
    ))
}
async fn hpgl_recipe(
    State(app): State<App>,
    Path((id, key)): Path<(String, String)>,
) -> Result<Json<crate::hpgl_recipes::Recipe>, ApiError> {
    Ok(Json(
        blocking(
            app,
            move |s| crate::hpgl_recipes::get(&s, &id, &key),
            StatusCode::NOT_FOUND,
        )
        .await?,
    ))
}
async fn save_hpgl_recipe(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(request): Json<crate::hpgl_recipes::Save>,
) -> Result<Json<crate::hpgl_recipes::Recipe>, ApiError> {
    check_write(&headers)?;
    let result = blocking(
        app,
        move |s| Ok(crate::hpgl_recipes::save(&s, &id, request)),
        StatusCode::INTERNAL_SERVER_ERROR,
    )
    .await?;
    result.map(Json).map_err(|e| {
        ApiError(
            if e.is::<crate::hpgl_recipes::Conflict>() {
                StatusCode::CONFLICT
            } else {
                StatusCode::UNPROCESSABLE_ENTITY
            },
            e.to_string(),
        )
    })
}
async fn job(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Job>, ApiError> {
    Ok(Json(
        blocking(app, move |s| s.get(&id), StatusCode::NOT_FOUND).await?,
    ))
}

#[derive(Deserialize)]
struct Import {
    name: String,
    units_per_mm: f64,
}

async fn upload(
    State(app): State<App>,
    Query(query): Query<Import>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<impl IntoResponse, ApiError> {
    check_write(&headers)?;
    let job = blocking(
        app,
        move |s| s.import(&query.name, &body, query.units_per_mm),
        StatusCode::UNPROCESSABLE_ENTITY,
    )
    .await?;
    Ok((StatusCode::CREATED, Json(job)))
}

pub(crate) fn check_write(headers: &HeaderMap) -> Result<(), ApiError> {
    // A non-simple custom header prevents cross-origin form submissions. No
    // CORS allowance is installed. CLI imports may omit Origin.
    let cross_site = headers
        .get("sec-fetch-site")
        .is_some_and(|v| v == "cross-site");
    if headers
        .get("x-plotline-request")
        .or_else(|| headers.get("x-plotline-upload"))
        .is_none_or(|v| v != "1")
        || cross_site
    {
        return Err(ApiError(
            StatusCode::FORBIDDEN,
            "Write requires a same-origin Plotline Studios request".into(),
        ));
    }
    if let Some(origin) = headers.get(header::ORIGIN) {
        let host = headers
            .get(header::HOST)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        let origin = origin.to_str().unwrap_or("");
        if origin != format!("http://{host}") && origin != format!("https://{host}") {
            return Err(ApiError(
                StatusCode::FORBIDDEN,
                "Cross-origin write rejected".into(),
            ));
        }
    }
    Ok(())
}

async fn device_observation(State(app): State<App>) -> Result<Json<serde_json::Value>, ApiError> {
    let observation = blocking(app, |s| s.observation(), StatusCode::INTERNAL_SERVER_ERROR).await?;
    Ok(Json(json!({"live":false,"observation":observation})))
}
async fn carousel(State(app): State<App>) -> Result<Json<crate::carousel::Carousel>, ApiError> {
    Ok(Json(
        blocking(app, |s| s.carousel.get(), StatusCode::INTERNAL_SERVER_ERROR).await?,
    ))
}
async fn save_carousel(
    State(app): State<App>,
    headers: HeaderMap,
    Json(value): Json<crate::carousel::Carousel>,
) -> Result<Json<crate::carousel::Carousel>, ApiError> {
    check_write(&headers)?;
    Ok(Json(
        blocking(app, move |s| s.carousel.save(value), StatusCode::CONFLICT).await?,
    ))
}
async fn setup(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Setup>, ApiError> {
    Ok(Json(
        blocking(app, move |s| s.setup(&id), StatusCode::NOT_FOUND).await?,
    ))
}
async fn save_setup(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(mut value): Json<Setup>,
) -> Result<Json<Setup>, ApiError> {
    check_write(&headers)?;
    let saved = blocking(
        app,
        move |s| {
            value.upgrade(&s.get(&id)?);
            s.save_setup(&id, &value)?;
            Ok(value)
        },
        StatusCode::UNPROCESSABLE_ENTITY,
    )
    .await?;
    Ok(Json(saved))
}
async fn prepared(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(value): Json<Setup>,
) -> Result<impl IntoResponse, ApiError> {
    check_write(&headers)?;
    let commands = blocking(
        app,
        move |s| Ok(preview_plan(&s, &id, value)?.document),
        StatusCode::UNPROCESSABLE_ENTITY,
    )
    .await?;
    Ok((
        [
            (header::CONTENT_TYPE, "application/octet-stream"),
            (
                header::CONTENT_DISPOSITION,
                "attachment; filename=prepared.hpgl",
            ),
        ],
        commands,
    ))
}

fn preview_plan(s: &Store, id: &str, value: Setup) -> anyhow::Result<crate::prepare::Prepared> {
    // Offline previews use the selected sheet, not whichever sheet was last
    // measured by a previous plot. Actual submission still prepares against a
    // fresh observation in Plotter::prepare, including finite pen footprints.
    let observation = if value.window == "reported" {
        s.observation()?
    } else {
        None
    };
    preview_plan_observed(s, id, value, observation.as_ref())
}
fn preview_plan_observed(
    s: &Store,
    id: &str,
    mut value: Setup,
    observation: Option<&crate::layout::Observation>,
) -> anyhow::Result<crate::prepare::Prepared> {
    let job = s.get(id)?;
    value.upgrade(&job);
    value.validate(&job)?;
    let colors = s.carousel.get()?.resolve(&mut value, &job)?;
    let source = crate::storage::read_to_string(s.path(id)?.join("source.hpgl"))?;
    let context = value.fit_context(&job, observation);
    crate::prepare::prepare_registered(
        &source,
        job.units_per_mm,
        value.profile(observation)?,
        value.rotation,
        value.pens,
        value.pen_map,
        value.ink_settings,
        colors,
        job.registration().is_some(),
        job.calibration.is_some().then_some(5.),
    )
    .map_err(|error| crate::layout::explain_fit(error, &context))
}

async fn plot_estimate(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(value): Json<Setup>,
) -> Result<Json<serde_json::Value>, ApiError> {
    check_write(&headers)?;
    // Same frozen preparation rules as export. No device access or plot ticket.
    let estimate = blocking(
        app,
        move |s| {
            let plan = preview_plan(&s, &id, value)?;
            let inventory = s.carousel.get()?;
            let inks: Vec<_> = plan.report["decoded"]["pens"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|pen| {
                    let slot = pen["id"].as_u64().unwrap_or(0) as u8;
                    json!({"slot":slot,"loaded":{"color":pen["color"],
                        "label":inventory.slots.get(&slot).map(|p| &p.label)}})
                })
                .collect();
            let mut result =
                serde_json::to_value(crate::timing::forecast(&plan.commands)?.estimate)?;
            result["progress"] =
                serde_json::to_value(crate::progress::describe(&plan.commands, &inks))?;
            Ok(result)
        },
        StatusCode::UNPROCESSABLE_ENTITY,
    )
    .await?;
    Ok(Json(estimate))
}

async fn asset(app: App, id: String, name: &'static str) -> Result<Vec<u8>, ApiError> {
    blocking(
        app,
        move |s| {
            let path = s.path(&id)?;
            crate::storage::touch(&path);
            if name == "preview.svg" && !crate::storage::exists(&path.join(name)) {
                let job = s.get(&id)?;
                let source = crate::storage::read_to_string(path.join("source.hpgl"))?;
                return Ok(crate::hpgl::parse(&source, job.units_per_mm)?
                    .svg()
                    .into_bytes());
            }
            Ok(crate::storage::read(path.join(name))?)
        },
        StatusCode::NOT_FOUND,
    )
    .await
}
async fn preview(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, ApiError> {
    Ok((
        [(header::CONTENT_TYPE, "image/svg+xml")],
        asset(app, id, "preview.svg").await?,
    ))
}
async fn source(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, ApiError> {
    Ok((
        [
            (header::CONTENT_TYPE, "application/octet-stream"),
            (
                header::CONTENT_DISPOSITION,
                "attachment; filename=original.hpgl",
            ),
        ],
        asset(app, id, "source.hpgl").await?,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_require_a_same_origin_studio_header() {
        assert!(check_write(&HeaderMap::new()).is_err());
        for name in ["x-plotline-request", "x-plotline-upload"] {
            let mut headers = HeaderMap::new();
            headers.insert(name, "1".parse().unwrap());
            assert!(check_write(&headers).is_ok());
            headers.insert("sec-fetch-site", "cross-site".parse().unwrap());
            assert!(check_write(&headers).is_err());
        }
    }

    use axum::{
        body::{Body, to_bytes},
        http::Request,
    };
    use tower::ServiceExt;
    #[test]
    fn fit_diagnostics_preserve_registered_origin_and_pen_footprint() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(temp.path().to_owned()).unwrap();
        let source =
            b"IN;CO\"pen id=1 r=0 g=0 b=0 width_mm=0.3\";SP1;PU4400,-1600;PD11600,1600;PU;SP0;";
        let mut inventory = store.carousel.get().unwrap();
        let pen = inventory.slots.get_mut(&1).unwrap();
        pen.state = "loaded".into();
        pen.color = Some("#000000".into());
        pen.width_mm = Some(0.3);
        store.carousel.save(inventory).unwrap();
        let registration = crate::media::Registration {
            sheet_size_mm: [240., 140.],
            sheet_to_device_units: [40., 0., 0., -40., 3200., 2800.],
            limits: [4000, -2000, 12000, 2000],
            rotation: 0,
            evidence: "Isolated offset fixture".into(),
        };
        let job = store
            .import_pdf(
                "Offset.pdf.hpgl",
                source,
                crate::store::PdfSource {
                    document_id: uuid::Uuid::new_v4().to_string(),
                    conversion_id: uuid::Uuid::new_v4().to_string(),
                    page_index: 0,
                    registration,
                    raster_preview: false,
                },
            )
            .unwrap();
        let setup = store.setup(&job.id).unwrap();
        let expected = preview_plan(&store, &job.id, setup.clone())
            .unwrap()
            .commands;
        assert!(
            expected.contains("PU4400,-1600;"),
            "Original device placement survives export"
        );
        let mut observation = crate::layout::Observation {
            identity: "7585B".into(),
            observed_at: "2026-11-01T00:00:00Z".into(),
            units_per_mm: 40.,
            limits: [0, -2000, 8000, 2000],
        };
        // Equal dimensions do not imply equal placement: this window is 100 mm left.
        let error = preview_plan_observed(&store, &job.id, setup.clone(), Some(&observation))
            .err()
            .unwrap()
            .to_string();
        assert!(error.contains("does not fit"), "{error}");
        assert!(error.contains("Document sheet 9.45 × 5.51 in"), "{error}");
        assert!(
            error.contains("Measured drawable area 7.87 × 3.94 in"),
            "{error}"
        );
        assert!(
            error.contains("front (+X) by 3.543 in (90.00 mm)"),
            "{error}"
        );
        assert!(error.contains(&observation.observed_at), "{error}");
        // Centerlines fit exactly, but the finite pen still must not cross an edge.
        observation.limits = [4400, -2000, 11600, 2000];
        let error = preview_plan_observed(&store, &job.id, setup.clone(), Some(&observation))
            .err()
            .unwrap()
            .to_string();
        assert!(error.contains("pen footprint exceeds"), "{error}");
        assert!(error.contains("Measured drawable area"), "{error}");
        assert!(
            !error.contains("Drawing bounds exceed"),
            "Centerline diagnostics must not invent overflow: {error}"
        );
        assert_eq!(
            preview_plan(&store, &job.id, setup).unwrap().commands,
            expected
        );
        assert!(!temp.path().join("plots").exists());
    }

    #[tokio::test]
    async fn estimate_uses_planned_pdf_sheet_without_loading_paper_or_starting_plot() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(temp.path().to_owned()).unwrap();
        let source =
            b"IN;CO\"pen id=1 r=0 g=0 b=0 width_mm=0.3\";SP1;PU-7000,-1000;PD7000,1000;PU;SP0;";
        let registration =
            crate::media::Registration::for_media(&crate::media::profile([431.8, 279.4]).unwrap())
                .unwrap();
        let job = store
            .import_pdf(
                "Tabloid.pdf.hpgl",
                source,
                crate::store::PdfSource {
                    document_id: uuid::Uuid::new_v4().to_string(),
                    conversion_id: uuid::Uuid::new_v4().to_string(),
                    page_index: 0,
                    registration,
                    raster_preview: false,
                },
            )
            .unwrap();
        let mut inventory = store.carousel.get().unwrap();
        let pen = inventory.slots.get_mut(&1).unwrap();
        pen.state = "loaded".into();
        pen.color = Some("#000000".into());
        pen.width_mm = Some(0.3);
        store.carousel.save(inventory).unwrap();
        let setup = store.setup(&job.id).unwrap();
        let expected = preview_plan(&store, &job.id, setup.clone())
            .unwrap()
            .commands;
        let observation = crate::layout::Observation {
            identity: "7585B".into(),
            observed_at: "2026-11-04T04:49:13Z".into(),
            units_per_mm: 40.,
            limits: [-3202, -5088, 3202, 5088],
        };
        let saved = serde_json::to_vec(&observation).unwrap();
        std::fs::write(temp.path().join("device-observation.json"), &saved).unwrap();
        assert_eq!(
            preview_plan(&store, &job.id, setup.clone())
                .unwrap()
                .commands,
            expected
        );
        // The physical preparation path must still reject this smaller loaded
        // sheet. Changing preview policy must not relax that independent check.
        assert!(
            crate::prepare::prepare_registered(
                std::str::from_utf8(source).unwrap(),
                40.,
                setup.profile(Some(&observation)).unwrap(),
                setup.rotation,
                setup.pens.clone(),
                setup.pen_map.clone(),
                setup.ink_settings.clone(),
                Default::default(),
                true,
                None,
            )
            .is_err()
        );
        let app = router(store.clone());
        for endpoint in ["estimate", "prepared.hpgl"] {
            let response = app
                .clone()
                .oneshot(
                    Request::post(format!("/api/jobs/{}/{endpoint}", job.id))
                        .header("X-Plotline-Request", "1")
                        .header("Content-Type", "application/json")
                        .body(Body::from(serde_json::to_vec(&setup).unwrap()))
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(response.status(), 200);
            let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
            if endpoint == "estimate" {
                let value: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
                assert_eq!(value["progress"]["total_bytes"], expected.len());
                assert!(
                    value["progress"]["sections"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .any(|section| section["slot"] == 1 && section["color"] == "#000000")
                );
                let result: crate::timing::Estimate = serde_json::from_slice(&bytes).unwrap();
                assert!(result.motion_seconds > 0.);
                assert_eq!(
                    result.prepared_sha256,
                    crate::timing::forecast(&expected)
                        .unwrap()
                        .estimate
                        .prepared_sha256
                );
            }
        }
        assert!(
            !temp.path().join("plots").exists(),
            "No plot attempt or device configured"
        );
        assert_eq!(
            std::fs::read(temp.path().join("device-observation.json")).unwrap(),
            saved
        );
        // Raw HPGL can explicitly request the reported drawing area; that
        // selection must continue to use the stored limits.
        let raw = store.import("raw.hpgl", source, 40.).unwrap();
        let mut raw_setup = store.setup(&raw.id).unwrap();
        assert!(preview_plan(&store, &raw.id, raw_setup.clone()).is_ok());
        raw_setup.window = "reported".into();
        assert!(preview_plan(&store, &raw.id, raw_setup).is_err());
    }

    #[tokio::test]
    async fn upload_preview_download_and_no_hardware_routes() {
        let temp = tempfile::tempdir().unwrap();
        let app = router(Store::open(temp.path().to_owned()).unwrap());
        let source = "IN;SP1;PU0,0;PD40,40;PU;SP0;";
        let request = |authorized| {
            let mut r = Request::post("/api/jobs?name=test.hpgl&units_per_mm=40");
            if authorized {
                r = r.header("x-plotline-upload", "1");
            }
            r.body(Body::from(source)).unwrap()
        };
        assert_eq!(
            app.clone().oneshot(request(false)).await.unwrap().status(),
            403
        );
        let response = app.clone().oneshot(request(true)).await.unwrap();
        assert_eq!(response.status(), 201);
        let job: Job =
            serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
                .unwrap();
        for (suffix, content_type) in [
            ("preview.svg", "image/svg+xml"),
            ("source.hpgl", "application/octet-stream"),
        ] {
            let r = app
                .clone()
                .oneshot(
                    Request::get(format!("/api/jobs/{}/{suffix}", job.id))
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(r.status(), 200);
            assert_eq!(r.headers()[header::CONTENT_TYPE], content_type);
            if suffix == "source.hpgl" {
                assert_eq!(to_bytes(r.into_body(), usize::MAX).await.unwrap(), source);
            }
        }
        for path in ["/api/plot", "/api/play", "/api/jobs/not-a-uuid/source.hpgl"] {
            let r = app
                .clone()
                .oneshot(Request::get(path).body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(r.status(), 404);
        }
        let r = app
            .oneshot(
                Request::post("/api/jobs?name=bad.hpgl&units_per_mm=40")
                    .header("x-plotline-upload", "1")
                    .body(Body::from("IN;SC0,1,0,1;"))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), 422);
    }
    #[tokio::test]
    async fn cross_origin_upload_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let app = router(Store::open(dir.path().to_owned()).unwrap());
        let r = app
            .oneshot(
                Request::post("/api/jobs?name=test&units_per_mm=40")
                    .header("x-plotline-upload", "1")
                    .header("host", "dev:9191")
                    .header("origin", "https://unrelated.example")
                    .body(Body::from("IN;"))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), 403);
    }

    #[tokio::test]
    async fn hardware_writes_require_header_and_preview_mode_cannot_start() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path().to_owned()).unwrap();
        let job = store
            .import("test.hpgl", b"IN;SP1;PU0,0;PD40,40;PU;SP0;", 40.)
            .unwrap();
        let request =
            json!({"attempt":"00000000-0000-0000-0000-000000000000","setup":Setup::new(&job)});
        let app = router(store);
        for (path, body) in [
            (format!("/api/jobs/{}/plot", job.id), request.to_string()),
            (
                "/api/plots/00000000-0000-0000-0000-000000000000/recovery".into(),
                json!({"attempt":"00000000-0000-0000-0000-000000000001","source_sha256":"abc","start_stroke":0,"pause_before_pen_change":true}).to_string(),
            ),
            (
                "/api/plotter/cancel".into(),
                json!({"attempt":"00000000-0000-0000-0000-000000000000"}).to_string(),
            ),
        ] {
            for origin in [None, Some("https://unrelated.example")] {
                let mut r = Request::post(&path).header("Content-Type", "application/json");
                if let Some(origin) = origin {
                    r = r
                        .header("Origin", origin)
                        .header("Host", "dev:9191")
                        .header("X-Plotline-Request", "1");
                }
                let response = app
                    .clone()
                    .oneshot(r.body(Body::from(body.clone())).unwrap())
                    .await
                    .unwrap();
                assert_eq!(response.status(), 403);
            }
        }
        let response = app
            .oneshot(
                Request::post(format!("/api/jobs/{}/plot", job.id))
                    .header("X-Plotline-Request", "1")
                    .header("Content-Type", "application/json")
                    .body(Body::from(request.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), 503);
    }
}
