use crate::model::{Command, Matrix, Point, Stroke};
use anyhow::{Context, Result, bail, ensure};
use geos::{BufferParams, CapStyle, CoordSeq, Geom, Geometry, GeometryTypes, JoinStyle};
use kurbo::{BezPath, PathEl};

pub const QUADRANTS: i32 = 32;

pub fn empty() -> Result<Geometry> {
    Ok(Geometry::create_empty_polygon()?)
}

pub fn line(points: &[Point]) -> Result<Geometry> {
    ensure!(points.len() >= 2, "Line requires two points");
    Ok(Geometry::create_line_string(CoordSeq::new_from_vec(
        points,
    )?)?)
}

pub fn union(geoms: Vec<Geometry>) -> Result<Geometry> {
    if geoms.is_empty() {
        return empty();
    }
    Ok(Geometry::create_geometry_collection(geoms)?.unary_union()?)
}

pub fn transform(geometry: &Geometry, m: Matrix) -> Result<Geometry> {
    Ok(geometry.transform_xy(|x, y| {
        Ok::<_, geos::Error>((m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]))
    })?)
}

pub fn map_point(p: Point, m: Matrix) -> Point {
    [
        m[0] * p[0] + m[2] * p[1] + m[4],
        m[1] * p[0] + m[3] * p[1] + m[5],
    ]
}

fn check_point(p: Point) -> Result<()> {
    ensure!(
        p.iter().all(|x| x.is_finite() && x.abs() < 1e8),
        "Coordinate outside finite probe range"
    );
    Ok(())
}

pub fn flatten(commands: &[Command], m: Matrix, tolerance: f64) -> Result<Vec<Vec<Point>>> {
    ensure!(m.iter().all(|x| x.is_finite()), "Non-finite transform");
    ensure!(
        (m[0] * m[3] - m[1] * m[2]).abs() > 1e-12,
        "Singular transform is not supported yet"
    );
    let scale_bound = (m[0] * m[0] + m[1] * m[1] + m[2] * m[2] + m[3] * m[3]).sqrt();
    ensure!(scale_bound < 1e6, "Transform exceeds probe range");
    let mut path = BezPath::new();
    let mut active = false;
    for command in commands {
        match *command {
            Command::Move { p } => {
                check_point(p)?;
                path.move_to((p[0], p[1]));
                active = true;
            }
            Command::Line { p } => {
                ensure!(active, "Line without move");
                check_point(p)?;
                path.line_to((p[0], p[1]));
            }
            Command::Cubic { p1, p2, p3 } => {
                ensure!(active, "Curve without move");
                for p in [p1, p2, p3] {
                    check_point(p)?;
                }
                path.curve_to((p1[0], p1[1]), (p2[0], p2[1]), (p3[0], p3[1]));
            }
            Command::Close => {
                ensure!(active, "Close without move");
                path.close_path();
                active = false;
            }
        }
    }
    let mut paths: Vec<Vec<Point>> = Vec::new();
    let mut drawn = Vec::new();
    kurbo::flatten(
        path.elements().iter().copied(),
        tolerance / scale_bound,
        |el| match el {
            PathEl::MoveTo(p) => {
                paths.push(vec![[p.x, p.y]]);
                drawn.push(false);
            }
            PathEl::LineTo(p) => {
                *drawn.last_mut().unwrap() = true;
                let sub = paths.last_mut().expect("kurbo move before line");
                if sub.last() != Some(&[p.x, p.y]) {
                    sub.push([p.x, p.y]);
                }
            }
            PathEl::ClosePath => {
                *drawn.last_mut().unwrap() = true;
                let sub = paths.last_mut().expect("kurbo move before close");
                if sub.last() != sub.first() {
                    sub.push(sub[0]);
                }
            }
            _ => unreachable!("flatten emits lines"),
        },
    );
    ensure!(
        paths.iter().map(Vec::len).sum::<usize>() < 1_000_000,
        "Flattened path exceeds probe limit"
    );
    // A lone moveto paints nothing; a zero-length line can paint a round dot.
    Ok(paths
        .into_iter()
        .zip(drawn)
        .filter_map(|(path, drawn)| drawn.then_some(path))
        .collect())
}

// A single simple ring bounds the same filled region under either PDF fill
// rule. Complex/invalid rings still need the general winding/polygonizer path.
pub fn clip_fill(paths: &[Vec<Point>], even_odd: bool) -> Result<Geometry> {
    if let [path] = paths
        && path.len() >= 3
    {
        let mut ring = path.clone();
        if ring.last() != ring.first() {
            ring.push(ring[0]);
        }
        if ring.len() < 4 {
            return fill(paths, even_odd);
        }
        let polygon = Geometry::create_polygon(
            Geometry::create_linear_ring(CoordSeq::new_from_vec(&ring)?)?,
            vec![],
        )?;
        if polygon.is_valid()? {
            return Ok(polygon);
        }
    }
    fill(paths, even_odd)
}

// PDF fill rules are not OGC polygon rules. GEOS nodes and polygonizes the
// linework; winding numbers against the ORIGINAL contours select its faces.
pub fn fill(paths: &[Vec<Point>], even_odd: bool) -> Result<Geometry> {
    let mut rings = Vec::new();
    let mut edges = Vec::new();
    for path in paths {
        if path.len() < 3 {
            continue;
        }
        let mut ring = path.clone();
        if ring.last() != ring.first() {
            ring.push(ring[0]);
        }
        edges.push(line(&ring)?);
        rings.push(ring);
    }
    if edges.is_empty() {
        return empty();
    }
    let noded = union(edges)?;
    let faces = Geometry::polygonize(&[noded])?;
    let mut selected = Vec::new();
    for i in 0..faces.get_num_geometries()? {
        let face = faces.get_geometry_n(i)?;
        let sample = face.point_on_surface()?;
        let p = [sample.get_x()?, sample.get_y()?];
        let winding: i32 = rings.iter().map(|r| winding_number(r, p)).sum();
        if if even_odd {
            winding % 2 != 0
        } else {
            winding != 0
        } {
            selected.push(Geom::clone(&face)?);
        }
    }
    let result = union(selected)?;
    ensure!(
        result.is_valid()?,
        "Invalid fill: {}",
        result.is_valid_reason()?
    );
    Ok(result)
}

fn winding_number(ring: &[Point], p: Point) -> i32 {
    let mut count = 0;
    for edge in ring.windows(2) {
        let [a, b] = [edge[0], edge[1]];
        let cross = (b[0] - a[0]) * (p[1] - a[1]) - (p[0] - a[0]) * (b[1] - a[1]);
        if a[1] <= p[1] && b[1] > p[1] && cross > 0.0 {
            count += 1;
        }
        if a[1] > p[1] && b[1] <= p[1] && cross < 0.0 {
            count -= 1;
        }
    }
    count
}

pub fn dashed(paths: &[Vec<Point>], style: &Stroke) -> Result<Vec<Vec<Point>>> {
    ensure!(style.dash_phase.is_finite(), "Non-finite dash phase");
    if style.dashes.is_empty() {
        return Ok(paths.to_vec());
    }
    ensure!(
        style.dashes.iter().all(|d| d.is_finite() && *d > 0.0),
        "Zero/negative dash entries are not supported yet"
    );
    let mut pattern = style.dashes.clone();
    if pattern.len() % 2 == 1 {
        pattern.extend(style.dashes.iter().copied());
    }
    let period: f64 = pattern.iter().sum();
    ensure!(
        period.is_finite() && period > 1e-8,
        "Dash period outside probe range"
    );
    let mut result = Vec::new();
    for path in paths {
        if path.len() < 2 {
            bail!("Zero-length dashed subpaths need a policy");
        }
        let mut phase = style.dash_phase.rem_euclid(period);
        let mut idx = 0;
        while phase >= pattern[idx] {
            phase -= pattern[idx];
            idx = (idx + 1) % pattern.len();
        }
        let mut remaining = pattern[idx] - phase;
        let mut current = Vec::new();
        let mut pieces = Vec::new();
        for edge in path.windows(2) {
            let [a, b] = [edge[0], edge[1]];
            let length = (b[0] - a[0]).hypot(b[1] - a[1]);
            if length == 0.0 {
                continue;
            }
            let mut along = 0.0;
            while along < length {
                let step = remaining.min(length - along);
                let at = |d: f64| {
                    [
                        a[0] + (b[0] - a[0]) * d / length,
                        a[1] + (b[1] - a[1]) * d / length,
                    ]
                };
                if idx % 2 == 0 {
                    if current.is_empty() {
                        current.push(at(along));
                    }
                    current.push(at(along + step));
                }
                along += step;
                remaining -= step;
                if remaining <= 1e-10 {
                    if !current.is_empty() {
                        pieces.push(std::mem::take(&mut current));
                    }
                    idx = (idx + 1) % pattern.len();
                    remaining = pattern[idx];
                }
                ensure!(pieces.len() < 100_000, "Dash count exceeds probe limit");
            }
        }
        if !current.is_empty() {
            pieces.push(current);
        }
        // A dash continuing across a closed subpath's seam has a join, not caps.
        if pieces.len() > 1
            && path.first() == path.last()
            && pieces.first().and_then(|p| p.first()) == path.first()
            && pieces.last().and_then(|p| p.last()) == path.last()
        {
            let first = pieces.remove(0);
            let last = pieces.last_mut().unwrap();
            last.extend(first.into_iter().skip(1));
        }
        result.extend(pieces);
    }
    Ok(result)
}

pub fn stroke(paths: &[Vec<Point>], style: &Stroke) -> Result<(Geometry, Vec<Vec<Point>>)> {
    ensure!(
        style.width.is_finite() && style.width > 0.0,
        "Hairline/invalid stroke width requires a policy"
    );
    ensure!(
        style.miter_limit.is_finite() && style.miter_limit >= 1.0,
        "Invalid miter limit"
    );
    let cap = match style.cap.as_str() {
        "Butt" => CapStyle::Flat,
        "Round" => CapStyle::Round,
        "Square" => CapStyle::Square,
        _ => bail!("Unsupported cap {}", style.cap),
    };
    let join = match style.join.as_str() {
        "Miter" => JoinStyle::Mitre,
        "Round" => JoinStyle::Round,
        "Bevel" => JoinStyle::Bevel,
        _ => bail!("Unsupported join {}", style.join),
    };
    let params = BufferParams::builder()
        .end_cap_style(cap)
        .join_style(join)
        .mitre_limit(style.miter_limit)
        .quadrant_segments(QUADRANTS)
        .build()?;
    let paths = dashed(paths, style)?;
    let mut coverage = Vec::new();
    for path in &paths {
        if path.is_empty() {
            continue;
        }
        if path.len() == 1 {
            match cap {
                CapStyle::Round => coverage.push(
                    Geometry::create_point(CoordSeq::new_from_vec(path)?)?
                        .buffer_with_params(style.width / 2.0, &params)?,
                ),
                CapStyle::Flat => {}
                _ => bail!("Directionless square-capped stroke needs a policy"),
            }
            continue;
        }
        if style.join == "Miter" {
            let cuts = over_limit_miters(path, style.miter_limit);
            if !cuts.is_empty() {
                // PDF switches to a bevel at an over-limit corner; GEOS instead
                // truncates the miter. Start with bevels, then add GEOS miter
                // buffers for runs whose internal corners are all in limit.
                let bevel = BufferParams::builder()
                    .end_cap_style(cap)
                    .join_style(JoinStyle::Bevel)
                    .quadrant_segments(QUADRANTS)
                    .build()?;
                coverage.push(line(path)?.buffer_with_params(style.width / 2.0, &bevel)?);
                let miter = BufferParams::builder()
                    .end_cap_style(CapStyle::Flat)
                    .join_style(JoinStyle::Mitre)
                    .mitre_limit(style.miter_limit)
                    .quadrant_segments(QUADRANTS)
                    .build()?;
                for run in split_at_corners(path, &cuts) {
                    coverage.push(line(&run)?.buffer_with_params(style.width / 2.0, &miter)?);
                }
                continue;
            }
        }
        coverage.push(line(path)?.buffer_with_params(style.width / 2.0, &params)?);
    }
    Ok((union(coverage)?, paths))
}

fn over_limit_miters(path: &[Point], limit: f64) -> Vec<usize> {
    let closed = path.first() == path.last();
    let n = path.len() - usize::from(closed);
    let mut cuts = Vec::new();
    for i in 0..n {
        if !closed && (i == 0 || i == n - 1) {
            continue;
        }
        let [a, b, c] = [path[(i + n - 1) % n], path[i], path[(i + 1) % n]];
        let u = [b[0] - a[0], b[1] - a[1]];
        let v = [c[0] - b[0], c[1] - b[1]];
        let denominator = u[0].hypot(u[1]) * v[0].hypot(v[1]);
        if denominator == 0.0 {
            continue;
        }
        let dot = ((u[0] * v[0] + u[1] * v[1]) / denominator).clamp(-1.0, 1.0);
        let ratio = (2.0 / (1.0 + dot)).sqrt();
        if ratio > limit {
            cuts.push(i);
        }
    }
    cuts
}

fn split_at_corners(path: &[Point], cuts: &[usize]) -> Vec<Vec<Point>> {
    let closed = path.first() == path.last();
    let n = path.len() - usize::from(closed);
    let first = if closed { cuts[0] } else { 0 };
    let end = if closed { first + n } else { n - 1 };
    let mut runs = Vec::new();
    let mut run = vec![path[first]];
    for j in first + 1..=end {
        let i = j % n;
        run.push(path[i]);
        if j == end || cuts.binary_search(&i).is_ok() {
            runs.push(std::mem::replace(&mut run, vec![path[i]]));
        }
    }
    runs
}

/// Approximate only the temporary candidate boundary, at the caller's physical
/// precision budget. Original allowed ink remains authoritative. A zero budget
/// retains exact input vertices; failed simplification/erosion retries them.
pub(crate) fn centerline_inset(
    geometry: &Geometry,
    radius: f64,
    tolerance: f64,
) -> Result<Geometry> {
    ensure!(
        radius.is_finite() && radius > 0.,
        "Invalid pen-clearance radius"
    );
    ensure!(
        tolerance.is_finite() && tolerance >= 0.,
        "Invalid clearance precision"
    );
    if tolerance > 0.
        && geometry.get_num_coordinates()? >= 256
        && geometry.is_valid()?
        && let Ok(simplified) = geometry.topology_preserve_simplify(tolerance)
        && simplified.is_valid().unwrap_or(false)
        && let Ok(inset) = component_inset(&simplified, radius)
    {
        return Ok(inset);
    }
    component_inset(geometry, radius)
}

// Disconnected valid polygons remain disjoint under erosion. Positive buffers
// cannot use this decomposition; keep the original round joins/quadrant count.
fn component_inset(geometry: &Geometry, radius: f64) -> Result<Geometry> {
    if geometry.geometry_type()? != GeometryTypes::MultiPolygon
        || geometry.get_num_coordinates()? < 256
        || !geometry.is_valid()?
    {
        return round_buffer(geometry, -radius);
    }
    let mut output = Vec::new();
    for i in 0..geometry.get_num_geometries()? {
        let inset = geometry.get_geometry_n(i)?.buffer(-radius, QUADRANTS)?;
        if inset.is_empty()? {
            continue;
        }
        if inset.geometry_type()? == GeometryTypes::Polygon {
            output.push(inset);
        } else {
            for j in 0..inset.get_num_geometries()? {
                output.push(Geom::clone(&inset.get_geometry_n(j)?)?);
            }
        }
    }
    Ok(match output.len() {
        0 => empty()?,
        1 => output.pop().unwrap(),
        _ => Geometry::create_multipolygon(output)?,
    })
}

pub fn round_buffer(geometry: &Geometry, distance: f64) -> Result<Geometry> {
    Ok(geometry.buffer(distance, QUADRANTS)?)
}

pub fn lines<G: Geom>(geometry: &G) -> Result<Vec<Vec<Point>>> {
    if geometry.is_empty()? {
        return Ok(Vec::new());
    }
    match geometry.geometry_type()? {
        GeometryTypes::LineString | GeometryTypes::LinearRing => {
            let seq = geometry.get_coord_seq()?;
            let points = (0..seq.size()?)
                .map(|i| Ok([seq.get_x(i)?, seq.get_y(i)?]))
                .collect::<Result<Vec<_>>>()?;
            Ok(vec![points])
        }
        GeometryTypes::MultiLineString | GeometryTypes::GeometryCollection => {
            let mut output = Vec::new();
            for i in 0..geometry.get_num_geometries()? {
                output.extend(lines(&geometry.get_geometry_n(i)?)?);
            }
            Ok(output)
        }
        GeometryTypes::Point | GeometryTypes::MultiPoint => Ok(Vec::new()),
        other => bail!("Expected linework, got {other:?}"),
    }
}

pub fn validate(geometry: &Geometry, context: &str) -> Result<()> {
    ensure!(
        geometry.is_valid().with_context(|| context.to_owned())?,
        "{context}: {}",
        geometry.is_valid_reason()?
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn component_insets_match_whole_buffer_with_holes_splits_and_disappearing_parts() -> Result<()>
    {
        let point = Geometry::new_from_wkt("POINT (0 0)")?;
        let ring = round_buffer(&point, 8.)?.difference(&round_buffer(&point, 6.)?)?;
        // An island inside a hole, point-touching squares, a narrow neck that
        // splits when eroded, and a tiny island that disappears.
        let shapes = [
            "POLYGON ((-1 -1,1 -1,1 1,-1 1,-1 -1))",
            "POLYGON ((20 0,24 0,24 4,20 4,20 0))",
            "POLYGON ((24 4,28 4,28 8,24 8,24 4))",
            "POLYGON ((40 0,44 0,44 1.9,46 1.9,46 0,50 0,50 4,46 4,46 2.1,44 2.1,44 4,40 4,40 0))",
            "POLYGON ((60 0,60.1 0,60.1 0.1,60 0.1,60 0))",
        ];
        let mut parts = vec![ring];
        for wkt in shapes {
            parts.push(Geometry::new_from_wkt(wkt)?);
        }
        let shape = Geometry::create_multipolygon(parts)?;
        assert!(shape.is_valid()? && shape.get_num_coordinates()? >= 256);
        for angle in [0_f64, 0.37, 1.2] {
            let (s, c) = angle.sin_cos();
            let input = transform(&shape, [c, s, -s, c, 1337.123, 854.789])?;
            for radius in [0.03, 0.2, 0.75, 2., 9.] {
                let mut expected = round_buffer(&input, -radius)?;
                let mut actual = centerline_inset(&input, radius, 0.)?;
                expected.normalize()?;
                actual.normalize()?;
                assert!(actual.is_valid()?);
                assert!(
                    actual.equals_exact(&expected, 0.)?,
                    "angle={angle}, radius={radius}"
                );
            }
        }
        Ok(())
    }

    #[test]
    fn invalid_overlapping_components_retain_whole_buffer_repair() -> Result<()> {
        let a = Geometry::new_from_wkt("POINT (0 0)")?.buffer(5., 64)?;
        let b = Geometry::new_from_wkt("POINT (1 0)")?.buffer(5., 64)?;
        let invalid = Geometry::create_multipolygon(vec![a, b])?;
        assert!(!invalid.is_valid()?);
        assert!(
            centerline_inset(&invalid, 0.5, 0.001)?
                .equals_exact(&round_buffer(&invalid, -0.5)?, 0.)?
        );
        Ok(())
    }

    #[test]
    fn bounded_clearance_reduces_dense_curves_without_spending_the_pen_guard() -> Result<()> {
        let point = Geometry::new_from_wkt("POINT (0 0)")?;
        let input = point
            .buffer(10., 512)?
            .difference(&point.buffer(4., 512)?)?;
        let settings = crate::model::Settings::default();
        let tolerance = settings.clearance_tolerance();
        let radius = settings.pen_width / 2.;
        let exact = centerline_inset(&input, radius + settings.guard(), 0.)?;
        let candidate = centerline_inset(&input, radius + settings.guard(), tolerance)?;
        assert!(candidate.is_valid()?);
        assert!(candidate.get_num_coordinates()? * 2 < exact.get_num_coordinates()?);
        // This stable ring fixture exercises both an outer boundary and a hole.
        // The general policy bounds input simplification, not output topology.
        assert!(
            candidate
                .difference(&round_buffer(&exact, tolerance * 1.01)?)?
                .area()?
                < 1e-7
        );
        assert!(
            exact
                .difference(&round_buffer(&candidate, tolerance * 1.01)?)?
                .area()?
                < 1e-7
        );
        assert!(
            round_buffer(&candidate, radius)?
                .difference(&input)?
                .area()?
                < 1e-7
        );
        // A small glyph-like polygon keeps every vertex, irrespective of budget.
        let small = Geometry::new_from_wkt("POLYGON ((0 0,2 0,2 4,3 4,3 5,0 5,0 0))")?;
        assert!(
            centerline_inset(&small, 0.1, tolerance)?
                .equals_exact(&round_buffer(&small, -0.1)?, 0.)?
        );
        Ok(())
    }
}
