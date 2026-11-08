use crate::{geometry as g, model::*};
use anyhow::{Context, Result, bail, ensure};
use geos::{Geom, Geometry};
use serde::Serialize;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

#[derive(Debug, Default, Serialize)]
pub struct CompileStats {
    pub coverage_seconds: f64,
    pub clip_seconds: f64,
    pub coverage_workers: usize,
    pub visibility_seconds: f64,
    pub planning_seconds: f64,
    pub planning_workers: usize,
    pub visibility_candidates: usize,
    pub visibility_shared_queries: usize,
    pub visibility_workers: usize,
    pub visibility_white_areas_deferred: usize,
    pub visibility_intersections: usize,
    pub visibility_components: usize,
    pub visibility_localized_overlays: usize,
    pub visibility_full_union_coordinates: usize,
    pub visibility_union_coordinates: usize,
    pub visibility_predicate_seconds: f64,
    pub visibility_overlay_seconds: f64,
    pub visibility_localize_seconds: f64,
    pub visibility_union_seconds: f64,
    pub visibility_difference_seconds: f64,
}

impl CompileStats {
    pub(crate) fn add_visibility(&mut self, other: &Self) {
        self.visibility_candidates += other.visibility_candidates;
        self.visibility_shared_queries += other.visibility_shared_queries;
        self.visibility_intersections += other.visibility_intersections;
        self.visibility_localized_overlays += other.visibility_localized_overlays;
        self.visibility_full_union_coordinates += other.visibility_full_union_coordinates;
        self.visibility_union_coordinates += other.visibility_union_coordinates;
        self.visibility_predicate_seconds += other.visibility_predicate_seconds;
        self.visibility_overlay_seconds += other.visibility_overlay_seconds;
        self.visibility_localize_seconds += other.visibility_localize_seconds;
        self.visibility_union_seconds += other.visibility_union_seconds;
        self.visibility_difference_seconds += other.visibility_difference_seconds;
    }
}

pub struct Paint {
    pub id: usize,
    pub source: String,
    pub color: Color,
    pub pen: Option<usize>,
    pub pen_width: f64,
    pub coverage: Geometry,
    pub visible: Geometry,
    pub(crate) visibility_resolved: bool,
    pub envelope: Geometry,
    pub allowed: Geometry,
    pub stroke_width: Option<f64>,
    // Page-space cap/join semantics; source_lines already have dashes expanded.
    pub stroke_style: Option<Stroke>,
    pub width_layout: Option<crate::width::Layout>,
    pub width_status: &'static str,
    pub source_lines: Vec<Vec<Point>>,
    pub is_stroke: bool,
    pub is_text: bool,
    pub is_raster: bool,
}

pub struct Plan {
    pub bounds: [f64; 4],
    pub paints: Vec<Paint>,
    pub palette: Vec<Color>,
    pub pen_widths: Vec<f64>,
    pub paths: Vec<PenPath>,
    pub settings: Settings,
    pub compile_stats: CompileStats,
}

#[derive(Debug, Clone, Serialize)]
pub struct PaintReport {
    pub id: usize,
    pub source: String,
    pub white: bool,
    pub source_color: Color,
    pub pen_color: Option<Color>,
    pub pen_id: Option<usize>,
    pub pen_width_pt: f64,
    /// Raster channels share the opaque image footprint; it is not solid ink demand.
    pub coverage_kind: &'static str,
    pub coverage_area_pt2: f64,
    // None only for white areas omitted by preview mode; never invented zeroes.
    pub visible_area_pt2: Option<f64>,
    // Raster channels are audited stroke by stroke; no costly union-area
    // diagnostic or solid-color demand is claimed for a screened image.
    pub ink_area_pt2: Option<f64>,
    pub ink_inside_visible_area_pt2: Option<f64>,
    pub unrepresented_area_pt2: Option<f64>,
    pub outside_visible_area_pt2: f64,
    pub outside_allowed_area_pt2: f64,
    pub uncovered_required_area_pt2: Option<f64>,
    pub width_layout: Option<crate::width::Layout>,
    pub width_status: &'static str,
    pub paths: usize,
    pub pen_down_mm: f64,
    pub incoming_pen_up_mm: f64,
    pub representation: &'static str,
    pub ink_fraction_of_visible_area: Option<f64>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub missing_detail_bounds: Vec<[f64; 4]>,
}

pub fn compile(page: &Page, settings: Settings) -> Result<Plan> {
    compile_with_workers(page, settings, None, true)
}

/// Keep complete paint-order occlusion and ink verification, but omit the
/// surviving white-area geometry needed only by full diagnostic reports/views.
pub fn compile_for_preview(page: &Page, settings: Settings) -> Result<Plan> {
    compile_with_workers(page, settings, None, false)
}

fn compile_with_workers(
    page: &Page,
    settings: Settings,
    worker_override: Option<usize>,
    include_white_visibility: bool,
) -> Result<Plan> {
    let start = Instant::now();
    ensure!(
        page.version == 1 && page.units == "pt" && page.y_axis == "down",
        "Unsupported event format/coordinate system"
    );
    ensure!(
        settings.pen_width.is_finite() && settings.pen_width > 0.0,
        "Invalid pen width"
    );
    ensure!(
        settings.flatten_tolerance.is_finite()
            && settings.flatten_tolerance > 0.0
            && settings.flatten_tolerance < settings.pen_width / 4.0,
        "Invalid flatten tolerance"
    );
    ensure!(
        settings.units_per_mm.is_finite() && settings.units_per_mm > 0.0,
        "Invalid HPGL simulation scale"
    );
    if let Some(spacing) = settings.hatch_spacing {
        ensure!(
            spacing.is_finite() && spacing > 0.0,
            "Invalid hatch spacing"
        );
    }
    if let Some(tolerance) = settings.stroke_width_tolerance {
        ensure!(
            tolerance.is_finite() && (0.0..=1.0).contains(&tolerance),
            "Stroke width tolerance must be between 0 and 1"
        );
    }
    ensure!(
        settings.thin_detail_thickening.is_finite()
            && (0.0..=1.0).contains(&settings.thin_detail_thickening),
        "Thin-detail thickening must be between 0 and 1 pen diameters"
    );
    let [x0, y0, x1, y1] = page.bounds;
    ensure!(
        page.bounds.iter().all(|v| v.is_finite() && v.abs() < 1e8) && x1 > x0 && y1 > y0,
        "Invalid page bounds"
    );
    ensure!(
        (x1 - x0).max(y1 - y0) * settings.units_per_pt() < i32::MAX as f64,
        "HPGL coordinates exceed simulation range"
    );
    let unsupported: Vec<_> = page
        .events
        .iter()
        .filter_map(|e| match e {
            Event::Unsupported { feature, detail } => Some(format!("{feature}: {detail}")),
            _ => None,
        })
        .collect();
    ensure!(
        unsupported.is_empty(),
        "PDF needs unsupported policies: {}",
        unsupported.join(", ")
    );
    ensure!(
        page.inks.len() <= 8,
        "At most eight document inks are supported"
    );
    let explicit = !page.inks.is_empty();
    let mut ids = std::collections::HashSet::new();
    for ink in &page.inks {
        ensure!(
            !ink.id.is_empty() && ids.insert(&ink.id),
            "Duplicate/empty ink ID"
        );
        ensure!(
            ink.width.is_finite() && ink.width > settings.flatten_tolerance * 4.,
            "Invalid ink diameter"
        );
        ensure!(
            !is_white(ink.color)
                && ink
                    .color
                    .iter()
                    .all(|c| c.is_finite() && (0.0..=1.0).contains(c)),
            "Invalid ink color"
        );
    }
    ensure!(
        explicit || page.paint_inks.is_empty(),
        "Assignments require a document palette"
    );
    let palette: Vec<Color> = if explicit {
        page.inks.iter().map(|ink| ink.color).collect()
    } else {
        match settings.palette_policy {
            PalettePolicy::Exact => Vec::new(),
            PalettePolicy::RedGreenBlack => vec![
                crate::palette::BLACK,
                crate::palette::RED,
                crate::palette::GREEN,
            ],
        }
    };
    let pen_widths: Vec<f64> = if explicit {
        page.inks.iter().map(|ink| ink.width).collect()
    } else {
        vec![settings.pen_width; palette.len()]
    };
    if let Some(angle) = settings.hatch_angle {
        ensure!(angle.is_finite(), "Invalid hatch angle");
    }
    if matches!(settings.fill_style, FillStyle::Feathered) {
        ensure!(
            settings
                .feather_distance
                .is_some_and(|d| d.is_finite() && d > 0.),
            "Feather distance must be positive"
        );
    }
    let page_clip = Geometry::create_rectangle(x0, y0, x1, y1)?;
    page_clip.get_extent()?;
    let mut clips = vec![Arc::new(page_clip)];
    let mut pending = Vec::new();
    let mut progress =
        crate::progress::Progress::new("clips", "Resolving PDF clips", page.events.len(), "events");
    for (id, event) in page.events.iter().enumerate() {
        progress.update(id);
        match event {
            Event::PopClip => {
                ensure!(clips.len() > 1, "Clip stack underflow at event {id}");
                clips.pop();
            }
            Event::ClipFill { .. } | Event::ClipStroke { .. } => {
                let (clip, _) = event_geometry(event, clips.last().unwrap(), id, settings)?;
                // Initialize GEOS's lazy envelope before sharing immutable clips.
                if !clip.is_empty()? {
                    clip.get_extent()?;
                }
                clips.push(Arc::new(clip));
            }
            Event::Fill { .. } | Event::Stroke { .. } | Event::RasterPaths { .. } => {
                pending.push((id, event, Arc::clone(clips.last().unwrap())));
            }
            _ => unreachable!(),
        }
    }
    progress.finish();
    ensure!(clips.len() == 1, "Unbalanced clip stack");
    let clip_seconds = start.elapsed().as_secs_f64();
    let workers = worker_override.unwrap_or_else(|| crate::work::workers(pending.len()));
    let mut progress = crate::progress::Progress::new(
        "coverage",
        "Building paint geometry",
        pending.len(),
        "paints",
    );
    let mut paints = crate::work::ordered_mut(
        &mut pending,
        workers,
        &mut progress,
        |_, (id, event, clip)| build_paint(page, *id, event, clip, &pen_widths, settings),
    )?;
    drop(pending);
    progress.finish();
    let assigned = paints
        .iter()
        .filter(|p| p.pen.is_some())
        .map(|p| p.id)
        .collect::<std::collections::BTreeSet<_>>();
    ensure!(
        !explicit || assigned == page.paint_inks.keys().copied().collect(),
        "Assignments include non-paint or white events"
    );
    let mut compile_stats = CompileStats {
        coverage_seconds: start.elapsed().as_secs_f64(),
        clip_seconds,
        coverage_workers: workers,
        ..CompileStats::default()
    };
    eprintln!(
        "Coverage: {} paints in {:.2}s",
        paints.len(),
        compile_stats.coverage_seconds
    );
    let start = Instant::now();
    crate::visibility::resolve(&mut paints, &mut compile_stats, include_white_visibility)?;
    compile_stats.visibility_seconds = start.elapsed().as_secs_f64();
    eprintln!(
        "Visibility: {:.2}s ({} candidates)",
        compile_stats.visibility_seconds, compile_stats.visibility_candidates
    );
    let start = Instant::now();
    let mut plan = Plan {
        bounds: page.bounds,
        paints,
        palette,
        pen_widths,
        paths: Vec::new(),
        settings,
        compile_stats,
    };
    let mut progress = crate::progress::Progress::new(
        "planning",
        "Choosing pen paths",
        plan.paints.len(),
        "paints",
    );
    let workers = worker_override.unwrap_or_else(|| {
        crate::work::workers(
            plan.paints
                .iter()
                .map(|p| crate::work::path_work(p.source_lines.iter().map(Vec::len)))
                .sum(),
        )
    });
    plan.compile_stats.planning_workers = workers;
    let results =
        crate::work::ordered_mut(&mut plan.paints, workers, &mut progress, |_, paint| {
            plan_paint(paint, page.bounds, settings)
        })?;
    for (paint, paths) in plan.paints.iter_mut().zip(results) {
        let Some(paths) = paths else { continue };
        let pen = if let Some(pen) = paint.pen {
            pen
        } else {
            let mapped = crate::palette::map(paint.color, settings.palette_policy);
            let pen = if let Some(i) = plan.palette.iter().position(|c| *c == mapped) {
                i + 1
            } else {
                ensure!(
                    plan.palette.len() < 8,
                    "More than eight colors: pen mapping needs a policy"
                );
                plan.palette.push(mapped);
                plan.pen_widths.push(settings.pen_width);
                plan.palette.len()
            };
            paint.pen = Some(pen);
            pen
        };
        plan.paths.extend(paths.into_iter().map(|points| PenPath {
            paint_id: paint.id,
            pen,
            points,
        }));
    }
    progress.finish();
    plan.compile_stats.planning_seconds = start.elapsed().as_secs_f64();
    eprintln!(
        "Planning: {} paths in {:.2}s",
        plan.paths.len(),
        plan.compile_stats.planning_seconds
    );
    Ok(plan)
}

fn event_geometry(
    event: &Event,
    clip: &Geometry,
    id: usize,
    settings: Settings,
) -> Result<(Geometry, Vec<Vec<Point>>)> {
    let (path, matrix, style, even_odd) = match event {
        Event::Fill {
            path,
            matrix,
            even_odd,
            ..
        } => (path, *matrix, None, *even_odd),
        Event::RasterPaths { path, matrix, .. } => (path, *matrix, None, false),
        Event::Stroke {
            path,
            matrix,
            style,
            ..
        } => (path, *matrix, Some(style), false),
        Event::ClipFill {
            path,
            matrix,
            even_odd,
        } => (path, *matrix, None, *even_odd),
        Event::ClipStroke {
            path,
            matrix,
            style,
            ..
        } => (path, *matrix, Some(style), false),
        _ => unreachable!(),
    };
    let paths = g::flatten(path, matrix, settings.flatten_tolerance)
        .with_context(|| format!("event {id}"))?;
    let (page_coverage, source_lines) = if let Some(style) = style {
        let (local, lines) =
            g::stroke(&paths, style).with_context(|| format!("stroke event {id}"))?;
        // Stroke width belongs to source coordinates, including nonuniform CTMs.
        (g::transform(&local, matrix)?, lines)
    } else {
        // Node fill contours AFTER transforming. Transforming already-polygonized
        // faces can collapse near-coincident edges through floating-point rounding
        // and produce an invalid shell/hole relationship (seen in a native glyph).
        let world: Vec<_> = paths
            .iter()
            .map(|line| line.iter().map(|p| g::map_point(*p, matrix)).collect())
            .collect();
        (
            if matches!(event, Event::ClipFill { .. }) {
                g::clip_fill(&world, even_odd)?
            } else {
                g::fill(&world, even_odd)?
            },
            Vec::new(),
        )
    };
    let coverage = page_coverage
        .intersection(clip)
        .with_context(|| format!("clipping coverage at event {id}"))?;
    g::validate(&coverage, &format!("coverage event {id}"))?;
    Ok((coverage, source_lines))
}

fn build_paint(
    page: &Page,
    id: usize,
    event: &Event,
    clip: &Geometry,
    pen_widths: &[f64],
    settings: Settings,
) -> Result<Paint> {
    let (matrix, style, color) = match event {
        Event::Fill { matrix, color, .. } | Event::RasterPaths { matrix, color, .. } => {
            (*matrix, None, *color)
        }
        Event::Stroke {
            matrix,
            style,
            color,
            ..
        } => (*matrix, Some(style), *color),
        _ => unreachable!(),
    };
    let explicit = !page.inks.is_empty();
    ensure!(
        color
            .iter()
            .all(|c| c.is_finite() && (0.0..=1.0).contains(c)),
        "Invalid color at event {id}"
    );
    let pen = if is_white(color) {
        None
    } else if explicit {
        let pen = *page
            .paint_inks
            .get(&id)
            .context("Missing document ink assignment")?;
        ensure!(
            (1..=pen_widths.len()).contains(&pen),
            "Invalid document ink assignment"
        );
        Some(pen)
    } else {
        None
    };
    let settings = Settings {
        pen_width: pen.map(|p| pen_widths[p - 1]).unwrap_or(settings.pen_width),
        ..settings
    };
    let use_pen = matches!(settings.stroke_width_mode, StrokeWidthMode::Pen)
        && style.is_some()
        && !is_white(color);
    let (coverage, source_lines) = if use_pen {
        let Event::Stroke { path, style, .. } = event else {
            unreachable!()
        };
        let flattened = g::flatten(path, matrix, settings.flatten_tolerance)?;
        // Expand dashes in the source coordinate system, then stroke their
        // page-space centerlines with the actual pen diameter, including shear.
        let lines = g::dashed(&flattened, style)?;
        let world: Vec<_> = lines
            .iter()
            .map(|line| line.iter().map(|p| g::map_point(*p, matrix)).collect())
            .collect();
        let pen_style = Stroke {
            width: settings.pen_width,
            dashes: Vec::new(),
            dash_phase: 0.,
            ..style.clone()
        };
        let (paint, _) = g::stroke(&world, &pen_style)?;
        (paint.intersection(clip)?, lines)
    } else {
        event_geometry(event, clip, id, settings)?
    };
    // A uniform scale/rotation/reflection has one page-space stroke width.
    // Anisotropic/sheared strokes retain the existing contour policy.
    let sx = matrix[0].hypot(matrix[1]);
    let sy = matrix[2].hypot(matrix[3]);
    let conformal = (sx - sy).abs() <= sx * 1e-6
        && (matrix[0] * matrix[2] + matrix[1] * matrix[3]).abs() <= sx * sy * 1e-6;
    let stroke_width = if use_pen {
        Some(settings.pen_width)
    } else {
        style.filter(|_| conformal).map(|s| s.width * sx)
    };
    let width_allowance = if stroke_width.is_some() {
        settings.stroke_width_tolerance.unwrap_or(0.)
    } else {
        0.
    };
    let is_raster = matches!(event, Event::RasterPaths { .. });
    let allowance = if is_raster {
        0.
    } else {
        width_allowance.max(settings.thin_detail_thickening)
    };
    let envelope = if allowance > 0. && !is_white(color) {
        g::round_buffer(&coverage, allowance * settings.pen_width / 2.0)?.intersection(clip)?
    } else {
        Geom::clone(&coverage)?
    };
    let source_lines = if let Event::RasterPaths { lines, .. } = event {
        ensure!(lines.iter().all(|l| l.len() >= 2 && l.iter().flatten().all(|v| v.is_finite() && v.abs() < 1e8)), "Invalid raster paths");
        lines.clone()
    } else {
        source_lines
    };
    Ok(Paint {
        id,
        source: page
            .sources
            .get(&id)
            .cloned()
            .unwrap_or_else(|| "unclassified".into()),
        color,
        pen,
        pen_width: settings.pen_width,
        coverage,
        visible: g::empty()?,
        visibility_resolved: true,
        envelope,
        allowed: g::empty()?,
        stroke_width,
        stroke_style: style.filter(|_| conformal || use_pen).map(|s| Stroke {
            width: if use_pen {
                settings.pen_width
            } else {
                s.width * sx
            },
            dashes: Vec::new(),
            dash_phase: 0.0,
            ..s.clone()
        }),
        width_layout: None,
        width_status: if settings.stroke_width_tolerance.is_none()
            || style.is_none()
            || is_white(color)
        {
            "not_requested"
        } else {
            "contour_fallback"
        },
        is_raster,
        is_stroke: style.is_some(),
        is_text: matches!(
            event,
            Event::Fill {
                role: PaintRole::Text,
                ..
            }
        ),
        source_lines: source_lines
            .iter()
            .map(|line| line.iter().map(|p| g::map_point(*p, matrix)).collect())
            .collect(),
    })
}

fn plan_paint(
    paint: &mut Paint,
    bounds: [f64; 4],
    settings: Settings,
) -> Result<Option<Vec<Vec<Point>>>> {
    let settings = Settings {
        pen_width: paint.pen_width,
        ..settings
    };
    if paint.is_raster
        || settings.thin_detail_thickening == 0.
        || is_white(paint.color)
        || paint.visible.is_empty()?
        || (!paint.is_stroke && !paint.is_text && matches!(settings.fill_style, FillStyle::None))
    {
        return plan_paint_standard(paint, bounds, settings, None);
    }
    // Visibility already clipped the maximum of the regular-width and
    // recovery envelopes against the original clip, sheet and later paint.
    // Only intersect again when this particular policy needs a smaller one.
    let regular_allowance = if paint.stroke_width.is_some() {
        settings.stroke_width_tolerance.unwrap_or(0.)
    } else {
        0.
    };
    let recovery_allowed = if settings.thin_detail_thickening >= regular_allowance {
        Geom::clone(&paint.allowed)?
    } else {
        paint.allowed.intersection(&g::round_buffer(
            &paint.coverage,
            settings.thin_detail_thickening * settings.pen_width / 2.,
        )?)?
    };
    if regular_allowance < settings.thin_detail_thickening {
        paint.allowed = paint.allowed.intersection(&g::round_buffer(
            &paint.coverage,
            regular_allowance * settings.pen_width / 2.,
        )?)?;
    }
    // Equal allowances have the exact same eligible centerline area. Eroding
    // detailed, occlusion-cut outlines is expensive; compute it once for both
    // ordinary planning and recovery. Keep this snapshot even if the standard
    // planner falls back to strict visible coverage and changes paint.allowed.
    let shared_safe = if regular_allowance == settings.thin_detail_thickening {
        Some(g::centerline_inset(
            &recovery_allowed,
            settings.pen_width / 2. + settings.guard(),
            settings.clearance_tolerance(),
        )?)
    } else {
        None
    };
    let mut paths =
        plan_paint_standard(paint, bounds, settings, shared_safe.as_ref())?.unwrap_or_default();
    let extra = if let Some(safe) = &shared_safe {
        crate::thin::recover_with_safe(paint, &paths, safe, settings)?
    } else {
        crate::thin::recover(paint, &paths, &recovery_allowed, settings)?
    };
    if !extra.is_empty() {
        paint.allowed = paint.allowed.union(&recovery_allowed)?;
        paint.width_status = "thin_detail_recovery";
        paths.extend(extra);
    }
    Ok(Some(paths))
}

fn plan_paint_standard(
    paint: &mut Paint,
    bounds: [f64; 4],
    settings: Settings,
    safe: Option<&Geometry>,
) -> Result<Option<Vec<Vec<Point>>>> {
    let [x0, y0, _, _] = bounds;
    let settings = Settings {
        pen_width: paint.pen_width,
        ..settings
    };
    if is_white(paint.color) || paint.visible.is_empty()? {
        return Ok(None);
    }
    let computed_safe;
    let safe = if let Some(safe) = safe {
        safe
    } else {
        computed_safe = g::centerline_inset(
            &paint.allowed,
            settings.pen_width / 2.0 + settings.guard(),
            settings.clearance_tolerance(),
        )?;
        &computed_safe
    };
    if paint.is_raster {
        let prepared = geos::PreparedGeometry::new(safe)?;
        let mut paths = Vec::new();
        for line in &paint.source_lines {
            let geometry = g::line(line)?;
            if prepared.covers(&geometry)? {
                paths.push(line.clone());
            } else if prepared.intersects(&geometry)? {
                paths.extend(g::lines(&geometry.intersection(safe)?)?);
            }
        }
        paint.width_status = "raster_paths";
        return Ok(Some(paths));
    }
    if !paint.is_stroke && !paint.is_text && matches!(settings.fill_style, FillStyle::None) {
        return Ok(None);
    }
    let mut paths_out = Vec::new();
    if paint.is_stroke && matches!(settings.stroke_width_mode, StrokeWidthMode::Pen) {
        // One centerline traversal per surviving run. No offsets or contour
        // fallback that would silently turn this policy into multiple passes.
        for source in &paint.source_lines {
            if source.len() > 1 {
                let clipped = g::line(source)?.intersection(safe)?.line_merge()?;
                paths_out.extend(g::lines(&clipped)?);
            }
        }
        paint.width_status = "pen_width";
        return Ok(Some(paths_out));
    }
    if let Some(tolerance) = settings.stroke_width_tolerance
        && let Some(width) = paint.stroke_width
        && let Some(layout) = crate::width::layout(width, settings.pen_width, tolerance)?
        && let Some((paths, selected_layout)) = width_paths(paint, &layout, bounds, settings, safe)?
    {
        paint.width_layout = Some(selected_layout);
        paint.width_status = "width_tolerance";
        for points in paths {
            paths_out.push(points);
        }
        return Ok(Some(paths_out));
    }
    if safe.is_empty()? {
        return Ok(None);
    }
    // Fallback retains strict visible-area containment, including casings
    // whose original source centerline has been removed by a white core.
    paint.allowed = Geom::clone(&paint.visible)?;
    let inset = settings.pen_width / 2.0 + settings.guard();
    let strict_safe;
    let safe = if settings.stroke_width_tolerance.is_none() || paint.stroke_width.is_none() {
        safe
    } else {
        strict_safe = g::round_buffer(&paint.visible, -inset)?;
        &strict_safe
    };
    if safe.is_empty()? {
        return Ok(Some(paths_out));
    }
    let mut candidates = Vec::new();
    // Work from final visible coverage: surviving white-road casings have
    // their own inner boundary, no longer aligned to the original offsets.
    if paint.is_stroke || paint.is_text || matches!(settings.fill_style, FillStyle::Solid) {
        for source in &paint.source_lines {
            if source.len() > 1 {
                candidates.push(g::line(source)?.intersection(safe)?.line_merge()?);
            }
        }
        let mut distance = inset + settings.flatten_tolerance / 10.0;
        let mut finished = false;
        let mut had_inner = false;
        for _ in 0..10_000 {
            let inner = g::round_buffer(&paint.visible, -distance)?;
            if inner.is_empty()? {
                // A whole pass interval can jump over the last narrow strip.
                // Try its midpoint before declaring the interior covered.
                if had_inner {
                    let residual =
                        g::round_buffer(&paint.visible, -(distance - settings.pen_width * 0.45))?;
                    if !residual.is_empty()? {
                        candidates.push(residual.boundary()?);
                    }
                }
                finished = true;
                break;
            }
            candidates.push(inner.boundary()?);
            had_inner = true;
            distance += settings.pen_width * 0.9;
        }
        ensure!(finished, "Contour pass count exceeds probe limit");
    } else {
        let inner = g::round_buffer(&paint.visible, -(inset + settings.flatten_tolerance / 10.0))?;
        if !inner.is_empty()? && !matches!(settings.fill_style, FillStyle::Feathered) {
            candidates.push(inner.boundary()?);
        }
        let min_y = safe.get_y_min()?;
        let max_y = safe.get_y_max()?;
        let min_x = safe.get_x_min()?;
        let max_x = safe.get_x_max()?;
        let cross = matches!(
            settings.fill_style,
            FillStyle::Crosshatch | FillStyle::Feathered
        );
        let step = settings
            .hatch_spacing
            .unwrap_or(settings.pen_width * if cross { 12.0 } else { 3.0 });
        let first = settings.hatch_angle.unwrap_or(if cross { 45. } else { 0. });
        let angles = if matches!(settings.fill_style, FillStyle::Outline) {
            vec![]
        } else if cross {
            vec![first, first + 90.]
        } else {
            vec![first]
        };
        let feather_cores = if matches!(settings.fill_style, FillStyle::Feathered) {
            let distance = settings.feather_distance.unwrap();
            vec![
                g::round_buffer(&paint.coverage, -distance)?,
                g::round_buffer(&paint.coverage, -distance / 2.)?,
            ]
        } else {
            Vec::new()
        };
        for angle in angles {
            let (sin, cos) = angle.to_radians().sin_cos();
            let corners = [
                [min_x - x0, min_y - y0],
                [min_x - x0, max_y - y0],
                [max_x - x0, min_y - y0],
                [max_x - x0, max_y - y0],
            ];
            let along: Vec<_> = corners.iter().map(|p| cos * p[0] + sin * p[1]).collect();
            let across: Vec<_> = corners.iter().map(|p| -sin * p[0] + cos * p[1]).collect();
            let lo = along.iter().copied().fold(f64::INFINITY, f64::min) - step;
            let hi = along.iter().copied().fold(f64::NEG_INFINITY, f64::max) + step;
            let low = across.iter().copied().fold(f64::INFINITY, f64::min);
            let high = across.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            ensure!(
                (high - low) / step < 100_000.,
                "Hatch count exceeds probe limit"
            );
            let mut index = (low / step).ceil() as i64;
            while (index as f64) * step < high {
                let offset = index as f64 * step;
                let mut candidate = g::line(&[
                    [x0 + cos * lo - sin * offset, y0 + sin * lo + cos * offset],
                    [x0 + cos * hi - sin * offset, y0 + sin * hi + cos * offset],
                ])?;
                if !feather_cores.is_empty() {
                    candidate =
                        candidate.difference(&feather_cores[index.rem_euclid(2) as usize])?;
                }
                candidates.push(candidate);
                index += 1;
            }
        }
    }
    // Clip each complete candidate separately. Unioning all linework nodes
    // every hatch/outline intersection and causes needless pen lifts.
    for candidate in candidates {
        let clipped = candidate.intersection(safe)?.line_merge()?;
        for points in g::lines(&clipped)? {
            if points.len() >= 2 && g::line(&points)?.length()? > settings.flatten_tolerance {
                paths_out.push(points);
            }
        }
    }
    Ok(Some(paths_out))
}

// Try nominal source offsets, then locally adjust a single traversal at cuts.
// Accept only a complete, quantized approximation of the width-adjusted interior.
// Erased source centerlines and unsupported layouts retain contour recovery.
type WidthPaths = (Vec<Vec<Point>>, crate::width::Layout);

// Narrow the source stroke, rather than eroding its unioned outline: erosion
// leaves extra wedges at inside corners that a legitimate thinner pen misses.
// The visible-area erosion additionally permits retreat at actual paint/clip
// cuts. Neither operation changes the hard allowed-ink region.
fn width_required(paint: &Paint, settings: Settings) -> Result<Geometry> {
    let margin =
        settings.stroke_width_tolerance.unwrap() * settings.pen_width / 2.0 + settings.guard();
    let radius = paint.stroke_width.unwrap() / 2.0 - margin;
    if radius <= 0.0 {
        return g::empty();
    }
    let mut style = paint.stroke_style.as_ref().unwrap().clone();
    style.width = radius * 2.0;
    let (narrowed, _) = g::stroke(&paint.source_lines, &style)?;
    Ok(narrowed.intersection(&g::round_buffer(&paint.visible, -margin)?)?)
}

// Move only the pieces that fail clearance toward the nearest safe centerline
// position. GEOS supplies proximity and clipping; unchanged straight runs keep
// their original vertices instead of becoming a dense, stair-stepped polyline.
fn locally_adjusted_lines(
    sources: &[Geometry],
    safe: &Geometry,
    max_shift: f64,
    step: f64,
) -> Result<Vec<Vec<Point>>> {
    fn visit(
        a: Point,
        b: Point,
        safe: &Geometry,
        max_shift: f64,
        step: f64,
        output: &mut Vec<Option<Point>>,
        budget: &mut usize,
    ) -> Result<()> {
        if *budget == 0 {
            anyhow::bail!("Local stroke adjustment exceeds subdivision limit");
        }
        *budget -= 1;
        let segment = g::line(&[a, b])?;
        if safe.covers(&segment)? {
            output.extend([Some(a), Some(b)]);
        } else if segment.distance(safe)? > max_shift {
            output.push(None);
        } else if (b[0] - a[0]).hypot(b[1] - a[1]) > step {
            let m = [(a[0] + b[0]) / 2., (a[1] + b[1]) / 2.];
            visit(a, m, safe, max_shift, step, output, budget)?;
            visit(m, b, safe, max_shift, step, output, budget)?;
        } else {
            for p in [a, b] {
                let point = Geometry::create_point(geos::CoordSeq::new_from_vec(&[p])?)?;
                let nearest = point.nearest_points(safe)?;
                let q = [nearest.get_x(1)?, nearest.get_y(1)?];
                output.push(((q[0] - p[0]).hypot(q[1] - p[1]) <= max_shift).then_some(q));
            }
        }
        Ok(())
    }
    let mut result = Vec::new();
    let mut budget = 100_000;
    for source in sources {
        let mut sampled = Vec::new();
        for line in g::lines(source)? {
            for pair in line.windows(2) {
                visit(
                    pair[0],
                    pair[1],
                    safe,
                    max_shift,
                    step,
                    &mut sampled,
                    &mut budget,
                )?;
            }
            sampled.push(None);
        }
        let mut run = Vec::new();
        for point in sampled {
            if let Some(p) = point {
                if run.last() != Some(&p) {
                    run.push(p);
                }
            } else {
                if run.len() >= 2 {
                    result.extend(g::lines(&g::line(&run)?.intersection(safe)?.line_merge()?)?);
                }
                run.clear();
            }
        }
    }
    Ok(result)
}

// A conservative cut can round its end cap just short of required ink. Search
// the eight adjacent HPGL grid positions instead of retracing the entire paint.
// Only open endpoints move, by at most one grid cell in each axis. Every trial
// checks the complete incident path's footprint and the remaining required ink.
// This bounded search may fail; it never weakens coverage or occlusion checks.
fn adjust_cut_endpoints(
    mut paths: Vec<Vec<Point>>,
    allowed: &Geometry,
    required: &Geometry,
    bounds: [f64; 4],
    settings: Settings,
) -> Result<Option<Vec<Vec<Point>>>> {
    let radius = settings.pen_width / 2.;
    let quantum = 1. / settings.units_per_pt();
    let mut footprints = paths
        .iter()
        .map(|p| g::round_buffer(&g::line(p)?, radius))
        .collect::<Result<Vec<_>>>()?;
    let mut missing = required.difference(&g::union(
        footprints
            .iter()
            .map(Geom::clone)
            .collect::<Result<_, _>>()?,
    )?)?;
    let mut budget = 256;
    for i in 0..paths.len() {
        if paths[i].first() == paths[i].last() {
            continue;
        }
        let other = g::union(
            footprints
                .iter()
                .enumerate()
                .filter(|(j, _)| *j != i)
                .map(|(_, p)| Geom::clone(p))
                .collect::<Result<_, _>>()?,
        )?;
        let needed = required.difference(&other)?;
        for at_start in [true, false] {
            let j = if at_start { 0 } else { paths[i].len() - 1 };
            let p = paths[i][j];
            let point = Geometry::create_point(geos::CoordSeq::new_from_vec(&[p])?)?;
            if point.distance(&missing)? > radius + quantum * std::f64::consts::SQRT_2 {
                continue;
            }
            let mut best = None;
            let mut best_area = missing.area()?;
            // Prefer a single-axis step when equally good candidates exist.
            for [dx, dy] in [
                [0., -1.],
                [-1., 0.],
                [1., 0.],
                [0., 1.],
                [-1., -1.],
                [-1., 1.],
                [1., -1.],
                [1., 1.],
            ] {
                if budget == 0 {
                    return Ok(None);
                }
                budget -= 1;
                let mut candidate = paths[i].clone();
                candidate[j] = [p[0] + dx * quantum, p[1] + dy * quantum];
                let candidate = crate::hpgl::quantize_points(&candidate, bounds, settings);
                if candidate.len() < 2 {
                    continue;
                }
                let ink = g::round_buffer(&g::line(&candidate)?, radius)?;
                if ink.difference(allowed)?.area()? > 1e-7 {
                    continue;
                }
                let remainder = needed.difference(&ink)?;
                let area = remainder.area()?;
                if area < best_area {
                    best_area = area;
                    best = Some((candidate, ink, remainder));
                    if area <= 1e-7 {
                        break;
                    }
                }
            }
            if let Some((candidate, ink, remainder)) = best {
                paths[i] = candidate;
                footprints[i] = ink;
                missing = remainder;
                if best_area <= 1e-7 {
                    let all_ink = g::union(
                        footprints
                            .iter()
                            .map(Geom::clone)
                            .collect::<Result<_, _>>()?,
                    )?;
                    if all_ink.difference(allowed)?.area()? <= 1e-7 {
                        return Ok(Some(paths));
                    }
                    return Ok(None);
                }
            }
        }
    }
    Ok(None)
}

fn width_paths(
    paint: &Paint,
    ideal: &crate::width::Layout,
    bounds: [f64; 4],
    settings: Settings,
    safe: &Geometry,
) -> Result<Option<WidthPaths>> {
    let radius = settings.pen_width / 2.0;
    let tolerance = settings.stroke_width_tolerance.unwrap();
    let max_shift = ideal.requested_width_pt / 2.0 + tolerance * radius - radius;
    // At the one-pass tolerance limit the full guard can erase whole runs.
    // Smaller candidate clearance still requires exact quantized-ink checks.
    let narrow_safe;
    let safe = if ideal.passes == 1 && max_shift < settings.guard() {
        narrow_safe = g::centerline_inset(
            &paint.allowed,
            radius + max_shift.max(0.) / 2.,
            settings.clearance_tolerance(),
        )?;
        &narrow_safe
    } else {
        safe
    };
    let sources: Vec<_> = paint
        .source_lines
        .iter()
        .filter(|p| p.len() >= 2)
        .map(|p| g::line(p))
        .collect::<Result<_>>()?;
    // A conservative rounding guard can erase the candidate corridor even
    // though the quantized pen fits. Single-pass layouts still get the smaller
    // clearance attempts below, with exact footprint verification. Multi-pass
    // layouts whose source is erased retain contour recovery for casing bands.
    let mut source_survives = false;
    for line in &sources {
        if !line.intersection(safe)?.is_empty()? {
            source_survives = true;
            break;
        }
    }
    if !source_survives && ideal.passes != 1 {
        return Ok(None);
    }
    // Reject interior holes, including gaps between rounded HPGL tracks, using
    // the same narrower source-stroke contract as the independent path verifier.
    let required = width_required(paint, settings)?;
    let quantum = 1.0 / settings.units_per_pt();
    for passes in ideal.passes..=(ideal.passes + 3).min(10_000) {
        if !source_survives {
            break;
        }
        let span = if passes == 1 {
            settings.pen_width
        } else {
            ideal
                .requested_width_pt
                .min(passes as f64 * settings.pen_width)
        };
        let nominal_spacing = if passes == 1 {
            0.0
        } else {
            (span - settings.pen_width) / (passes - 1) as f64
        };
        // Touching tracks can separate on the integer grid. First try the ideal
        // spacing; only then reduce spacing or add passes, within the same bounds.
        for adjustment in [0.0, quantum / 4.0, quantum / 2.0, quantum, quantum * 1.5] {
            if passes == 1 && adjustment != 0.0 {
                break;
            }
            let spacing = nominal_spacing - adjustment;
            if passes > 1
                && (spacing <= 0.0
                    || settings.pen_width - spacing
                        > (1.0 - tolerance) * settings.pen_width + settings.pen_width * 1e-6)
            {
                continue;
            }
            let planned_span = settings.pen_width + (passes - 1) as f64 * spacing;
            if (ideal.requested_width_pt - planned_span).abs()
                > tolerance * settings.pen_width + settings.pen_width * 1e-6
            {
                continue;
            }
            let mut result = Vec::new();
            for source in &sources {
                // One buffered boundary supplies a symmetric offset pair, including
                // round end caps. Odd pass counts also use the original centerline.
                for i in 0..passes.div_ceil(2) {
                    let offset = if passes.is_multiple_of(2) {
                        (i as f64 + 0.5) * spacing
                    } else {
                        i as f64 * spacing
                    };
                    let candidate = if offset == 0.0 {
                        Geom::clone(source)?
                    } else {
                        g::round_buffer(source, offset)?.boundary()?
                    };
                    for points in g::lines(&candidate.intersection(safe)?.line_merge()?)? {
                        let points = crate::hpgl::quantize_points(&points, bounds, settings);
                        if points.len() >= 2 {
                            result.push(points);
                        }
                    }
                }
            }
            if result.is_empty() {
                continue;
            }
            let ink = g::union(
                result
                    .iter()
                    .map(|p| g::round_buffer(&g::line(p)?, radius))
                    .collect::<Result<_>>()?,
            )?;
            if ink.difference(&paint.allowed)?.area()? <= 1e-7
                && required.difference(&ink)?.area()? <= 1e-7
            {
                let mut selected = ideal.clone();
                selected.passes = passes;
                selected.planned_spacing_pt = spacing;
                selected.planned_span_pt = planned_span;
                return Ok(Some((result, selected)));
            }
        }
    }
    if ideal.passes == 1 {
        // This is a replacement traversal, not extra overlapping contour passes.
        // Try conservative clearance first, then let exact quantized-footprint
        // verification decide whether a smaller retreat is sufficient at cuts.
        let mut endpoint_candidates = Vec::new();
        for clearance in [
            settings.guard(),
            settings.guard() / 2.,
            // Half a guard can leave a rounded cut endpoint outside the
            // allowed paint, while a full guard erases the whole corridor.
            // Keep a quarter of the available shift for a bounded middle try.
            (settings.guard() * 0.75).min(max_shift.max(0.) * 0.75),
            settings.flatten_tolerance,
        ] {
            let safe = g::round_buffer(&paint.allowed, -(radius + clearance))?;
            if safe.is_empty()? {
                continue;
            }
            let paths: Vec<_> = locally_adjusted_lines(&sources, &safe, max_shift, radius / 2.)?
                .into_iter()
                .map(|p| crate::hpgl::quantize_points(&p, bounds, settings))
                .filter(|p| p.len() >= 2)
                .collect();
            if paths.is_empty() {
                continue;
            }
            let ink = g::union(
                paths
                    .iter()
                    .map(|p| g::round_buffer(&g::line(p)?, radius))
                    .collect::<Result<_>>()?,
            )?;
            if ink.difference(&paint.allowed)?.area()? > 1e-7 {
                continue;
            }
            if required.difference(&ink)?.area()? <= 1e-7 {
                let mut selected = ideal.clone();
                selected.locally_adjusted = true;
                return Ok(Some((paths, selected)));
            }
            endpoint_candidates.push(paths);
        }
        for paths in endpoint_candidates {
            if let Some(paths) =
                adjust_cut_endpoints(paths, &paint.allowed, &required, bounds, settings)?
            {
                let mut selected = ideal.clone();
                selected.locally_adjusted = true;
                return Ok(Some((paths, selected)));
            }
        }
    }
    Ok(None)
}

pub fn resolve_visibility(paints: &mut [Paint], stats: &mut CompileStats) -> Result<()> {
    crate::visibility::resolve(paints, stats, true)
}

impl Plan {
    pub fn verify_paths(&self, paths: &[PenPath]) -> Result<Vec<PaintReport>> {
        self.verify_paths_with_progress(paths, "Checking pen footprints")
    }

    pub fn verify_paths_with_progress(
        &self,
        paths: &[PenPath],
        label: &'static str,
    ) -> Result<Vec<PaintReport>> {
        Ok(self.verify_paths_impl(paths, label, None, None)?.0)
    }

    /// Verify both independently obtained path sets. Reuse geometric results only
    /// for paints whose full source/pen/coordinate sequences are exactly equal.
    /// Changed or quantized paths still undergo all geometric checks. Incoming
    /// travel is always recalculated, even when a paint's own paths did not change.
    pub fn verify_path_pair(
        &self,
        planned: &[PenPath],
        decoded: &[PenPath],
    ) -> Result<(Vec<PaintReport>, Vec<PaintReport>, usize)> {
        let (a, _) =
            self.verify_paths_impl(planned, "Checking planned pen footprints", None, None)?;
        let (b, reused) = self.verify_paths_impl(
            decoded,
            "Checking decoded HPGL footprints",
            Some((planned, &a)),
            None,
        )?;
        Ok((a, b, reused))
    }

    fn verify_paths_impl(
        &self,
        paths: &[PenPath],
        label: &'static str,
        reuse: Option<(&[PenPath], &[PaintReport])>,
        worker_override: Option<usize>,
    ) -> Result<(Vec<PaintReport>, usize)> {
        let mut progress =
            crate::progress::Progress::new("verification", label, self.paints.len(), "paints");
        let paint_indices: HashMap<_, _> = self
            .paints
            .iter()
            .enumerate()
            .map(|(i, p)| (p.id, i))
            .collect();
        let mut grouped = vec![Vec::new(); self.paints.len()];
        let mut incoming = vec![0.0; self.paints.len()];
        let mut position = [self.bounds[0], self.bounds[3]];
        for path in paths {
            let index = *paint_indices
                .get(&path.paint_id)
                .context("Path has unknown source paint")?;
            let paint = &self.paints[index];
            ensure!(
                !is_white(paint.color) && paint.pen == Some(path.pen),
                "Incorrect pen mapping"
            );
            grouped[index].push(path);
            if let (Some(first), Some(last)) = (path.points.first(), path.points.last()) {
                incoming[index] +=
                    (first[0] - position[0]).hypot(first[1] - position[1]) * 25.4 / 72.0;
                position = *last;
            }
        }
        let mut previous = vec![Vec::new(); self.paints.len()];
        if let Some((checked, _)) = reuse {
            for path in checked {
                previous[paint_indices[&path.paint_id]].push(path);
            }
        }
        let workers = worker_override.unwrap_or_else(|| {
            let work = grouped
                .iter()
                .zip(&previous)
                .filter(|(a, b)| reuse.is_none() || a != b)
                .map(|(paths, _)| crate::work::path_work(paths.iter().map(|p| p.points.len())))
                .sum();
            crate::work::workers(work)
        });
        let results = crate::work::ordered_mut(
            &mut grouped,
            workers,
            &mut progress,
            |index, selected| {
                let paint = &self.paints[index];
                ensure!(
                    paint.visibility_resolved || (is_white(paint.color) && selected.is_empty()),
                    "Missing drawable paint visibility"
                );
                if let Some((_, reports)) = reuse
                    && *selected == previous[index]
                {
                    let mut row = reports[index].clone();
                    row.incoming_pen_up_mm = incoming[index];
                    return Ok((row, true));
                }
                let settings = Settings {
                    pen_width: paint.pen_width,
                    ..self.settings
                };
                let (ink, outside, outside_allowed) = if paint.is_raster {
                    // A union cannot escape if every constituent footprint is
                    // contained. Keep the finite-pen audit independent of the
                    // centerline clipping pass without globally unioning screens.
                    let prepared = geos::PreparedGeometry::new(&paint.allowed)?;
                    let mut outside_sum = 0.;
                    for path in selected.iter() {
                        let footprint =
                            g::round_buffer(&g::line(&path.points)?, settings.pen_width / 2.)?;
                        let outside = if prepared.covers(&footprint)? {
                            0.
                        } else {
                            footprint.difference(&paint.allowed)?.area()?
                        };
                        outside_sum += outside;
                        ensure!(
                            outside_sum <= 1e-7,
                            "Raster paint {} pen footprint escapes by {outside} pt²",
                            paint.id
                        );
                    }
                    (None, outside_sum, outside_sum)
                } else {
                    let ink = g::union(
                        selected
                            .iter()
                            .map(|p| g::round_buffer(&g::line(&p.points)?, settings.pen_width / 2.))
                            .collect::<Result<_>>()?,
                    )?;
                    let outside = ink.difference(&paint.visible)?.area()?;
                    let outside_allowed = ink.difference(&paint.allowed)?.area()?;
                    (Some(ink), outside, outside_allowed)
                };
                let uncovered_required = if paint.width_layout.is_some() {
                    ensure!(
                        !selected.is_empty(),
                        "Width approximation lost all paths for paint {}",
                        paint.id
                    );
                    let required = width_required(paint, settings)?;
                    let missing = required
                        .difference(ink.as_ref().context("Missing stroke footprint")?)?
                        .area()?;
                    ensure!(
                        missing <= 1e-7,
                        "Paint {} width approximation leaves {missing} pt² of required interior unpainted",
                        paint.id
                    );
                    Some(missing)
                } else {
                    None
                };
                // This is a numerical assertion against the flattened coverage model,
                // not a mechanical tolerance or a proof about arbitrary PDF curves.
                if outside_allowed > 1e-7 {
                    bail!(
                        "Paint {} pen footprint escapes allowed region by {outside_allowed} pt²",
                        paint.id
                    );
                }
                let visible_area = paint.visible.area()?;
                let ink_inside_visible_area = ink
                    .as_ref()
                    .map(|ink| ink.intersection(&paint.visible)?.area())
                    .transpose()?;
                Ok((
                    PaintReport {
                        id: paint.id,
                        source: paint.source.clone(),
                        white: is_white(paint.color),
                        source_color: paint.color,
                        pen_color: if is_white(paint.color) {
                            None
                        } else {
                            Some(paint.pen.map(|p| self.palette[p - 1]).unwrap_or_else(|| {
                                crate::palette::map(paint.color, self.settings.palette_policy)
                            }))
                        },
                        pen_id: paint.pen,
                        pen_width_pt: paint.pen_width,
                        coverage_kind: if paint.is_raster {
                            "image_footprint"
                        } else {
                            "pdf_paint"
                        },
                        coverage_area_pt2: paint.coverage.area()?,
                        visible_area_pt2: paint.visibility_resolved.then_some(visible_area),
                        ink_area_pt2: ink.as_ref().map(|ink| ink.area()).transpose()?,
                        ink_inside_visible_area_pt2: ink_inside_visible_area,
                        unrepresented_area_pt2: if is_white(paint.color) {
                            Some(0.0)
                        } else {
                            ink_inside_visible_area.map(|area| (visible_area - area).max(0.))
                        },
                        outside_visible_area_pt2: outside,
                        outside_allowed_area_pt2: outside_allowed,
                        uncovered_required_area_pt2: uncovered_required,
                        width_layout: paint.width_layout.clone(),
                        width_status: paint.width_status,
                        missing_detail_bounds: if !paint.is_raster
                            && visible_area - ink_inside_visible_area.unwrap_or(0.)
                                > settings.guard().powi(2)
                        {
                            crate::thin::missing(
                                paint,
                                ink.as_ref().context("Missing vector footprint")?,
                                settings,
                            )?
                        } else {
                            Vec::new()
                        },
                        paths: selected.len(),
                        pen_down_mm: selected
                            .iter()
                            .flat_map(|p| p.points.windows(2))
                            .map(|p| (p[1][0] - p[0][0]).hypot(p[1][1] - p[0][1]) * 25.4 / 72.0)
                            .sum(),
                        incoming_pen_up_mm: incoming[paint_indices[&paint.id]],
                        representation: if is_white(paint.color) {
                            "occlusion_only"
                        } else if paint.is_raster {
                            "raster_paths"
                        } else if paint.width_layout.is_some() {
                            "stroke_width_tolerance"
                        } else if paint.is_stroke {
                            "stroke_contours"
                        } else if paint.is_text {
                            "glyph_contours"
                        } else if matches!(self.settings.fill_style, FillStyle::None) {
                            "no_fill_ink"
                        } else if matches!(self.settings.fill_style, FillStyle::Outline) {
                            "fill_outline"
                        } else if matches!(self.settings.fill_style, FillStyle::Feathered) {
                            "feathered"
                        } else if matches!(self.settings.fill_style, FillStyle::Solid) {
                            "solid_contours"
                        } else if matches!(self.settings.fill_style, FillStyle::Crosshatch) {
                            "crosshatch"
                        } else {
                            "hatch"
                        },
                        ink_fraction_of_visible_area: if is_white(paint.color)
                            || visible_area == 0.0
                        {
                            None
                        } else {
                            ink_inside_visible_area.map(|area| area / visible_area)
                        },
                    },
                    false,
                ))
            },
        )?;
        let reused = results.iter().filter(|(_, reused)| *reused).count();
        let report = results.into_iter().map(|(row, _)| row).collect();
        progress.finish();
        Ok((report, reused))
    }
}

#[cfg(test)]
mod tests;
