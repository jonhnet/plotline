use anyhow::{Context, Result, ensure};
use plotline::{
    continuity,
    model::{FillStyle, Page, PalettePolicy, Settings},
    output, plan, route, summary,
};
use std::{fs, path::PathBuf};

// Include native GEOS allocations, not only Rust's global allocator. The
// dependency's override feature exports the same allocator to C/C++ malloc.
#[cfg(all(target_os = "linux", target_env = "gnu"))]
#[global_allocator]
static ALLOCATOR: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;

fn run() -> Result<()> {
    let mut args: Vec<_> = std::env::args().skip(1).collect();
    if args.first().is_some_and(|a| a == "--help") {
        println!(
            "Usage: plotline INPUT.events.json OUTPUT_DIRECTORY [PEN_WIDTH_PT=0.8] [SIMULATION_UNITS_PER_MM=40] [hatch|crosshatch|solid] [exact|red-green-black] [HATCH_SPACING_PT] [strict|WIDTH_TOLERANCE_0_TO_1]\nOr: plotline INPUT.events.json OUTPUT_DIRECTORY --settings SETTINGS.json (document inks and fill controls).\nAppend --artifacts full|preview (default full). Preview writes HPGL, coverage/decoded SVGs and summary; all verification remains enabled.\nOffline PDF geometry conversion; no plotter connection. See README.md for supported PDF operations."
        );
        return Ok(());
    }
    let mut full = true;
    if let Some(index) = args.iter().position(|a| a == "--artifacts") {
        ensure!(
            index >= 2 && index + 1 < args.len(),
            "Use --artifacts full|preview after input/output"
        );
        full = match args[index + 1].as_str() {
            "full" => true,
            "preview" => false,
            _ => anyhow::bail!("Unknown artifact mode"),
        };
        args.drain(index..=index + 1);
        ensure!(
            !args.iter().any(|a| a == "--artifacts"),
            "Repeated --artifacts"
        );
    }
    ensure!(
        (2..=8).contains(&args.len()),
        "Usage: plotline INPUT.events.json OUTPUT_DIRECTORY [PEN_WIDTH_PT] [SIMULATION_UNITS_PER_MM] [hatch|crosshatch|solid] [exact|red-green-black] [HATCH_SPACING_PT] [strict|WIDTH_TOLERANCE_0_TO_1]"
    );
    let mut settings = Settings::default();
    if args.get(2).is_some_and(|s| s == "--settings") {
        ensure!(args.len() == 4, "Use --settings FILE");
        settings = serde_json::from_slice(&fs::read(&args[3])?)?;
    } else {
        if let Some(width) = args.get(2) {
            settings.pen_width = width.parse()?;
        }
        if let Some(palette) = args.get(5) {
            settings.palette_policy = match palette.as_str() {
                "exact" => PalettePolicy::Exact,
                "red-green-black" => PalettePolicy::RedGreenBlack,
                _ => anyhow::bail!("Unknown palette policy: {palette}"),
            };
        }
        if let Some(scale) = args.get(3) {
            settings.units_per_mm = scale.parse()?;
        }
        if let Some(style) = args.get(4) {
            settings.fill_style = match style.as_str() {
                "hatch" => FillStyle::Hatch,
                "crosshatch" => FillStyle::Crosshatch,
                "solid" => FillStyle::Solid,
                _ => anyhow::bail!("Unknown fill style: {style}"),
            };
        }
        if let Some(spacing) = args.get(6) {
            settings.hatch_spacing = Some(spacing.parse()?);
        }
        if let Some(tolerance) = args.get(7)
            && tolerance != "strict"
        {
            settings.stroke_width_tolerance = Some(tolerance.parse()?);
        }
    }
    let mut progress = plotline::progress::Progress::new("reading", "Reading page geometry", 0, "");
    let input = fs::read_to_string(&args[0]).context("reading event input")?;
    let page: Page = serde_json::from_str(&input).context("parsing event input")?;
    let input = full.then_some(input);
    progress.finish();
    let mut plan = if full {
        plan::compile(&page, settings)?
    } else {
        plan::compile_for_preview(&page, settings)?
    };
    let mut progress = plotline::progress::Progress::new("routing", "Ordering pen paths", 0, "");
    let routing_start = std::time::Instant::now();
    let routing = route::optimize(&mut plan)?;
    let encoded = continuity::encode(&plan)?;
    let routing_seconds = routing_start.elapsed().as_secs_f64();
    eprintln!("Routing: {routing_seconds:.2}s");
    progress.finish();
    let verification_start = std::time::Instant::now();
    let (decoded, metrics) = encoded.readback(&plan)?;
    let (planned_report, decoded_report, reused_paint_checks) =
        plan.verify_path_pair(&plan.paths, &encoded.source_paths)?;
    let verification_seconds = verification_start.elapsed().as_secs_f64();
    eprintln!("Verification: {verification_seconds:.2}s");
    let geos = geos::version()?;
    let allocator = if cfg!(all(target_os = "linux", target_env = "gnu")) {
        "jemalloc"
    } else {
        "system"
    };
    let timing = serde_json::json!({"allocator":allocator,"compile":plan.compile_stats,"routing_seconds":routing_seconds,"verification_seconds":verification_seconds,"reused_paint_checks":reused_paint_checks});
    let validation_definitions = serde_json::json!({
        "interpretation": "tools/check.py compares the original PDF raster ONLY to coverage.svg (solid paint areas), not to plotted ink.",
        "containment": "Decoded footprints must remain inside their allowed region to 1e-7 pt^2. Width-tolerant strokes and optional thin-detail recovery may expand by half their configured total-width allowance, clipped by original clips and all later logical paints. Excess outside original visible coverage is reported separately.",
        "representation": "Ink fractions measure visible area covered. Hatching intentionally leaves gaps; quality gates are fixture- and policy-specific.",
        "serialization": "Independent HPGL readback must reproduce quantized coordinates and pen assignments exactly. Source paths retain paint attribution; continuous physical runs include separately checked connectors.",
        "continuity": "Only adjacent same-ink strokes with overlapping round end caps may connect. The complete quantized connector footprint must fit the union of the two source paints' allowed regions to 1e-7 pt^2. Per-paint coverage reports describe original strokes; continuity records attribute added mileage to both source paints."
    });
    let mut totals = summary::summarize(&decoded_report, &page.inks)?;
    summary::add_continuity(&mut totals, &encoded.report, &decoded_report, &page.inks)?;
    let mut progress = plotline::progress::Progress::new(
        "artifacts",
        "Marking lost details and writing previews",
        0,
        "",
    );
    let (detail_loss, detail_loss_summary) = output::detail_loss(&plan, &decoded_report)?;
    let summary = serde_json::json!({
        "schema":"plotline.conversion-summary/1", "engine":page.engine,"geos":geos,
        "settings":settings,"palette":plan.palette,"pen_widths_pt":plan.pen_widths,"document_inks":page.inks,
        "metrics":metrics,"routing":routing,"continuity":encoded.report,"timing":timing,
        "by_ink":totals.by_ink,"by_feature":totals.by_feature,
        "visible_paints_without_paths":totals.visible_paints_without_paths,
        "detail_loss":detail_loss_summary,
        "verification":{"planned":summary::verification(&planned_report),"decoded_hpgl":summary::verification(&decoded_report)},
        "validation_definitions":validation_definitions,
    });
    let mut artifacts = vec![
        ("plot.hpgl", encoded.commands),
        ("coverage.svg", output::svg(&plan, None, false)?),
        ("hpgl.svg", output::svg(&plan, Some(&decoded), false)?),
        ("detail-loss.svg", detail_loss),
        (
            "summary.json",
            serde_json::to_string_pretty(&summary)? + "\n",
        ),
    ];
    if full {
        let report = serde_json::json!({
            "engine":page.engine,"geos":geos,"settings":settings,
            "scope":"Opaque vector experiment with adapter-supplied RGB colors. Model verification, not device calibration.",
            "palette":plan.palette,"pen_widths_pt":plan.pen_widths,"document_inks":page.inks,"planned":planned_report,"decoded_hpgl":decoded_report,"metrics":metrics,"routing":routing,"continuity":encoded.report,
            "timing":timing,"validation_definitions":validation_definitions,
        });
        artifacts.extend([
            ("input.events.json", input.unwrap()),
            ("visible.svg", output::svg(&plan, None, true)?),
            ("planned.svg", output::svg(&plan, Some(&plan.paths), false)?),
            ("comparison.svg", output::comparison(&plan, &decoded)?),
            ("report.json", serde_json::to_string_pretty(&report)? + "\n"),
        ]);
    }
    let out = PathBuf::from(&args[1]);
    // Do all geometry and verification before publishing any generated artifacts.
    fs::create_dir_all(&out)?;
    for (name, body) in artifacts {
        fs::write(out.join(name), body)?;
    }
    progress.finish();
    println!(
        "{} paint events, {} pens, {} decoded paths; footprint checks passed.\nArtifacts: {}",
        plan.paints.len(),
        plan.palette.len(),
        decoded.len(),
        out.display()
    );
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("plotline: {error:#}");
        std::process::exit(1);
    }
}
