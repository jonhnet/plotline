use crate::{geometry as g, model::*, plan::Plan};
use anyhow::Result;
use geos::{Geom, GeometryTypes};

pub fn color(color: Color) -> String {
    format!(
        "rgb({},{},{})",
        (color[0] * 255.0).round(),
        (color[1] * 255.0).round(),
        (color[2] * 255.0).round()
    )
}

fn path_data(points: &[Point], close: bool) -> String {
    let mut s = String::new();
    for (i, p) in points.iter().enumerate() {
        s.push_str(&format!(
            "{} {:.6} {:.6} ",
            if i == 0 { "M" } else { "L" },
            p[0],
            p[1]
        ));
    }
    if close {
        s.push('Z');
    }
    s
}

fn polygons<G: Geom>(geometry: &G) -> Result<String> {
    if geometry.is_empty()? {
        return Ok(String::new());
    }
    let mut data = String::new();
    match geometry.geometry_type()? {
        GeometryTypes::Polygon => {
            for points in g::lines(&geometry.get_exterior_ring()?)? {
                data.push_str(&path_data(&points, true));
            }
            for i in 0..geometry.get_num_interior_rings()? {
                for points in g::lines(&geometry.get_interior_ring_n(i)?)? {
                    data.push_str(&path_data(&points, true));
                }
            }
        }
        GeometryTypes::MultiPolygon | GeometryTypes::GeometryCollection => {
            for i in 0..geometry.get_num_geometries()? {
                data.push_str(&polygons(&geometry.get_geometry_n(i)?)?);
            }
        }
        _ => {}
    }
    Ok(data)
}

fn paint_body(plan: &Plan, visible: bool) -> Result<String> {
    let mut result = String::new();
    for paint in &plan.paints {
        if paint.is_raster {
            let paths: Vec<_> = plan
                .paths
                .iter()
                .filter(|p| p.paint_id == paint.id)
                .cloned()
                .collect();
            result.push_str(&pen_body(plan, &paths));
            continue;
        }
        let geometry = if visible {
            anyhow::ensure!(
                paint.visibility_resolved,
                "Visible-paint diagnostics require full visibility"
            );
            &paint.visible
        } else {
            &paint.coverage
        };
        result.push_str(&format!(
            "<path fill=\"{}\" fill-rule=\"evenodd\" d=\"{}\"/>\n",
            color(paint.color),
            polygons(geometry)?
        ));
    }
    Ok(result)
}

fn pen_body(plan: &Plan, paths: &[PenPath]) -> String {
    let mut result = String::new();
    let raster_ids: std::collections::HashSet<_> = plan
        .paints
        .iter()
        .filter(|p| p.is_raster)
        .map(|p| p.id)
        .collect();
    let mut raster: std::collections::BTreeMap<usize, Vec<&PenPath>> =
        std::collections::BTreeMap::new();
    for path in paths {
        if raster_ids.contains(&path.paint_id) {
            raster.entry(path.pen).or_default().push(path);
        } else {
            result.push_str(&format!("<path fill=\"none\" stroke=\"{}\" stroke-width=\"{}\" stroke-linecap=\"round\" stroke-linejoin=\"round\" d=\"{}\"/>\n",color(plan.palette[path.pen-1]),plan.pen_widths[path.pen-1],path_data(&path.points,false)));
        }
    }
    // Blend each ink's union once, as in the lab; retracing the same ink does
    // not repeatedly darken the illustrative preview.
    for (pen, paths) in raster {
        // Raster channels carry the frozen material swatch used by separation.
        // The nominal palette remains the pen identity in emitted HPGL.
        let swatch = plan
            .paints
            .iter()
            .find(|paint| paint.id == paths[0].paint_id)
            .map_or(plan.palette[pen - 1], |paint| paint.color);
        result.push_str(&format!("<g fill=\"none\" stroke=\"{}\" stroke-width=\"{}\" stroke-linecap=\"round\" stroke-linejoin=\"round\" style=\"mix-blend-mode:multiply\">", color(swatch),plan.pen_widths[pen-1]));
        for path in paths {
            result.push_str(&format!("<path d=\"{}\"/>", path_data(&path.points, false)));
        }
        result.push_str("</g>");
    }
    result
}

pub fn svg(plan: &Plan, paths: Option<&[PenPath]>, visible: bool) -> Result<String> {
    let [x0, y0, x1, y1] = plan.bounds;
    let body = if let Some(paths) = paths {
        pen_body(plan, paths)
    } else {
        paint_body(plan, visible)?
    };
    Ok(format!(
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"{x0} {y0} {} {}\" width=\"{}pt\" height=\"{}pt\"><rect x=\"{x0}\" y=\"{y0}\" width=\"{}\" height=\"{}\" fill=\"white\"/>{body}</svg>\n",
        x1 - x0,
        y1 - y0,
        x1 - x0,
        y1 - y0,
        x1 - x0,
        y1 - y0
    ))
}

/// A transparent diagnostic layer, never added to artwork or HPGL. Bounds are
/// local missing fragments found by decoded-footprint verification. Grow and
/// merge them so the user can find small broken letters at normal page zoom.
pub fn detail_loss(
    plan: &Plan,
    reports: &[crate::plan::PaintReport],
) -> Result<(String, serde_json::Value)> {
    let boxes: Vec<_> = reports
        .iter()
        .flat_map(|r| r.missing_detail_bounds.iter())
        .collect();
    // Expand boxes before merging: buffering tens of thousands of tiny islands
    // first would manufacture millions of round-join vertices. Most nearby
    // defects merge into a much smaller set of display regions this way.
    let padding = 0.3 * 72. / 25.4;
    let regions = g::union(
        boxes
            .iter()
            .map(|b| {
                Ok(geos::Geometry::create_rectangle(
                    b[0] - padding,
                    b[1] - padding,
                    b[2] + padding,
                    b[3] + padding,
                )?)
            })
            .collect::<Result<_>>()?,
    )?;
    let regions = g::round_buffer(&regions, 0.05 * 72. / 25.4)?.intersection(
        &geos::Geometry::create_rectangle(
            plan.bounds[0],
            plan.bounds[1],
            plan.bounds[2],
            plan.bounds[3],
        )?,
    )?;
    let data = polygons(&regions)?;
    let [x0, y0, x1, y1] = plan.bounds;
    let svg = format!(
        r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {} {}"><path fill="white" fill-rule="evenodd" d="{data}"/></svg>"#,
        x1 - x0,
        y1 - y0
    );
    let summary = serde_json::json!({"version":2,"affected_paints":reports.iter().filter(|r|!r.missing_detail_bounds.is_empty()).count(),"zones":boxes.len(),"recovered_paints":reports.iter().filter(|r|r.width_status=="thin_detail_recovery").count(),"display_padding_mm":0.35});
    Ok((svg, summary))
}

pub fn comparison(plan: &Plan, decoded: &[PenPath]) -> Result<String> {
    let [x0, y0, x1, y1] = plan.bounds;
    let w = x1 - x0;
    let h = y1 - y0;
    let panel = w + 24.0;
    let mut result = format!(
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {} {}\" width=\"1296\" height=\"{}\"><rect width=\"100%\" height=\"100%\" fill=\"#f1f3f5\"/><g font-family=\"sans-serif\" fill=\"#203040\"><text x=\"16\" y=\"24\" font-size=\"17\">Plotline — paint coverage and physical pen representation</text>",
        panel * 3.0 + 16.0,
        h + 118.0,
        (h + 118.0) / (panel * 3.0 + 16.0) * 1296.0
    );
    let titles = [
        "1 · PDF area model (solid fills)",
        "2 · Planned physical pen strokes",
        "3 · Decoded HPGL (pen width modeled)",
    ];
    let bodies = [
        paint_body(plan, false)?,
        pen_body(plan, &plan.paths),
        pen_body(plan, decoded),
    ];
    for i in 0..3 {
        let x = 16.0 + i as f64 * panel;
        result.push_str(&format!("<text x=\"{x}\" y=\"52\" font-size=\"12\">{}</text><g transform=\"translate({x},64)\"><rect width=\"{w}\" height=\"{h}\" fill=\"white\" stroke=\"#cbd2d8\"/><g transform=\"translate({},{})\">{}</g></g>",titles[i],-x0,-y0,bodies[i]));
    }
    result.push_str(&format!("<text x=\"16\" y=\"{}\" font-size=\"11\">{} ink widths · White is occlusion only · Decoded ink checked against the configured width and occlusion limits</text><text x=\"16\" y=\"{}\" font-size=\"10\">The PDF pixel check tests panel 1 only. Panels 2–3 follow the selected stroke/fill policies; gaps and excess ink are reported separately.</text></g></svg>\n",h+87.0,plan.pen_widths.len(),h+105.0));
    Ok(result)
}
