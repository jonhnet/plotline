use anyhow::Result;
use geos::{Geom, Geometry};
use plotline::{continuity, geometry as g, hpgl, model::*, plan};

fn fixture(points: Vec<Vec<Point>>) -> Result<plan::Plan> {
    // One HPGL unit per point gives exact, inspectable synthetic coordinates.
    let settings = Settings {
        pen_width: 4.,
        units_per_mm: 72. / 25.4,
        ..Settings::default()
    };
    let page = Page {
        version: 1,
        engine: "continuity fixture".into(),
        units: "pt".into(),
        y_axis: "down".into(),
        bounds: [0., 0., 100., 100.],
        events: vec![Event::Fill {
            path: vec![
                Command::Move { p: [0., 0.] },
                Command::Line { p: [100., 0.] },
                Command::Line { p: [100., 100.] },
                Command::Line { p: [0., 100.] },
                Command::Close,
            ],
            matrix: [1., 0., 0., 1., 0., 0.],
            color: [0., 0.5, 0.],
            even_odd: false,
            role: PaintRole::Artwork,
        }],
        inks: vec![],
        paint_inks: Default::default(),
        sources: Default::default(),
    };
    let mut p = plan::compile(&page, settings)?;
    p.paths = points
        .into_iter()
        .map(|points| PenPath {
            paint_id: 0,
            pen: 1,
            points,
        })
        .collect();
    Ok(p)
}

#[test]
fn touching_and_overlapping_strokes_form_one_verified_run() -> Result<()> {
    let p = fixture(vec![
        vec![[10., 10.], [20., 10.]],
        vec![[20., 10.], [30., 10.]],
        vec![[32., 10.], [40., 15.]],
    ])?;
    let e = continuity::encode(&p)?;
    assert_eq!(e.report.touching_joins, 1);
    assert_eq!(e.report.overlap_joins, 1);
    assert_eq!(e.report.lifts_removed, 2);
    let (physical, metrics) = e.readback(&p)?;
    assert_eq!(physical.len(), 1);
    assert_eq!(metrics.paths, 1);
    assert_eq!(e.source_paths.len(), 3);
    assert_eq!(
        physical[0].points,
        vec![[10., 10.], [20., 10.], [30., 10.], [32., 10.], [40., 15.]]
    );
    p.verify_paths(&e.source_paths)?;
    p.verify_paths(&physical)?;
    assert!(e.commands.ends_with("PU;SP0;\n"));
    Ok(())
}

#[test]
fn small_gaps_and_clearings_require_the_full_pen_footprint() -> Result<()> {
    let mut p = fixture(vec![
        vec![[10., 10.], [20., 10.]],
        vec![[23., 10.], [30., 10.]],
    ])?;
    // End caps overlap but connecting their centers would add a lens of ink.
    let ink = g::union(
        p.paths
            .iter()
            .map(|p| g::round_buffer(&g::line(&p.points)?, 2.))
            .collect::<Result<_>>()?,
    )?;
    p.paints[0].allowed = ink;
    let e = continuity::encode(&p)?;
    assert_eq!(e.report.overlap_candidates, 1);
    assert_eq!(e.report.overlap_joins, 0);
    assert_eq!(e.readback(&p)?.1.paths, 2);
    // Even if the connector's centerline fits a narrow corridor, its width does not.
    let corridor = Geometry::new_from_wkt("POLYGON ((20 9.9,23 9.9,23 10.1,20 10.1,20 9.9))")?;
    p.paints[0].allowed = p.paints[0].allowed.union(&corridor)?;
    assert_eq!(continuity::encode(&p)?.report.overlap_joins, 0);
    Ok(())
}

#[test]
fn different_inks_and_distant_strokes_keep_their_lifts() -> Result<()> {
    let mut p = fixture(vec![
        vec![[10., 10.], [20., 10.]],
        vec![[20., 10.], [30., 10.]],
        vec![[40., 10.], [50., 10.]],
    ])?;
    p.palette.push([1., 0., 0.]);
    p.pen_widths.push(4.);
    p.paths[1].pen = 2;
    let e = continuity::encode(&p)?;
    assert_eq!(e.report.lifts_removed, 0);
    assert_eq!(e.readback(&p)?.1.paths, 3);
    p.paths[1].pen = 1;
    let e = continuity::encode(&p)?;
    assert_eq!(e.report.touching_joins, 1);
    assert_eq!(e.report.overlap_candidates, 0);
    assert_eq!(e.readback(&p)?.1.paths, 2);
    Ok(())
}

#[test]
fn connections_use_the_final_integer_coordinates() -> Result<()> {
    let p = fixture(vec![
        vec![[10., 10.], [20.49, 10.]],
        vec![[24.51, 10.], [30., 10.]],
    ])?;
    let e = continuity::encode(&p)?;
    assert_eq!(e.report.lifts_removed, 0); // Actual endpoints are 20 and 25.
    let p = fixture(vec![
        vec![[10., 10.], [20.49, 10.]],
        vec![[20.1, 10.], [30., 10.]],
    ])?;
    let e = continuity::encode(&p)?;
    assert_eq!(e.report.touching_joins, 1);
    assert_eq!(e.readback(&p)?.1.paths, 1);
    Ok(())
}

#[test]
fn readback_rejects_changed_or_missing_strokes() -> Result<()> {
    let p = fixture(vec![
        vec![[10., 10.], [20., 10.]],
        vec![[22., 10.], [30., 10.]],
    ])?;
    let mut e = continuity::encode(&p)?;
    e.commands = e.commands.replace("22,90", "23,90");
    assert!(e.readback(&p).is_err());
    let mut e = continuity::encode(&p)?;
    e.commands = e.commands.replace(",30,90", "");
    assert!(e.readback(&p).is_err());
    Ok(())
}

#[test]
fn source_paint_identity_survives_connections_across_paints() -> Result<()> {
    let mut p = fixture(vec![
        vec![[10., 10.], [20., 10.]],
        vec![[24., 10.], [30., 10.]],
    ])?;
    let mut second = fixture(vec![])?.paints.remove(0);
    second.id = 1;
    second.source = "second feature".into();
    // Neither individual mask fits the whole connector, but their union does.
    p.paints[0].allowed = Geometry::new_from_wkt("POLYGON ((0 0,22 0,22 100,0 100,0 0))")?;
    second.allowed = Geometry::new_from_wkt("POLYGON ((22 0,100 0,100 100,22 100,22 0))")?;
    p.paints.push(second);
    p.paths[1].paint_id = 1;
    let e = continuity::encode(&p)?;
    assert_eq!(e.report.overlap_joins, 1);
    assert_eq!(e.report.connections[0].from_paint, 0);
    assert_eq!(e.report.connections[0].to_paint, 1);
    assert_eq!(e.source_paths[0].paint_id, 0);
    assert_eq!(e.source_paths[1].paint_id, 1);
    assert_eq!(e.readback(&p)?.1.paths, 1);
    let reports = p.verify_paths(&e.source_paths)?;
    let mut totals = plotline::summary::summarize(&reports, &[])?;
    plotline::summary::add_continuity(&mut totals, &e.report, &reports, &[])?;
    let mm = e.readback(&p)?.1.pen_down_mm;
    assert_eq!(totals.by_ink["1"].paths, 1);
    assert!((totals.by_ink["1"].pen_down_mm - mm).abs() < 1e-9);
    assert!(
        (totals
            .by_feature
            .values()
            .map(|r| r.pen_down_mm)
            .sum::<f64>()
            - mm)
            .abs()
            < 1e-9
    );
    Ok(())
}

#[test]
fn white_paint_and_clips_survive_stitching() -> Result<()> {
    // Genuine compilation, including later paint subtraction and finite-width cuts.
    let input = include_str!("fixtures/house-width-clipped.json");
    let page: Page = serde_json::from_str(input)?;
    let mut p = plan::compile(
        &page,
        Settings {
            stroke_width_tolerance: Some(0.75),
            ..Settings::default()
        },
    )?;
    plotline::route::optimize(&mut p)?;
    let e = continuity::encode(&p)?;
    let (physical, _) = e.readback(&p)?;
    p.verify_paths(&e.source_paths)?;
    let masks = g::union(
        p.paints
            .iter()
            .filter(|p| p.pen.is_some())
            .map(|p| Ok(Geom::clone(&p.allowed)?))
            .collect::<Result<_>>()?,
    )?;
    let ink = g::union(
        physical
            .iter()
            .map(|path| g::round_buffer(&g::line(&path.points)?, p.pen_widths[path.pen - 1] / 2.))
            .collect::<Result<_>>()?,
    )?;
    assert!(ink.difference(&masks)?.area()? <= 1e-7);
    let (_, before) = hpgl::decode(&hpgl::encode(&p)?.0, p.bounds, p.settings)?;
    assert_eq!(before.paths - e.report.lifts_removed, physical.len());
    Ok(())
}
