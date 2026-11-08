//! Bounded recovery of narrow visible details and diagnostics of what remains.
//! Logical paint never changes. All recovered centerlines must fit the already
//! occlusion-clipped allowance with the same rounding guard as ordinary paths.
use crate::{geometry as g, model::*, plan::Paint};
use anyhow::Result;
use geos::{Geom, Geometry, GeometryTypes};
use rstar::RTree;
use std::collections::HashMap;

fn polygons<G: Geom>(geometry: &G) -> Result<Vec<Geometry>> {
    if geometry.is_empty()? {
        return Ok(vec![]);
    }
    match geometry.geometry_type()? {
        GeometryTypes::Polygon => Ok(vec![geometry.clone()?]),
        GeometryTypes::MultiPolygon | GeometryTypes::GeometryCollection => {
            let mut result = vec![];
            for i in 0..geometry.get_num_geometries()? {
                result.extend(polygons(&geometry.get_geometry_n(i)?)?);
            }
            Ok(result)
        }
        _ => Ok(vec![]),
    }
}

fn footprint(paths: &[Vec<Point>], radius: f64) -> Result<Geometry> {
    g::union(
        paths
            .iter()
            .map(|p| g::round_buffer(&g::line(p)?, radius))
            .collect::<Result<_>>()?,
    )
}

/// The parts a strict finite-width pen cannot reach. This excludes the broad
/// interior of a deliberately sparse hatch or outline fill.
fn narrow(visible: &Geometry, settings: Settings) -> Result<Geometry> {
    let radius = settings.pen_width / 2. + settings.guard();
    let core = g::round_buffer(visible, -radius)?;
    Ok(visible.difference(&g::round_buffer(&core, radius)?)?)
}

/// Approximate the medial graph from densely sampled polygon boundaries using
/// GEOS Voronoi edges. Opposing-site filtering removes sampling ribs; short
/// terminal branches remove corner whiskers, while retaining cycles and stems.
/// This is bounded vector approximation, not font substitution or raster OCR.
pub fn centerlines(geometry: &Geometry, settings: Settings) -> Result<Vec<Vec<Point>>> {
    let mut output = vec![];
    let step = (settings.pen_width / 12.).max(settings.flatten_tolerance * 2.);
    for polygon in polygons(geometry)? {
        let boundary = polygon.boundary()?;
        // Huge area features must not turn one detail control into unbounded
        // tessellation. Unrecovered pieces remain visible in diagnostics.
        if boundary.length()? / step > 40_000. || boundary.get_num_coordinates()? > 40_000 {
            continue;
        }
        let samples = boundary.densify(step)?;
        let mut sites: Vec<Point> = g::lines(&samples)?.into_iter().flatten().collect();
        sites.sort_by(|a, b| a[0].total_cmp(&b[0]).then(a[1].total_cmp(&b[1])));
        sites.dedup();
        if sites.len() < 3 {
            continue;
        }
        let tree = RTree::bulk_load(sites);
        let diagram = samples.voronoi(None::<&Geometry>, 0., true)?;
        let mut edges = vec![];
        for line in g::lines(&diagram)? {
            for pair in line.windows(2) {
                let mid = [
                    (pair[0][0] + pair[1][0]) / 2.,
                    (pair[0][1] + pair[1][1]) / 2.,
                ];
                let mut nearest = tree.nearest_neighbor_iter(mid);
                let a = nearest.next().unwrap();
                let b = nearest.next().unwrap();
                let u = [a[0] - mid[0], a[1] - mid[1]];
                let v = [b[0] - mid[0], b[1] - mid[1]];
                let lengths = u[0].hypot(u[1]) * v[0].hypot(v[1]);
                if lengths > 0. && u[0] * v[0] + u[1] * v[1] < lengths * 0.5 {
                    edges.push(g::line(pair)?);
                }
            }
        }
        if edges.is_empty() {
            continue;
        }
        let graph = g::union(edges)?.intersection(&polygon)?.line_merge()?;
        let mut lines = g::lines(&graph)?;
        // Voronoi vertices shared by multiple edges can differ in their final
        // floating-point bit after clipping. This key is only for degree counts.
        let key = |p: Point| ((p[0] * 1e8).round() as i64, (p[1] * 1e8).round() as i64);
        let mut degree = HashMap::new();
        for line in &lines {
            for p in [line[0], *line.last().unwrap()] {
                *degree.entry(key(p)).or_insert(0usize) += 1;
            }
        }
        lines.retain(|line| {
            let a = degree[&key(line[0])];
            let b = degree[&key(*line.last().unwrap())];
            let length: f64 = line
                .windows(2)
                .map(|p| (p[1][0] - p[0][0]).hypot(p[1][1] - p[0][1]))
                .sum();
            !((a == 1 && b > 2 || b == 1 && a > 2) && length < settings.pen_width * 0.5)
        });
        if !lines.is_empty() {
            let merged = g::union(
                lines
                    .iter()
                    .map(|line| g::line(line))
                    .collect::<Result<_>>()?,
            )?
            .line_merge()?;
            output.extend(g::lines(&merged.simplify(settings.flatten_tolerance)?)?);
        }
    }
    Ok(output)
}

pub fn recover(
    paint: &Paint,
    paths: &[Vec<Point>],
    allowed: &Geometry,
    settings: Settings,
) -> Result<Vec<Vec<Point>>> {
    let safe = g::centerline_inset(
        allowed,
        settings.pen_width / 2. + settings.guard(),
        settings.clearance_tolerance(),
    )?;
    recover_with_safe(paint, paths, &safe, settings)
}

/// The caller may reuse an identical inset of the recovery allowance. This
/// must be the recovery domain, not a stricter contour-fallback domain.
pub(crate) fn recover_with_safe(
    paint: &Paint,
    paths: &[Vec<Point>],
    safe: &Geometry,
    settings: Settings,
) -> Result<Vec<Vec<Point>>> {
    if safe.is_empty()? {
        return Ok(vec![]);
    }
    let radius = settings.pen_width / 2.;
    let ink = footprint(paths, radius)?;
    // Recovery only places centerlines outside this contracted existing ink.
    // If it already covers every safe centerline location, no residual can
    // produce another path. Prove that before constructing narrow slivers.
    let occupied = g::round_buffer(&ink, -radius * 0.2)?;
    if geos::PreparedGeometry::new(&occupied)?.covers(safe)? {
        return Ok(vec![]);
    }
    let residual = paint
        .visible
        .difference(&g::round_buffer(&ink, settings.guard() * 2.)?)?;
    if residual.area()? <= settings.guard().powi(2) {
        return Ok(vec![]);
    }
    let residual = residual.intersection(&narrow(&paint.visible, settings)?)?;
    if residual.area()? <= settings.guard().powi(2) {
        return Ok(vec![]);
    }
    let target = g::round_buffer(&residual, radius)?.intersection(safe)?;
    let target = target.difference(&occupied)?;
    if target.is_empty()? {
        return Ok(vec![]);
    }
    let mut result = vec![];
    if paint.is_stroke {
        // Prefer original centerlines, including dashes, whenever they survive.
        for line in &paint.source_lines {
            if line.len() < 2 {
                continue;
            }
            result.extend(g::lines(
                &g::line(line)?
                    .intersection(&paint.visible)?
                    .intersection(&target)?
                    .line_merge()?,
            )?);
        }
    }
    let represented = footprint(&result, radius)?;
    let remaining = residual.difference(&g::round_buffer(&represented, settings.guard() * 2.)?)?;
    if remaining.area()? > settings.guard().powi(2) {
        let target = target
            .intersection(&g::round_buffer(&remaining, radius)?)?
            .difference(&g::round_buffer(&represented, -radius * 0.2)?)?;
        // Restrict expensive medial work to the missing neighborhood. A broad
        // surviving body or a long clipped street must not be tessellated just
        // to investigate a small remnant at its end. The padding keeps the
        // artificial crop edges outside the candidate-centerline region.
        if target.is_empty()? {
            return Ok(result);
        }
        let support = paint
            .visible
            .intersection(&g::round_buffer(&target, settings.pen_width * 2.)?)?;
        for line in centerlines(&support, settings)? {
            result.extend(g::lines(
                &g::line(&line)?.intersection(&target)?.line_merge()?,
            )?);
        }
    }
    result.retain(|line| {
        line.len() > 1
            && line
                .windows(2)
                .map(|p| (p[1][0] - p[0][0]).hypot(p[1][1] - p[0][1]))
                .sum::<f64>()
                > settings.flatten_tolerance
    });
    Ok(result)
}

// Bounding a long curved remnant with one rectangle can highlight the whole
// page. Subdivide sparse rectangles until they follow the missing geometry.
fn local_bounds(geometry: &Geometry, size: f64, bounds: &mut Vec<[f64; 4]>) -> Result<()> {
    if geometry.is_empty()? {
        return Ok(());
    }
    let b = geometry.get_extent()?;
    let (x0, y0, x1, y1) = (b[0], b[1], b[2], b[3]);
    let (w, h) = (x1 - x0, y1 - y0);
    if w.max(h) <= size || w * h <= geometry.area()? * 4. {
        bounds.push([x0, y0, x1, y1]);
        return Ok(());
    }
    let rectangles = if w > h {
        let mid = (x0 + x1) / 2.;
        [[x0, y0, mid, y1], [mid, y0, x1, y1]]
    } else {
        let mid = (y0 + y1) / 2.;
        [[x0, y0, x1, mid], [x0, mid, x1, y1]]
    };
    for [a, b, c, d] in rectangles {
        for part in polygons(&geometry.intersection(&Geometry::create_rectangle(a, b, c, d)?)?)? {
            local_bounds(&part, size, bounds)?;
        }
    }
    Ok(())
}

/// Significant missing zones, before display dilation. This is an attention
/// aid, not every uncovered square point. Width approximation, short trimming
/// at cuts, and small texture remnants must not blanket a map with warnings.
pub fn missing(paint: &Paint, ink: &Geometry, settings: Settings) -> Result<Vec<[f64; 4]>> {
    if is_white(paint.color)
        || paint.visible.is_empty()?
        || (!paint.is_stroke && !paint.is_text && matches!(settings.fill_style, FillStyle::None))
    {
        return Ok(vec![]);
    }
    // Half a pen diameter of edge/end trimming is ordinary approximation.
    // A lost crossbar or stem still has a middle beyond this neighborhood.
    let residual = paint.visible.difference(&g::round_buffer(
        ink,
        (settings.pen_width * 0.5).max(settings.guard() * 2.),
    )?)?;
    if residual.area()? <= settings.guard().powi(2) {
        return Ok(vec![]);
    }
    let lost = if ink.is_empty()? {
        residual
    } else {
        residual.intersection(&narrow(&paint.visible, settings)?)?
    };
    // Map Studio's explicit source attribution identifies texture and labels;
    // ordinary PDF text has its native text role. Do not apply the coarser
    // texture threshold to all green ink or all thin strokes.
    let feature = paint.source.split('|').next().unwrap_or("");
    let text = paint.is_text || feature.starts_with("labels/");
    let texture = matches!(feature, "parks/explicit_hatch" | "terrain/hillshade");
    let min_span = settings.pen_width
        * if text {
            0.6
        } else if texture {
            5.
        } else {
            2.
        };
    let mut bounds = vec![];
    for part in polygons(&lost)? {
        if part.area()? > settings.guard().powi(2) {
            let b = part.get_extent()?;
            if !(text && ink.is_empty()?) && (b[2] - b[0]).max(b[3] - b[1]) < min_span {
                continue;
            }
            local_bounds(&part, settings.pen_width * 2., &mut bounds)?;
        }
    }
    Ok(bounds)
}
