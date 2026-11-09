use anyhow::Result;
use geos::{Geom, Geometry};
use plotline::{geometry as g, hpgl, model::*, plan};

const I: Matrix = [1., 0., 0., 1., 0., 0.];
fn line(a: Point, b: Point, color: Color, width: f64) -> Event {
    Event::Stroke {
        path: vec![Command::Move { p: a }, Command::Line { p: b }],
        matrix: I,
        color,
        style: Stroke {
            width,
            cap: "Round".into(),
            join: "Round".into(),
            miter_limit: 10.,
            dash_phase: 0.,
            dashes: vec![],
        },
    }
}
fn rect(x: f64, y: f64, w: f64, h: f64) -> Vec<Command> {
    vec![
        Command::Move { p: [x, y] },
        Command::Line { p: [x + w, y] },
        Command::Line { p: [x + w, y + h] },
        Command::Line { p: [x, y + h] },
        Command::Close,
    ]
}
fn fill(color: Color) -> Event {
    Event::Fill {
        path: rect(48., 40., 4., 20.),
        matrix: I,
        color,
        even_odd: false,
        role: PaintRole::Artwork,
    }
}
fn compile(events: Vec<Event>, overlap: bool, mode: StrokeWidthMode) -> Result<plan::Plan> {
    let page = Page {
        version: 1,
        engine: "overlap-fixture".into(),
        units: "pt".into(),
        y_axis: "down".into(),
        bounds: [0., 0., 100., 100.],
        events,
        inks: vec![],
        paint_inks: Default::default(),
        sources: Default::default(),
    };
    plan::compile(
        &page,
        Settings {
            pen_width: 1.,
            stroke_width_tolerance: Some(0.75),
            thin_detail_thickening: 0.75,
            stroke_width_mode: mode,
            overlap_strokes: overlap,
            fill_style: FillStyle::None,
            ..Settings::default()
        },
    )
}
fn decode(plan: &plan::Plan) -> Result<Vec<PenPath>> {
    let (commands, expected) = hpgl::encode(plan)?;
    let (paths, _) = hpgl::readback(plan, &commands, &expected)?;
    plan.verify_paths(&paths)?;
    Ok(paths)
}

#[test]
fn crossings_keep_both_complete_strokes_for_same_and_different_inks() -> Result<()> {
    for color in [[0.; 3], [1., 0., 0.]] {
        for mode in [StrokeWidthMode::Pen, StrokeWidthMode::Preserve] {
            let events = vec![
                line([10., 50.], [90., 50.], [0.; 3], 1.),
                line([50., 10.], [50., 90.], color, 1.),
            ];
            let cut = compile(events.clone(), false, mode)?;
            assert_eq!(cut.paths.iter().filter(|p| p.paint_id == 0).count(), 2);
            let whole = compile(events, true, mode)?;
            assert_eq!(whole.paths.len(), 2);
            for path in decode(&whole)? {
                assert!(g::line(&path.points)?.length()? > 79.9);
            }
            assert!(
                whole.paints[0]
                    .visible
                    .covers(&Geometry::new_from_wkt("POINT (50 50)")?)?
            );
        }
    }
    Ok(())
}

#[test]
fn overlap_keeps_white_and_colored_fills_white_strokes_and_clips() -> Result<()> {
    for blocker in [
        fill([1.; 3]),
        fill([0., 1., 0.]),
        line([50., 40.], [50., 60.], [1.; 3], 4.),
    ] {
        let plan = compile(
            vec![
                Event::ClipFill {
                    path: rect(15., 20., 70., 60.),
                    matrix: I,
                    even_odd: false,
                },
                line([10., 50.], [90., 50.], [0.; 3], 1.),
                Event::PopClip,
                blocker,
            ],
            true,
            StrokeWidthMode::Pen,
        )?;
        let paths = decode(&plan)?;
        assert_eq!(paths.len(), 2);
        let ink = g::union(
            paths
                .iter()
                .map(|p| g::round_buffer(&g::line(&p.points)?, 0.5))
                .collect::<Result<_>>()?,
        )?;
        assert!(Geometry::create_rectangle(15., 20., 85., 80.)?.covers(&ink)?);
        assert!(
            ink.intersection(&Geometry::create_rectangle(48., 40., 52., 60.)?)?
                .area()?
                < 1e-7
        );
    }
    // Colored strokes still obscure earlier fills; only stroke/stroke cutting changes.
    let plan = compile(
        vec![
            fill([0.; 3]),
            line([40., 50.], [60., 50.], [1., 0., 0.], 1.),
        ],
        true,
        StrokeWidthMode::Pen,
    )?;
    assert!(
        plan.paints[0]
            .visible
            .intersection(&plan.paints[1].coverage)?
            .area()?
            < 1e-7
    );
    Ok(())
}

#[test]
fn repeated_compound_strokes_do_not_trigger_the_fully_hidden_shortcut() -> Result<()> {
    let mut stroke = line([10., 30.], [90., 30.], [0.; 3], 1.);
    if let Event::Stroke { path, .. } = &mut stroke {
        path.extend([
            Command::Move { p: [10., 70.] },
            Command::Line { p: [90., 70.] },
        ]);
    }
    let cut = compile(
        vec![stroke.clone(), stroke.clone()],
        false,
        StrokeWidthMode::Pen,
    )?;
    assert!(cut.paints[0].visible.is_empty()?);
    let whole = compile(vec![stroke.clone(), stroke], true, StrokeWidthMode::Pen)?;
    assert_eq!(whole.paths.len(), 4);
    assert_eq!(decode(&whole)?.len(), 4);
    Ok(())
}

#[test]
fn contained_self_crossing_curve_keeps_its_original_traversal() -> Result<()> {
    let points = vec![[10., 10.], [90., 90.], [90., 10.], [10., 90.], [10., 10.]];
    let mut stroke = line(points[0], points[1], [0.; 3], 1.);
    if let Event::Stroke { path, .. } = &mut stroke {
        *path = std::iter::once(Command::Move { p: points[0] })
            .chain(points[1..].iter().map(|p| Command::Line { p: *p }))
            .collect();
    }
    let whole = compile(vec![stroke], true, StrokeWidthMode::Pen)?;
    assert_eq!(whole.paths.len(), 1);
    assert_eq!(whole.paths[0].points, points);
    assert_eq!(decode(&whole)?.len(), 1);
    Ok(())
}
