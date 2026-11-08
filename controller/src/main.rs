mod calibration;
mod carousel;
mod completion;
mod documents;
mod hpgl;
mod hpgl_recipes;
mod layout;
mod maintenance;
mod maze;
mod mazes;
mod media;
mod patterns;
mod plot;
mod prepare;
mod progress;
mod recovery;
mod serial;
mod storage;
mod store;
mod studios;
mod timing;
mod web;

use anyhow::{Context, Result, bail};
use std::path::PathBuf;

#[tokio::main]
async fn main() -> Result<()> {
    let mut root = PathBuf::from("controller/state/jobs");
    let mut listen = "127.0.0.1:9191".to_owned();
    let mut import = None;
    let mut name = None;
    let mut units = 40.0;
    let mut prepare_input = None;
    let mut estimate_input = None;
    let mut profile = None;
    let mut output = None;
    let mut rotation = 0;
    let mut pens = std::collections::BTreeSet::from([1, 2, 3]);
    let mut device_config = None;
    let mut device_helper = None;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == "--media-catalog" {
            println!("{}", serde_json::to_string_pretty(&media::catalog()?)?);
            return Ok(());
        }
        if arg == "--help" {
            println!(
                "Plotline Studios — HPGL preview and plotter controller\n\nplotline-studios [--listen 127.0.0.1:9191] [--state controller/state/jobs] [--device-config CONNECTION.json] [--device-helper TEST_TRANSPORT.py]\nplotline-studios --import FILE [--name NAME] [--units-per-mm 40] [--state DIR]\nplotline-studios --prepare FILE --profile PROFILE.json --output NEW_DIR [--rotation 0|90|180|270] [--pens 1,2,3] [--units-per-mm 40]\nplotline-studios --estimate PREPARED.hpgl\nWithout --device-config the server is preview only. Import and preparation never start a plot."
            );
            return Ok(());
        }
        let value = args
            .next()
            .with_context(|| format!("Missing value for {arg}"))?;
        match arg.as_str() {
            "--state" => root = PathBuf::from(value),
            "--listen" => listen = value,
            "--import" => import = Some(PathBuf::from(value)),
            "--name" => name = Some(value),
            "--units-per-mm" => units = value.parse()?,
            "--prepare" => prepare_input = Some(PathBuf::from(value)),
            "--estimate" => estimate_input = Some(PathBuf::from(value)),
            "--profile" => profile = Some(PathBuf::from(value)),
            "--output" => output = Some(PathBuf::from(value)),
            "--rotation" => rotation = value.parse()?,
            "--pens" => pens = value.split(',').map(str::parse).collect::<Result<_, _>>()?,
            "--device-config" => device_config = Some(PathBuf::from(value)),
            "--device-helper" => device_helper = Some(PathBuf::from(value)),
            _ => bail!("Unknown argument {arg}; use --help"),
        }
    }
    if let Some(path) = estimate_input {
        anyhow::ensure!(
            import.is_none() && prepare_input.is_none(),
            "Choose estimate, import or prepare"
        );
        anyhow::ensure!(
            path.metadata()?.len() <= store::MAX_BYTES as u64,
            "Source exceeds 64 MiB"
        );
        println!(
            "{}",
            serde_json::to_string_pretty(
                &timing::forecast(&storage::read_to_string(path)?)?.estimate
            )?
        );
        return Ok(());
    }
    if let Some(path) = prepare_input {
        anyhow::ensure!(import.is_none(), "Choose import or prepare");
        anyhow::ensure!(
            path.metadata()?.len() <= store::MAX_BYTES as u64,
            "Source exceeds 64 MiB"
        );
        let profile = profile.context("--profile is required for device preparation")?;
        let output = output.context("--output is required for device preparation")?;
        anyhow::ensure!(!output.exists(), "Output directory already exists");
        let prepared = prepare::prepare(
            &storage::read_to_string(path)?,
            units,
            serde_json::from_slice(&std::fs::read(profile)?)?,
            rotation,
            pens,
        )?;
        std::fs::create_dir_all(&output)?;
        std::fs::write(output.join("prepared.hpgl"), prepared.commands)?;
        std::fs::write(output.join("preview.svg"), prepared.svg)?;
        std::fs::write(
            output.join("plan.json"),
            serde_json::to_vec_pretty(&prepared.report)?,
        )?;
        println!("{}", serde_json::to_string_pretty(&prepared.report)?);
        return Ok(());
    }
    let store = store::Store::open(root)?;
    if let Some(path) = import {
        anyhow::ensure!(
            path.metadata()?.len() <= store::MAX_BYTES as u64,
            "File exceeds 64 MiB"
        );
        let name = name.unwrap_or_else(|| {
            path.file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned()
        });
        let job = store.import(&name, &storage::read(path)?, units)?;
        println!("{}", serde_json::to_string_pretty(&job)?);
        return Ok(());
    }
    documents::recover(&store)?;
    let listener = tokio::net::TcpListener::bind(&listen).await?;
    let plotter = device_config
        .map(|config| {
            plot::Plotter::open(
                store.root.join("plots"),
                config,
                device_helper,
                store.carousel.clone(),
            )
        })
        .transpose()?;
    eprintln!("\nPlotline Studios listening on http://{listen}");
    axum::serve(listener, web::router_with_plotter(store, plotter))
        .with_graceful_shutdown(async {
            let mut terminate =
                tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                    .expect("SIGTERM handler");
            tokio::select! { _=tokio::signal::ctrl_c()=>{}, _=terminate.recv()=>{} }
        })
        .await?;
    Ok(())
}
