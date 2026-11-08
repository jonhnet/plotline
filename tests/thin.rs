use anyhow::Result;
use geos::{Geom, Geometry};
use plotline::{geometry as g, hpgl, model::*, plan};
const I: Matrix = [1., 0., 0., 1., 0., 0.];
fn rect(x: f64, y: f64, w: f64, h: f64) -> Vec<Command> {
    vec![
        Command::Move { p: [x, y] },
        Command::Line { p: [x + w, y] },
        Command::Line { p: [x + w, y + h] },
        Command::Line { p: [x, y + h] },
        Command::Close,
    ]
}
fn fill(path: Vec<Command>, text: bool, color: Color) -> Event {
    Event::Fill {
        path,
        matrix: I,
        even_odd: true,
        color,
        role: if text {
            PaintRole::Text
        } else {
            PaintRole::Artwork
        },
    }
}
fn page(events: Vec<Event>) -> Page {
    Page {
        version: 1,
        engine: "thin-detail-fixture".into(),
        units: "pt".into(),
        y_axis: "down".into(),
        bounds: [0., 0., 100., 100.],
        events,
        inks: vec![],
        paint_inks: Default::default(),
        sources: Default::default(),
    }
}
fn settings(t: f64) -> Settings {
    Settings {
        pen_width: 1.,
        thin_detail_thickening: t,
        ..Settings::default()
    }
}
fn decoded(plan: &plan::Plan) -> Result<(Vec<PenPath>, Geometry)> {
    let (hpgl, expected) = hpgl::encode(plan)?;
    let (paths, _) = hpgl::readback(plan, &hpgl, &expected)?;
    plan.verify_paths(&paths)?;
    let ink = g::union(
        paths
            .iter()
            .map(|p| g::round_buffer(&g::line(&p.points)?, 0.5))
            .collect::<Result<_>>()?,
    )?;
    Ok((paths, ink))
}

#[test]
fn underwidth_stroke_uses_one_original_centerline_with_bounded_excess() -> Result<()> {
    let p = page(vec![Event::Stroke {
        path: vec![
            Command::Move { p: [10., 20.] },
            Command::Line { p: [90., 20.] },
        ],
        matrix: I,
        color: [0.; 3],
        style: Stroke {
            width: 0.2,
            cap: "Round".into(),
            join: "Round".into(),
            miter_limit: 10.,
            dash_phase: 0.,
            dashes: vec![],
        },
    }]);
    for tolerance in [0., 0.5] {
        assert!(plan::compile(&p, settings(tolerance))?.paths.is_empty());
    }
    let result = plan::compile(&p, settings(1.))?;
    let (paths, _) = decoded(&result)?;
    assert_eq!(paths.len(), 1);
    assert!(paths[0].points.iter().all(|p| (p[1] - 20.).abs() < 0.03));
    assert!(paths[0].points.last().unwrap()[0] - paths[0].points[0][0] > 79.);
    let report = result.verify_paths(&paths)?;
    assert!(report[0].outside_visible_area_pt2 > 50.);
    assert!(report[0].missing_detail_bounds.is_empty());
    Ok(())
}

#[test]
fn narrow_glyph_recovers_a_stem_and_a_loop_without_outline_retracing() -> Result<()> {
    let mut ring = rect(40., 20., 12., 8.);
    ring.extend(rect(40.3, 20.3, 11.4, 7.4));
    let p = page(vec![
        fill(rect(20., 20., 0.35, 10.), true, [0.; 3]),
        fill(ring, true, [0.; 3]),
    ]);
    let strict = plan::compile(&p, settings(0.))?;
    assert!(strict.paths.is_empty());
    assert_eq!(
        strict
            .verify_paths(&[])?
            .iter()
            .filter(|r| !r.missing_detail_bounds.is_empty())
            .count(),
        2
    );
    let result = plan::compile(&p, settings(0.9))?;
    let (paths, ink) = decoded(&result)?;
    assert_eq!(
        paths.iter().filter(|p| p.paint_id == 0).count(),
        1,
        "I should be one stem, not two outlines"
    );
    for (x, y) in [
        (20.175, 25.),
        (46., 20.15),
        (51.85, 24.),
        (46., 27.85),
        (40.15, 24.),
    ] {
        assert!(
            ink.covers(&Geometry::new_from_wkt(&format!("POINT ({x} {y})"))?)?,
            "Missing stem/loop point {x} {y}"
        );
    }
    assert!(
        !ink.intersects(&Geometry::new_from_wkt("POINT (46 24)")?)?,
        "The O counter must remain open when there is physical room"
    );
    Ok(())
}

#[test]
fn partial_glyph_loss_is_reported_then_recovered_and_hatch_gaps_are_ignored() -> Result<()> {
    // Filled H: broad vertical stems survive, but its hairline crossbar cannot.
    let h = vec![
        [10., 10.],
        [11.6, 10.],
        [11.6, 14.9],
        [18., 14.9],
        [18., 10.],
        [19.6, 10.],
        [19.6, 20.],
        [18., 20.],
        [18., 15.1],
        [11.6, 15.1],
        [11.6, 20.],
        [10., 20.],
    ];
    let mut path = vec![Command::Move { p: h[0] }];
    path.extend(h[1..].iter().map(|p| Command::Line { p: *p }));
    path.push(Command::Close);
    let p = page(vec![
        fill(path, true, [0.; 3]),
        fill(rect(40., 10., 40., 40.), false, [0.; 3]),
    ]);
    let strict = plan::compile(&p, settings(0.))?;
    let (paths, _) = decoded(&strict)?;
    let report = strict.verify_paths(&paths)?;
    assert!(report[0].paths > 0);
    assert!(!report[0].missing_detail_bounds.is_empty());
    assert!(
        report[1].missing_detail_bounds.is_empty(),
        "Hatch gaps are intentional"
    );
    let result = plan::compile(&p, settings(1.))?;
    let (_, ink) = decoded(&result)?;
    assert!(ink.covers(&Geometry::new_from_wkt("POINT (15 15)")?)?);
    Ok(())
}

#[test]
fn recovery_respects_original_clips_later_white_and_complete_erasure() -> Result<()> {
    let p = page(vec![
        Event::ClipFill {
            path: rect(20., 10., 20., 40.),
            matrix: I,
            even_odd: false,
        },
        fill(rect(10., 20., 40., 0.35), true, [0.; 3]),
        Event::PopClip,
        fill(rect(29., 10., 2., 30.), false, [1.; 3]),
        fill(rect(50., 20., 0.3, 10.), true, [0.; 3]),
        fill(rect(49., 19., 3., 12.), false, [1.; 3]),
    ]);
    let result = plan::compile(&p, settings(1.))?;
    let (paths, ink) = decoded(&result)?;
    assert!(paths.iter().all(|p| p.paint_id == 1));
    assert!(ink.area()? > 3.);
    assert!(Geometry::create_rectangle(20., 10., 40., 50.)?.covers(&ink)?);
    assert!(
        ink.intersection(&Geometry::create_rectangle(29., 10., 31., 40.)?)?
            .area()?
            < 1e-7
    );
    let report = result.verify_paths(&paths)?;
    assert!(
        report
            .iter()
            .find(|r| r.id == 4)
            .unwrap()
            .missing_detail_bounds
            .is_empty()
    );
    Ok(())
}

#[test]
fn invalid_thickening_is_rejected_and_zero_is_the_default() -> Result<()> {
    assert_eq!(Settings::default().thin_detail_thickening, 0.);
    for value in [-0.1, 1.01, f64::NAN] {
        assert!(plan::compile(&page(vec![]), settings(value)).is_err());
    }
    Ok(())
}

fn narrow_stroke(a: Point, b: Point) -> Event {
    Event::Stroke {
        path: vec![Command::Move { p: a }, Command::Line { p: b }],
        matrix: I,
        color: [0.; 3],
        style: Stroke {
            width: 0.2,
            cap: "Round".into(),
            join: "Round".into(),
            miter_limit: 10.,
            dash_phase: 0.,
            dashes: vec![],
        },
    }
}

#[test]
fn diagnostic_prioritizes_missing_letters_and_long_marks_over_texture_remnants() -> Result<()> {
    let mut p = page(vec![
        narrow_stroke([10., 10.], [14., 10.]), // short explicit hatch
        narrow_stroke([10., 20.], [18., 20.]), // substantial missing hatch
        narrow_stroke([10., 30.], [14., 30.]), // ordinary lost line
        narrow_stroke([10., 40.], [11., 40.]), // tiny clipped remnant
        fill(rect(20., 20., 0.2, 0.3), true, [0.; 3]), // missing dot
        narrow_stroke([10., 50.], [11., 50.]), // outlined map lettering
    ]);
    p.sources
        .insert(0, "parks/explicit_hatch|grass|ink=green".into());
    p.sources.insert(1, "terrain/hillshade|slope".into());
    p.sources.insert(5, "labels/roads|name".into());
    let result = plan::compile(&p, settings(0.))?;
    assert!(result.paths.is_empty());
    let report = result.verify_paths(&[])?;
    for (index, expected) in [
        (0, false),
        (1, true),
        (2, true),
        (3, false),
        (4, true),
        (5, true),
    ] {
        assert_eq!(
            !report[index].missing_detail_bounds.is_empty(),
            expected,
            "paint {index}"
        );
    }
    Ok(())
}

#[test]
fn diagnostic_ignores_nearby_width_shortfall_but_preserves_a_missing_middle() -> Result<()> {
    let result = plan::compile(
        &page(vec![narrow_stroke([10., 20.], [90., 20.])]),
        settings(0.),
    )?;
    let paint = &result.paints[0];
    // The visible band sits only 0.2 pen diameters from this surviving ink:
    // its edge discrepancy should not create an 80-point-wide warning.
    let ink = Geometry::create_rectangle(10., 20.3, 90., 21.3)?;
    assert!(plotline::thin::missing(paint, &ink, settings(0.))?.is_empty());
    let interrupted = ink.difference(&Geometry::create_rectangle(40., 19., 50., 22.)?)?;
    let missing = plotline::thin::missing(paint, &interrupted, settings(0.))?;
    assert!(!missing.is_empty());
    assert!(missing.iter().all(|b| b[0] >= 40. && b[2] <= 50.));
    Ok(())
}

#[test]
fn diagonal_loss_highlight_follows_the_line_instead_of_its_page_sized_bounds() -> Result<()> {
    let result = plan::compile(
        &page(vec![narrow_stroke([10., 10.], [90., 90.])]),
        settings(0.),
    )?;
    let report = result.verify_paths(&[])?;
    let boxes = &report[0].missing_detail_bounds;
    assert!(boxes.len() > 10);
    assert!(boxes.iter().all(|b| (b[2] - b[0]).max(b[3] - b[1]) <= 2.01));
    let area: f64 = boxes.iter().map(|b| (b[2] - b[0]) * (b[3] - b[1])).sum();
    assert!(
        area < 300.,
        "Warning boxes must follow the diagonal, not cover its 6400 pt² envelope"
    );
    Ok(())
}

#[test]
fn covered_compound_strokes_skip_recovery_but_a_missing_interior_run_is_recovered() -> Result<()> {
    let mut path = Vec::new();
    for y in [20., 40., 60.] {
        path.extend([Command::Move { p: [10., y] }, Command::Line { p: [90., y] }]);
    }
    let p = page(vec![
        Event::ClipFill {
            path: rect(15., 10., 70., 60.),
            matrix: I,
            even_odd: false,
        },
        Event::Stroke {
            path,
            matrix: I,
            color: [0.; 3],
            style: Stroke {
                width: 1.,
                cap: "Round".into(),
                join: "Round".into(),
                miter_limit: 10.,
                dash_phase: 0.,
                dashes: vec![],
            },
        },
        Event::PopClip,
        fill(rect(48., 30., 4., 20.), false, [1.; 3]),
    ]);
    let settings = Settings {
        stroke_width_mode: StrokeWidthMode::Pen,
        stroke_width_tolerance: Some(0.75),
        ..settings(0.75)
    };
    let mut result = plan::compile(&p, settings)?;
    let paint = &result.paints[0];
    assert_eq!(paint.width_status, "pen_width");
    let complete: Vec<_> = result.paths.iter().map(|p| p.points.clone()).collect();
    assert_eq!(
        complete.len(),
        4,
        "The white knockout splits only the middle line"
    );
    assert!(plotline::thin::recover(paint, &complete, &paint.allowed, settings)?.is_empty());
    // Removing the middle line leaves the very same bounds. A bounding-box
    // shortcut (or skipping recovery merely because paths exist) is unsafe.
    let partial: Vec<_> = complete
        .into_iter()
        .filter(|p| (p[0][1] - 40.).abs() > 1.)
        .collect();
    let extra = plotline::thin::recover(paint, &partial, &paint.allowed, settings)?;
    assert!(
        !extra.is_empty(),
        "Recover an actual missing run inside the existing ink bounds"
    );
    result.paths = partial
        .into_iter()
        .chain(extra)
        .map(|points| PenPath {
            paint_id: paint.id,
            pen: paint.pen.unwrap(),
            points,
        })
        .collect();
    let (_, ink) = decoded(&result)?;
    for x in [25., 75.] {
        assert!(ink.covers(&Geometry::new_from_wkt(&format!("POINT ({x} 40)"))?)?);
    }
    assert!(Geometry::create_rectangle(15., 10., 85., 70.)?.covers(&ink)?);
    assert!(
        ink.intersection(&Geometry::create_rectangle(48., 30., 52., 50.)?)?
            .area()?
            < 1e-7
    );
    Ok(())
}
