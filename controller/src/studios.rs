//! Shared studio entry points; Map remains an independent local service.
use axum::{
    Router,
    body::{Body, to_bytes},
    extract::{Request, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
    routing::get,
};
use std::time::Duration;

#[derive(Clone)]
struct MapProxy {
    client: reqwest::Client,
    upstream: String,
}

pub fn router() -> Router {
    let upstream =
        std::env::var("PLOTLINE_MAP_UPSTREAM").unwrap_or_else(|_| "http://127.0.0.1:9193".into());
    map_router(upstream)
        .route(
            "/",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
                    include_str!("../web/home.html"),
                )
            }),
        )
        .route(
            "/home.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../web/home.js"),
                )
            }),
        )
        .route(
            "/studio-navigation.js",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
                    include_str!("../../shared/web/studio-navigation.js"),
                )
            }),
        )
}

fn map_router(upstream: String) -> Router {
    let url = reqwest::Url::parse(&upstream).expect("PLOTLINE_MAP_UPSTREAM must be a URL");
    assert!(
        url.scheme() == "http"
            && url.host_str().is_some_and(|h| h == "localhost"
                || h.parse::<std::net::IpAddr>()
                    .is_ok_and(|ip| ip.is_loopback()))
            && url.username().is_empty()
            && url.password().is_none()
            && url.path() == "/"
            && url.query().is_none()
            && url.fragment().is_none(),
        "Map upstream must be a local HTTP service origin"
    );
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(3))
        .timeout(Duration::from_secs(120))
        .build()
        .expect("Map proxy client");
    Router::new()
        .route("/map", get(proxy).post(proxy))
        .route("/map/", get(proxy).post(proxy))
        .route("/map/{*path}", get(proxy).post(proxy))
        .with_state(MapProxy {
            client,
            upstream: upstream.trim_end_matches('/').into(),
        })
}

async fn proxy(State(map): State<MapProxy>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let path = parts.uri.path().strip_prefix("/map").unwrap_or("/");
    let path = if path.is_empty() { "/" } else { path };
    let url = format!(
        "{}{}{}",
        map.upstream,
        path,
        parts
            .uri
            .query()
            .map(|q| format!("?{q}"))
            .unwrap_or_default()
    );
    let body = match to_bytes(body, 8_000_000).await {
        Ok(value) => value,
        Err(_) => {
            return (
                StatusCode::PAYLOAD_TOO_LARGE,
                "Map requests must be smaller than 8 MB",
            )
                .into_response();
        }
    };
    let mut outbound = map.client.request(parts.method, url).body(body);
    // Preserve both Host and Origin: the Map service must still reject foreign
    // browser writes. Do not turn a rejected Origin into a trusted local one.
    for name in [
        header::HOST,
        header::ORIGIN,
        header::CONTENT_TYPE,
        header::ACCEPT,
    ] {
        if let Some(value) = parts.headers.get(&name) {
            outbound = outbound.header(name, value);
        }
    }
    outbound = outbound.header("X-Forwarded-Prefix", "/map");
    match outbound.send().await {
        Ok(upstream) => {
            let status = upstream.status();
            let headers = upstream.headers().clone();
            let mut response = Response::new(Body::from_stream(upstream.bytes_stream()));
            *response.status_mut() = status;
            for name in [
                header::CONTENT_TYPE,
                header::CONTENT_LENGTH,
                header::CONTENT_DISPOSITION,
                header::CONTENT_SECURITY_POLICY,
                header::CACHE_CONTROL,
            ] {
                if let Some(value) = headers.get(&name) {
                    response.headers_mut().insert(name, value.clone());
                }
            }
            response
        }
        Err(_) => (
            StatusCode::BAD_GATEWAY,
            "Map Studio is unavailable. The other studios are still available at /.",
        )
            .into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tower::ServiceExt;
    #[tokio::test]
    async fn proxy_keeps_path_query_body_and_origin() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let upstream=Router::new().fallback(|req:Request|async move{
            let (parts,body)=req.into_parts();
            let value=to_bytes(body,1000).await.unwrap();
            axum::Json(serde_json::json!({"path":parts.uri.to_string(),"origin":parts.headers.get("origin").unwrap().to_str().unwrap(),"host":parts.headers.get("host").unwrap().to_str().unwrap(),"prefix":parts.headers.get("x-forwarded-prefix").unwrap().to_str().unwrap(),"body":String::from_utf8(value.to_vec()).unwrap()}))
        });
        let server = tokio::spawn(async move { axum::serve(listener, upstream).await.unwrap() });
        let router = map_router(format!("http://{address}"));
        let request = Request::builder()
            .method("POST")
            .uri("/map/api/validate?revision=3")
            .header("host", "plotline.test")
            .header("origin", "https://foreign.test")
            .header("content-type", "application/json")
            .body(Body::from("{}"))
            .unwrap();
        let response = router.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let value: serde_json::Value =
            serde_json::from_slice(&to_bytes(response.into_body(), 1000).await.unwrap()).unwrap();
        assert_eq!(
            value,
            serde_json::json!({"path":"/api/validate?revision=3","origin":"https://foreign.test","host":"plotline.test","prefix":"/map","body":"{}"})
        );
        server.abort();
    }
}
