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
fn stroke_style(width: f64) -> Stroke {
    Stroke {
        width,
        cap: "Round".into(),
        join: "Round".into(),
        miter_limit: 10.,
        dash_phase: 0.,
        dashes: vec![],
    }
}
fn stroke(a: Point, b: Point, width: f64, color: Color) -> Event {
    Event::Stroke {
        path: vec![Command::Move { p: a }, Command::Line { p: b }],
        matrix: I,
        style: stroke_style(width),
        color,
    }
}
fn fill(path: Vec<Command>, color: Color) -> Event {
    Event::Fill {
        path,
        matrix: I,
        even_odd: false,
        color,
        role: PaintRole::Artwork,
    }
}
fn page(events: Vec<Event>) -> Page {
    Page {
        version: 1,
        engine: "synthetic".into(),
        units: "pt".into(),
        y_axis: "down".into(),
        bounds: [0., 0., 100., 100.],
        events,
        inks: Vec::new(),
        paint_inks: Default::default(),
        sources: Default::default(),
    }
}
fn compile(events: Vec<Event>) -> Result<plan::Plan> {
    plan::compile(&page(events), Settings::default())
}
fn point(x: f64, y: f64) -> Result<Geometry> {
    Ok(Geometry::new_from_wkt(&format!("POINT ({x} {y})"))?)
}

#[test]
fn white_core_recovers_both_casing_edges_then_later_paint_cuts_them() -> Result<()> {
    let p = compile(vec![
        stroke([10., 50.], [90., 50.], 12., [0., 0., 0.]),
        stroke([10., 50.], [90., 50.], 8., [1.; 3]),
        fill(rect(45., 40., 10., 20.), [1.; 3]),
    ])?;
    assert_eq!(p.palette, vec![[0.; 3]]);
    assert!(p.paths.iter().all(|path| path.paint_id == 0));
    let points: Vec<_> = p.paths.iter().flat_map(|p| &p.points).collect();
    assert!(points.iter().any(|p| p[1] > 54. && p[0] < 40.));
    assert!(points.iter().any(|p| p[1] < 46. && p[0] > 60.));
    assert!(!p.paints[0].visible.intersects(&point(50., 55.)?)?);
    let reports = p.verify_paths(&p.paths)?;
    assert!(reports[0].ink_area_pt2.unwrap() > 150.); // Cannot pass merely by emitting nothing.
    let (text, expected) = hpgl::encode(&p)?;
    assert!(text.contains(&format!(
        "CO\"pen id=1 r=0 g=0 b=0 width_mm={:.6}\";",
        p.settings.pen_width * 25.4 / 72.
    )));
    let (decoded, _) = hpgl::readback(&p, &text, &expected)?;
    p.verify_paths(&decoded)?;
    assert!(!text.contains("SP2;"));
    Ok(())
}

#[test]
fn footprint_not_just_centerline_is_clipped() -> Result<()> {
    let p = compile(vec![
        stroke([10., 50.], [90., 50.], 4., [0.; 3]),
        fill(rect(50., 0., 50., 100.), [1.; 3]),
    ])?;
    let last_x = p
        .paths
        .iter()
        .flat_map(|p| &p.points)
        .map(|p| p[0])
        .fold(f64::NEG_INFINITY, f64::max);
    assert!(
        last_x < 49.6,
        "Pen radius must shorten endpoints; got {last_x}"
    );
    p.verify_paths(&p.paths)?;
    let bad = vec![PenPath {
        paint_id: 0,
        pen: 1,
        points: vec![[10., 50.], [50., 50.]],
    }];
    assert!(p.verify_paths(&bad).is_err());
    Ok(())
}

#[test]
fn winding_and_evenodd_differ_for_same_orientation_nested_rings() -> Result<()> {
    let mut path = rect(10., 10., 80., 80.);
    path.extend(rect(30., 30., 40., 40.));
    let paths = g::flatten(&path, I, 0.01)?;
    assert!((g::fill(&paths, false)?.area()? - 6400.).abs() < 1e-9);
    assert!((g::fill(&paths, true)?.area()? - 4800.).abs() < 1e-9);
    let mut reversed = paths.clone();
    reversed[1].reverse();
    assert!((g::fill(&reversed, false)?.area()? - 4800.).abs() < 1e-9);
    Ok(())
}

#[test]
fn self_intersections_and_duplicate_rings_keep_original_winding() -> Result<()> {
    let bow = vec![vec![[0., 0.], [10., 10.], [0., 10.], [10., 0.], [0., 0.]]];
    assert!((g::fill(&bow, true)?.area()? - 50.).abs() < 1e-9);
    let ring = vec![[0., 0.], [10., 0.], [10., 10.], [0., 10.], [0., 0.]];
    assert!(g::fill(&[ring.clone(), ring.clone()], true)?.is_empty()?);
    assert!((g::fill(&[ring.clone(), ring], false)?.area()? - 100.).abs() < 1e-9);
    Ok(())
}

#[test]
fn nested_nonrectangular_clips_restore_and_do_not_occlude_outside_clip() -> Result<()> {
    let triangle = vec![
        Command::Move { p: [0., 0.] },
        Command::Line { p: [100., 0.] },
        Command::Line { p: [0., 100.] },
        Command::Close,
    ];
    let p = compile(vec![
        fill(rect(0., 0., 100., 100.), [0.; 3]),
        Event::ClipFill {
            path: triangle,
            matrix: I,
            even_odd: false,
        },
        Event::ClipFill {
            path: rect(0., 0., 30., 100.),
            matrix: I,
            even_odd: false,
        },
        fill(rect(0., 0., 100., 100.), [1.; 3]),
        Event::PopClip,
        Event::PopClip,
        fill(rect(80., 80., 10., 10.), [0.5, 0., 0.]),
    ])?;
    assert!(p.paints[0].visible.intersects(&point(70., 70.)?)?);
    assert!(!p.paints[0].visible.intersects(&point(10., 10.)?)?);
    assert!(p.paints[2].coverage.intersects(&point(85., 85.)?)?);
    p.verify_paths(&p.paths)?;
    Ok(())
}

#[test]
fn stroked_clip_is_an_area_not_a_fill() -> Result<()> {
    let p = compile(vec![
        Event::ClipStroke {
            path: vec![
                Command::Move { p: [10., 50.] },
                Command::Line { p: [90., 50.] },
            ],
            matrix: I,
            style: stroke_style(10.),
        },
        fill(rect(0., 0., 100., 100.), [0.; 3]),
        Event::PopClip,
    ])?;
    assert!(p.paints[0].coverage.area()? > 800.);
    assert!(!p.paints[0].coverage.intersects(&point(50., 70.)?)?);
    Ok(())
}

#[test]
fn affine_transform_applies_to_stroke_coverage_not_scalar_width() -> Result<()> {
    let local = g::stroke(&[vec![[0., 0.], [10., 0.]]], &stroke_style(2.))?.0;
    let transformed = g::transform(&local, [2., 0., 0.5, 3., 10., 20.])?;
    assert!((transformed.area()? / local.area()? - 6.).abs() < 1e-9);
    assert!((transformed.get_y_max()? - 23.).abs() < 1e-9);
    Ok(())
}

#[test]
fn caps_joins_and_pdf_miter_limit_are_explicit() -> Result<()> {
    let path = vec![vec![[0., 0.], [10., 0.]]];
    let mut style = stroke_style(2.);
    style.cap = "Butt".into();
    assert!((g::stroke(&path, &style)?.0.area()? - 20.).abs() < 1e-9);
    style.cap = "Square".into();
    assert!((g::stroke(&path, &style)?.0.area()? - 24.).abs() < 1e-9);
    style.cap = "Round".into();
    assert!((g::stroke(&path, &style)?.0.area()? - (20. + std::f64::consts::PI)).abs() < 0.002);
    style.join = "Miter".into();
    style.miter_limit = 2.;
    let sharp = [vec![[0., 0.], [10., 0.], [0., 0.1]]];
    let miter = g::stroke(&sharp, &style)?.0;
    style.join = "Bevel".into();
    let bevel = g::stroke(&sharp, &style)?.0;
    assert!(miter.sym_difference(&bevel)?.area()? < 1e-8);
    Ok(())
}

#[test]
fn dash_phase_resets_at_subpath_and_continues_through_vertices() -> Result<()> {
    let mut style = stroke_style(1.);
    style.dashes = vec![3., 2.];
    style.dash_phase = 1.;
    let paths = g::dashed(
        &[
            vec![[0., 0.], [1., 0.], [10., 0.]],
            vec![[0., 5.], [10., 5.]],
        ],
        &style,
    )?;
    assert_eq!(paths[0], vec![[0., 0.], [1., 0.], [2., 0.]]);
    assert_eq!(paths[1], vec![[4., 0.], [7., 0.]]);
    assert_eq!(paths[3], vec![[0., 5.], [2., 5.]]);
    Ok(())
}

#[test]
fn closed_dash_has_no_false_cap_at_seam() -> Result<()> {
    let mut style = stroke_style(1.);
    style.dashes = vec![3., 2.];
    style.dash_phase = 1.;
    let paths = g::dashed(
        &[vec![[0., 0.], [3., 0.], [3., 3.], [0., 3.], [0., 0.]]],
        &style,
    )?;
    assert!(paths.iter().any(|p| p.windows(3).any(|w| w[1] == [0., 0.])));
    Ok(())
}

#[test]
fn repeated_vertices_round_dots_and_tangent_occlusion() -> Result<()> {
    assert!(g::flatten(&[Command::Move { p: [1., 1.] }], I, 0.01)?.is_empty());
    let commands = vec![
        Command::Move { p: [1., 1.] },
        Command::Line { p: [1., 1.] },
        Command::Line { p: [1., 1.] },
    ];
    let paths = g::flatten(&commands, I, 0.01)?;
    assert_eq!(paths, vec![vec![[1., 1.]]]);
    assert!(g::stroke(&paths, &stroke_style(2.))?.0.area()? > 3.);
    let p = compile(vec![
        stroke([10., 50.], [90., 50.], 10., [0.; 3]),
        fill(rect(20., 55., 60., 20.), [1.; 3]),
    ])?;
    assert!((p.paints[0].coverage.area()? - p.paints[0].visible.area()?).abs() < 1e-8);
    p.verify_paths(&p.paths)?;
    Ok(())
}

#[test]
fn near_coincident_edges_and_large_coordinates_remain_valid() -> Result<()> {
    for delta in [0., 1e-9, -1e-9, 0.001] {
        let a = Geometry::create_rectangle(1e6, 1e6, 1e6 + 100., 1e6 + 100.)?;
        let b = Geometry::create_rectangle(1e6 + 50. + delta, 1e6, 1e6 + 150., 1e6 + 100.)?;
        let rest = a.difference(&b)?;
        assert!(rest.is_valid()?);
        assert!((rest.area()? - (50. + delta) * 100.).abs() < 1e-5);
    }
    Ok(())
}

#[test]
fn cubic_flattening_error_is_scaled_to_page_space() -> Result<()> {
    let path = vec![
        Command::Move { p: [0., 0.] },
        Command::Cubic {
            p1: [0., 10.],
            p2: [10., 10.],
            p3: [10., 0.],
        },
    ];
    let small = g::flatten(&path, I, 0.01)?;
    let large = g::flatten(&path, [100., 0., 0., 100., 0., 0.], 0.01)?;
    assert!(large[0].len() > small[0].len());
    let curve = g::line(&large[0])?;
    for i in 0..101 {
        let t = i as f64 / 100.;
        let x = 30. * t * t * (1. - t) + 10. * t * t * t;
        let y = 30. * t * (1. - t);
        assert!(curve.distance(&point(x, y)?)? < 0.00011);
    }
    Ok(())
}

#[test]
fn empty_white_only_and_unrepresentable_thin_pages_are_reported() -> Result<()> {
    assert!(compile(vec![])?.paths.is_empty());
    let p = compile(vec![fill(rect(0., 0., 100., 100.), [1.; 3])])?;
    assert!(p.palette.is_empty());
    let p = compile(vec![stroke([10., 50.], [90., 50.], 0.1, [0.; 3])])?;
    assert!(p.paths.is_empty());
    assert!(
        p.verify_paths(&[])?
            .first()
            .unwrap()
            .unrepresented_area_pt2
            .unwrap()
            > 7.
    );
    Ok(())
}

#[test]
fn unsupported_and_invalid_inputs_fail_before_output() -> Result<()> {
    assert!(
        compile(vec![Event::Unsupported {
            feature: "fillText".into(),
            detail: String::new()
        }])
        .is_err()
    );
    assert!(compile(vec![Event::PopClip]).is_err());
    assert!(
        compile(vec![Event::ClipFill {
            path: rect(0., 0., 10., 10.),
            matrix: I,
            even_odd: false
        }])
        .is_err()
    );
    assert!(compile(vec![stroke([10., 50.], [90., 50.], 0., [0.; 3])]).is_err());
    assert!(
        plan::compile(
            &page(vec![]),
            Settings {
                pen_width: f64::NAN,
                ..Settings::default()
            }
        )
        .is_err()
    );
    Ok(())
}

#[test]
fn hpgl_interpreter_checks_state_units_and_y_flip() -> Result<()> {
    let settings = Settings::default();
    let (paths, m) = hpgl::decode(
        "IN;PA;SP1;PU40,80;PD80,80,80,120;PU;SP0;",
        [0., 0., 100., 100.],
        settings,
    )?;
    assert_eq!(paths.len(), 1);
    assert_eq!(paths[0].pen, 1);
    assert!((m.pen_down_mm - 2.).abs() < 1e-9);
    assert!((paths[0].points[0][1] - (100. - 2. * 72. / 25.4)).abs() < 1e-9);
    assert!(hpgl::decode("IN;PA;PD0,0;PU;SP0;", [0., 0., 100., 100.], settings).is_err());
    assert!(hpgl::decode("IN;PA;SP1;PD1,2,3;PU;SP0;", [0., 0., 100., 100.], settings).is_err());
    assert!(hpgl::decode("IN;PA;XX;", [0., 0., 100., 100.], settings).is_err());
    Ok(())
}

#[test]
fn casing_completeness_is_enforced_separately_from_containment() -> Result<()> {
    let p = compile(vec![
        stroke([10., 50.], [90., 50.], 12., [0.; 3]),
        stroke([10., 50.], [90., 50.], 8., [1.; 3]),
    ])?;
    let report = p.verify_paths(&p.paths)?;
    assert!(report[0].ink_fraction_of_visible_area.unwrap() > 0.90);
    // Removing every path remains contained, but MUST fail the quality gate.
    let missing = p.verify_paths(&[])?;
    assert!(missing[0].outside_visible_area_pt2 <= 1e-7);
    assert!(missing[0].ink_fraction_of_visible_area.unwrap() < 0.90);
    Ok(())
}

#[test]
fn hatch_spacing_and_extent_are_intentional_and_not_solid_fidelity() -> Result<()> {
    let p = compile(vec![fill(rect(10., 10., 40., 20.), [0., 0.5, 0.])])?;
    let rows: Vec<_> = p
        .paths
        .iter()
        .filter(|p| p.points.len() == 2 && (p.points[0][1] - p.points[1][1]).abs() < 1e-8)
        .collect();
    assert_eq!(rows.len(), 8);
    let mut ys: Vec<_> = rows.iter().map(|p| p.points[0][1]).collect();
    ys.sort_by(f64::total_cmp);
    for pair in ys.windows(2) {
        assert!((pair[1] - pair[0] - 3.0 * p.settings.pen_width).abs() < 1e-8);
    }
    for row in rows {
        assert!((row.points[1][0] - row.points[0][0]).abs() > 39.);
    }
    assert!(
        p.paths.len() <= 9,
        "Hatch/perimeter intersections must not fragment paths"
    );
    let fraction = p.verify_paths(&p.paths)?[0]
        .ink_fraction_of_visible_area
        .unwrap();
    assert!((0.35..0.50).contains(&fraction));
    Ok(())
}

#[test]
fn solid_fill_has_a_different_completeness_requirement() -> Result<()> {
    let p = plan::compile(
        &page(vec![fill(rect(10., 10., 40., 20.), [0., 0.5, 0.])]),
        Settings {
            fill_style: FillStyle::Solid,
            ..Settings::default()
        },
    )?;
    assert!(
        p.verify_paths(&p.paths)?[0]
            .ink_fraction_of_visible_area
            .unwrap()
            > 0.97
    );
    Ok(())
}

#[test]
fn routing_preserves_each_paints_linework_and_does_not_worsen_objective() -> Result<()> {
    let mut p = compile(vec![
        stroke([80., 20.], [90., 20.], 3., [0.; 3]),
        stroke([10., 90.], [20., 90.], 3., [0.; 3]),
        fill(rect(30., 30., 20., 20.), [0., 0.5, 0.]),
    ])?;
    let original = p.paths.clone();
    let report = plotline::route::optimize(&mut p)?;
    for paint in &p.paints {
        let collect = |paths: &[PenPath]| {
            g::union(
                paths
                    .iter()
                    .filter(|p| p.paint_id == paint.id)
                    .map(|p| g::line(&p.points))
                    .collect::<Result<_>>()?,
            )
        };
        assert!(collect(&original)?.equals(&collect(&p.paths)?)?);
    }
    if report.applied {
        assert!(
            report.candidate.pen_up_mm + report.y_penalty * report.candidate.total_y_travel_mm
                <= report.before.pen_up_mm + report.y_penalty * report.before.total_y_travel_mm
        );
    }
    p.verify_paths(&p.paths)?;
    Ok(())
}

#[test]
fn indexed_visibility_matches_reverse_global_union() -> Result<()> {
    let mut events = Vec::new();
    for i in 0..40 {
        events.push(fill(
            rect((i * 13 % 85) as f64, (i * 29 % 80) as f64, 12., 16.),
            if i % 3 == 0 { [1.; 3] } else { [0.; 3] },
        ));
    }
    let p = compile(events)?;
    let mut later = g::empty()?;
    for paint in p.paints.iter().rev() {
        let reference = paint.coverage.difference(&later)?;
        assert!(reference.sym_difference(&paint.visible)?.area()? < 1e-7);
        later = later.union(&paint.coverage)?;
    }
    Ok(())
}

#[test]
fn palette_compression_changes_pens_but_preserves_logical_paints_and_white_occlusion() -> Result<()>
{
    let input = page(vec![
        fill(rect(10., 10., 30., 50.), [0.95, 0.84, 0.71]),
        fill(rect(30., 30., 30., 50.), [0.78, 0.95, 0.64]),
        fill(rect(60., 10., 30., 50.), [0.4; 3]),
        fill(rect(20., 45., 60., 5.), [1.; 3]),
    ]);
    let exact = plan::compile(&input, Settings::default())?;
    let compressed = plan::compile(
        &input,
        Settings {
            palette_policy: PalettePolicy::RedGreenBlack,
            ..Settings::default()
        },
    )?;
    for (a, b) in exact.paints.iter().zip(&compressed.paints) {
        assert_eq!(a.color, b.color);
        assert!(a.visible.equals(&b.visible)?);
    }
    let report = compressed.verify_paths(&compressed.paths)?;
    assert_eq!(report[0].pen_color, Some(plotline::palette::RED));
    assert_eq!(report[1].pen_color, Some(plotline::palette::GREEN));
    assert_eq!(report[2].pen_color, Some(plotline::palette::BLACK));
    assert_eq!(report[3].paths, 0);
    let (text, expected) = hpgl::encode(&compressed)?;
    let (decoded, _) = hpgl::readback(&compressed, &text, &expected)?;
    compressed.verify_paths(&decoded)?;
    assert!(decoded.iter().all(|p| (1..=3).contains(&p.pen)));
    Ok(())
}

#[test]
fn crosshatch_is_sparse_in_both_directions_and_keeps_holes_clear() -> Result<()> {
    let mut source = page(vec![
        fill(rect(5., 5., 90., 90.), [0., 0.5, 0.]),
        fill(rect(40., 40., 20., 20.), [1.; 3]),
    ]);
    source.sources.insert(0, "parks|way/7|Test".into());
    let settings = Settings {
        fill_style: FillStyle::Crosshatch,
        hatch_spacing: Some(10.),
        ..Settings::default()
    };
    let mut p = plan::compile(&source, settings)?;
    for sign in [1., -1.] {
        assert!(p.paths.iter().flat_map(|p| p.points.windows(2)).any(|p| {
            let dx = p[1][0] - p[0][0];
            let dy = p[1][1] - p[0][1];
            dx * dy * sign > 0. && (dx.abs() - dy.abs()).abs() < 1e-8 && dx.abs() > 5.
        }));
    }
    plotline::route::optimize(&mut p)?;
    let (commands, expected) = hpgl::encode(&p)?;
    let (decoded, metrics) = hpgl::readback(&p, &commands, &expected)?;
    let reports = p.verify_paths(&decoded)?;
    let r = &reports[0];
    assert_eq!(r.source, "parks|way/7|Test");
    assert_eq!(r.representation, "crosshatch");
    assert!((0.14..0.22).contains(&r.ink_fraction_of_visible_area.unwrap()));
    assert!(
        (reports.iter().map(|r| r.pen_down_mm).sum::<f64>() - metrics.pen_down_mm).abs() < 1e-8
    );
    assert!(
        (reports.iter().map(|r| r.incoming_pen_up_mm).sum::<f64>() - metrics.pen_up_mm).abs()
            < 1e-8
    );
    let wider = plan::compile(
        &source,
        Settings {
            hatch_spacing: Some(20.),
            ..settings
        },
    )?;
    assert!(wider.paths.len() < p.paths.len());
    assert!(
        plan::compile(
            &source,
            Settings {
                hatch_spacing: Some(0.),
                ..settings
            }
        )
        .is_err()
    );
    Ok(())
}

#[test]
fn native_glyph_is_noded_after_reflection_and_translation() -> Result<()> {
    let page: Page = serde_json::from_str(include_str!("fixtures/transformed-glyph.json"))?;
    let p = plan::compile(
        &page,
        Settings {
            pen_width: 0.4,
            ..Settings::default()
        },
    )?;
    assert!(p.paints[0].visible.is_valid()?);
    assert!(p.paints[0].visible.area()? > 20.);
    let (commands, expected) = hpgl::encode(&p)?;
    let (decoded, _) = hpgl::readback(&p, &commands, &expected)?;
    let report = p.verify_paths(&decoded)?;
    assert!(report[0].ink_fraction_of_visible_area.unwrap() > 0.7);
    Ok(())
}

#[test]
fn width_tolerance_chooses_pass_counts_and_limits_nominal_overlap() -> Result<()> {
    for (width, passes, span, overlap) in [
        (0.3, 1, 0.4, 0.0),
        (0.4, 1, 0.4, 0.0),
        (0.65, 1, 0.4, 0.0),
        (0.7, 2, 0.7, 0.1),
        (0.9, 2, 0.8, 0.0),
        (1.1, 3, 1.1, 0.05),
    ] {
        let w = plotline::width::layout(width, 0.4, 0.75)?.unwrap();
        assert_eq!(w.passes, passes);
        assert!((w.ideal_span_pt - span).abs() < 1e-8);
        assert!((w.ideal_overlap_pt - overlap).abs() < 1e-8);
    }
    assert_eq!(
        plotline::width::layout(0.699999988, 0.4, 0.75)?
            .unwrap()
            .passes,
        2
    );
    assert!(plotline::width::layout(0.09, 0.4, 0.75)?.is_none());
    assert_eq!(plotline::width::layout(0.4, 0.4, 0.0)?.unwrap().passes, 1);
    assert!(plotline::width::layout(0.4, 0.4, 1.01).is_err());
    assert!(plotline::width::layout(0.4, 0.4, f64::NAN).is_err());
    Ok(())
}

#[test]
fn width_tolerance_survives_hpgl_rounding_without_a_middle_gap() -> Result<()> {
    for width in [0.3, 0.65, 0.7, 0.9, 1.1, 2.0] {
        let p = plan::compile(
            &page(vec![stroke([10., 50.], [90., 50.], width, [0.; 3])]),
            Settings {
                pen_width: 0.4,
                stroke_width_tolerance: Some(0.75),
                ..Settings::default()
            },
        )?;
        let w = p.paints[0]
            .width_layout
            .as_ref()
            .expect("width policy must handle the straight stroke");
        if width < 1.0 {
            assert_eq!(w.passes, if width < 0.7 { 1 } else { 2 });
        }
        assert!((w.planned_span_pt - width).abs() <= 0.3 + 1e-6);
        if w.passes > 1 {
            assert!(0.4 - w.planned_spacing_pt <= 0.1 + 1e-6);
        }
        let (commands, expected) = hpgl::encode(&p)?;
        let (decoded, _) = hpgl::readback(&p, &commands, &expected)?;
        let r = p.verify_paths(&decoded)?;
        assert_eq!(r[0].outside_allowed_area_pt2, 0.0);
        if width < 0.4 {
            assert!(r[0].outside_visible_area_pt2 > 0.0);
        }
        let ink = g::union(
            decoded
                .iter()
                .map(|path| g::round_buffer(&g::line(&path.points)?, 0.2))
                .collect::<Result<_>>()?,
        )?;
        assert!(
            ink.covers(&g::line(&[[20., 50.], [80., 50.]])?)?,
            "a gap appeared between passes at width {width}"
        );
    }
    Ok(())
}

#[test]
fn permitted_thickening_respects_later_paints_clips_and_erased_casings() -> Result<()> {
    let settings = Settings {
        pen_width: 0.4,
        stroke_width_tolerance: Some(0.75),
        ..Settings::default()
    };
    let mut events = vec![
        Event::ClipFill {
            path: rect(10., 10., 80., 80.),
            matrix: I,
            even_odd: false,
        },
        stroke([0., 50.], [100., 50.], 0.3, [0.; 3]),
        Event::PopClip,
        fill(rect(45., 40., 10., 20.), [1.; 3]),
        // This white mark misses the logical 0.3-wide line but intersects its
        // permitted wider envelope. The spatial query must still find it.
        fill(rect(60., 50.16, 10., 2.), [1.; 3]),
    ];
    let p = plan::compile(&page(events.clone()), settings)?;
    assert!(p.paints[0].width_layout.is_some());
    let (commands, expected) = hpgl::encode(&p)?;
    let (decoded, _) = hpgl::readback(&p, &commands, &expected)?;
    p.verify_paths(&decoded)?;
    let ink = g::union(
        decoded
            .iter()
            .map(|path| g::round_buffer(&g::line(&path.points)?, 0.2))
            .collect::<Result<_>>()?,
    )?;
    for paint in p.paints.iter().skip(1) {
        assert!(ink.intersection(&paint.coverage)?.area()? < 1e-7);
    }
    assert!(
        ink.difference(&Geometry::create_rectangle(10., 10., 90., 90.)?)?
            .area()?
            < 1e-7
    );
    events.push(fill(rect(0., 0., 100., 100.), [1.; 3]));
    assert!(plan::compile(&page(events), settings)?.paths.is_empty());
    let roads = plan::compile(
        &page(vec![
            stroke([10., 50.], [90., 50.], 12., [0.; 3]),
            stroke([10., 50.], [90., 50.], 8., [1.; 3]),
        ]),
        settings,
    )?;
    assert_eq!(roads.paints[0].width_status, "contour_fallback");
    let r = roads.verify_paths(&roads.paths)?;
    assert!(r[0].ink_fraction_of_visible_area.unwrap() > 0.9);
    assert!(
        roads
            .paths
            .iter()
            .flat_map(|p| &p.points)
            .any(|p| p[1] < 46.)
    );
    assert!(
        roads
            .paths
            .iter()
            .flat_map(|p| &p.points)
            .any(|p| p[1] > 54.)
    );
    // An arbitrary path in white must fail, even though width tolerance is enabled.
    let bad = vec![PenPath {
        paint_id: 0,
        pen: 1,
        points: vec![[20., 50.], [80., 50.]],
    }];
    assert!(roads.verify_paths(&bad).is_err());
    Ok(())
}

#[test]
fn width_policy_keeps_logical_paint_unchanged_and_nonuniform_strokes_use_contours() -> Result<()> {
    let mut wide = stroke([10., 20.], [30., 20.], 0.65, [1., 0., 0.]);
    if let Event::Stroke { matrix, .. } = &mut wide {
        *matrix = [2., 0., 0., 1., 0., 0.];
    }
    let source = page(vec![
        wide,
        stroke([10., 50.], [90., 50.], 0.3, [0.; 3]),
        fill(rect(40., 40., 20., 20.), [1.; 3]),
    ]);
    let settings = Settings {
        pen_width: 0.4,
        ..Settings::default()
    };
    let strict = plan::compile(&source, settings)?;
    let tolerant = plan::compile(
        &source,
        Settings {
            stroke_width_tolerance: Some(0.75),
            ..settings
        },
    )?;
    for (a, b) in strict.paints.iter().zip(&tolerant.paints) {
        assert!(a.coverage.sym_difference(&b.coverage)?.area()? < 1e-7);
        assert!(a.visible.sym_difference(&b.visible)?.area()? < 1e-7);
    }
    assert_eq!(tolerant.paints[0].width_status, "contour_fallback");
    assert!(tolerant.paints[0].stroke_width.is_none());
    let (commands, expected) = hpgl::encode(&tolerant)?;
    let (decoded, _) = hpgl::readback(&tolerant, &commands, &expected)?;
    let report = tolerant.verify_paths(&decoded)?;
    assert!(report[1].ink_area_pt2 > report[1].ink_inside_visible_area_pt2);
    assert!(report[1].ink_fraction_of_visible_area.unwrap() <= 1.0);
    assert_eq!(report[1].uncovered_required_area_pt2, Some(0.0));
    assert!(tolerant.verify_paths(&[]).is_err());
    Ok(())
}

#[test]
fn narrow_house_corners_stay_single_pass_across_grid_phases() -> Result<()> {
    let mut source: Page = serde_json::from_str(include_str!("fixtures/house-width-corner.json"))?;
    let settings = Settings {
        pen_width: 0.4,
        stroke_width_tolerance: Some(0.75),
        ..Settings::default()
    };
    // Keep the original case, then exercise other rotations and HPGL-grid phases.
    // The exact original corner missed 2.62e-7 pt² under the old erosion test,
    // causing 98.14 mm of travel around a 32.75 mm house.
    let original = source.events[0].clone();
    for (angle, shift) in [(0.0_f64, 0.), (0., 0.013), (13., 0.041), (47., 0.027)] {
        source.events[0] = original.clone();
        if (angle != 0. || shift != 0.)
            && let Event::Stroke { matrix, .. } = &mut source.events[0]
        {
            let (s, c) = angle.to_radians().sin_cos();
            // Rotate the page-space figure around a nearby point; the native
            // PDF fixture has a Y-flip and a 792 pt translation already.
            *matrix = [
                c,
                s,
                s,
                -c,
                180. - 180. * c - 500. * s + shift,
                292. - 180. * s + 500. * c,
            ];
        }
        let p = plan::compile(&source, settings)?;
        let layout = p.paints[0]
            .width_layout
            .as_ref()
            .expect("corner must retain width policy");
        assert_eq!(layout.passes, 1);
        let (commands, expected) = hpgl::encode(&p)?;
        let (decoded, metrics) = hpgl::readback(&p, &commands, &expected)?;
        assert_eq!(
            decoded.len(),
            1,
            "unoccluded house needs one closed traversal"
        );
        assert!(metrics.pen_down_mm > 32.6 && metrics.pen_down_mm < 32.9);
        assert_eq!(
            p.verify_paths(&decoded)?[0].uncovered_required_area_pt2,
            Some(0.)
        );
        // An independent physical continuity check, not just the policy's area test.
        let ink = g::round_buffer(&g::line(&decoded[0].points)?, 0.2)?;
        assert!(
            g::line(&p.paints[0].source_lines[0])?
                .difference(&ink)?
                .length()?
                < 1e-7
        );
    }
    Ok(())
}

#[test]
fn partial_occlusion_adjusts_one_house_side_without_retracing_the_rest() -> Result<()> {
    let source: Page = serde_json::from_str(include_str!("fixtures/house-width-clipped.json"))?;
    let p = plan::compile(
        &source,
        Settings {
            pen_width: 0.4,
            stroke_width_tolerance: Some(0.75),
            ..Settings::default()
        },
    )?;
    let layout = p.paints[0]
        .width_layout
        .as_ref()
        .expect("local cut must not force whole-house contours");
    assert_eq!(layout.passes, 1);
    assert!(layout.locally_adjusted);
    let (commands, expected) = hpgl::encode(&p)?;
    let (decoded, _) = hpgl::readback(&p, &commands, &expected)?;
    let r = p.verify_paths(&decoded)?;
    assert_eq!(r[0].paths, 1);
    assert!(r[0].pen_down_mm > 39.8 && r[0].pen_down_mm < 40.2);
    let lines = g::union(
        decoded
            .iter()
            .filter(|p| p.paint_id == 0)
            .map(|p| g::line(&p.points))
            .collect::<Result<_>>()?,
    )?;
    let ink = g::round_buffer(&lines, 0.2)?;
    assert!(
        ink.intersection(&p.paints[1].coverage)?.area()? < 1e-7,
        "later paint still occludes the finite pen"
    );
    // Far from the clipping mark, there must be exactly one crossing and no
    // nearby offset traces. This fails the previous three-traversal fallback.
    let cross = g::line(&[[230., 207.], [230., 211.]])?;
    let hits = lines.intersection(&cross)?;
    assert_eq!(hits.geometry_type()?, geos::GeometryTypes::Point);
    assert!((hits.get_y()? - 209.844).abs() < 0.08);
    Ok(())
}

#[test]
fn quantized_cut_endpoints_do_not_retrace_overlapping_houses() -> Result<()> {
    let settings = Settings {
        pen_width: 0.4,
        stroke_width_tolerance: Some(0.75),
        ..Settings::default()
    };
    for fixture in [
        include_str!("fixtures/house-width-grid-neighbor.json"),
        include_str!("fixtures/house-width-grid-road.json"),
    ] {
        for shift in [[0., 0.], [0.013, 0.], [0.041, 0.027], [0.05, -0.02]] {
            let mut source: Page = serde_json::from_str(fixture)?;
            for event in &mut source.events {
                let matrix = match event {
                    Event::Fill { matrix, .. }
                    | Event::Stroke { matrix, .. }
                    | Event::ClipFill { matrix, .. }
                    | Event::ClipStroke { matrix, .. } => matrix,
                    _ => continue,
                };
                matrix[4] += shift[0];
                matrix[5] += shift[1];
            }
            let p = plan::compile(&source, settings)?;
            let house = &p.paints[0];
            let layout = house
                .width_layout
                .as_ref()
                .expect("a rounded cut endpoint must not force whole-house contours");
            assert_eq!(layout.passes, 1);
            assert!(layout.locally_adjusted);
            let (commands, expected) = hpgl::encode(&p)?;
            let (decoded, _) = hpgl::readback(&p, &commands, &expected)?;
            let report = p.verify_paths(&decoded)?;
            let perimeter = house
                .source_lines
                .iter()
                .map(|line| g::line(line)?.length().map_err(Into::into))
                .sum::<Result<f64>>()?;
            assert!(report[0].pen_down_mm <= perimeter * 25.4 / 72. * 1.02 + 0.05);
            assert!(report[0].paths > 0);
            assert_eq!(report[0].uncovered_required_area_pt2, Some(0.));
            let lines = g::union(
                decoded
                    .iter()
                    .filter(|path| path.paint_id == house.id)
                    .map(|path| g::line(&path.points))
                    .collect::<Result<_>>()?,
            )?;
            let ink = g::round_buffer(&lines, settings.pen_width / 2.)?;
            for later in &p.paints[1..] {
                assert!(
                    ink.intersection(&later.coverage)?.area()? < 1e-7,
                    "endpoint correction crossed later paint"
                );
            }
        }
    }
    Ok(())
}

#[test]
fn width_verification_still_rejects_missing_tracks_and_middle_gaps() -> Result<()> {
    let p = plan::compile(
        &page(vec![stroke([10., 50.], [90., 50.], 0.9, [0.; 3])]),
        Settings {
            pen_width: 0.4,
            stroke_width_tolerance: Some(0.75),
            ..Settings::default()
        },
    )?;
    // Both examples fit within the allowed outer envelope, but leave required ink
    // missing. Neither the new corner rule nor local repair may excuse this.
    for ys in [vec![50.], vec![49.7, 50.3]] {
        let broken: Vec<_> = ys
            .into_iter()
            .map(|y| PenPath {
                paint_id: 0,
                pen: 1,
                points: vec![[10., y], [90., y]],
            })
            .collect();
        assert!(p.verify_paths(&broken).is_err());
    }
    Ok(())
}

#[test]
fn local_adjustment_obeys_original_clip_and_white_cut_without_a_bridge() -> Result<()> {
    let mut source: Page = serde_json::from_str(include_str!("fixtures/house-width-clipped.json"))?;
    let house = source.events[0].clone();
    source.events = vec![
        Event::ClipFill {
            path: rect(210.5, 200., 60., 40.),
            matrix: I,
            even_odd: false,
        },
        house,
        Event::PopClip,
        fill(rect(230., 220., 5., 15.), [1.; 3]),
    ];
    source.sources.clear();
    let p = plan::compile(
        &source,
        Settings {
            pen_width: 0.4,
            stroke_width_tolerance: Some(0.75),
            ..Settings::default()
        },
    )?;
    let layout = p.paints[0]
        .width_layout
        .as_ref()
        .expect("clip should adjust just the affected side");
    assert_eq!(layout.passes, 1);
    assert!(layout.locally_adjusted);
    let (commands, expected) = hpgl::encode(&p)?;
    let (decoded, _) = hpgl::readback(&p, &commands, &expected)?;
    let report = p.verify_paths(&decoded)?;
    assert!(report[0].pen_down_mm < 40.2);
    let ink = g::union(
        decoded
            .iter()
            .map(|p| g::round_buffer(&g::line(&p.points)?, 0.2))
            .collect::<Result<_>>()?,
    )?;
    assert!(
        ink.difference(&Geometry::create_rectangle(210.5, 200., 270.5, 240.)?)?
            .area()?
            < 1e-7
    );
    assert!(ink.intersection(&p.paints[1].coverage)?.area()? < 1e-7);
    Ok(())
}

#[test]
fn same_color_inks_keep_distinct_widths_and_reject_swapped_identity() -> Result<()> {
    let mut page = page(vec![
        stroke([10., 20.], [90., 20.], 0.5, [0.; 3]),
        stroke([10., 60.], [90., 60.], 2., [0.; 3]),
        fill(rect(50., 50., 50., 30.), [1.; 3]),
    ]);
    page.inks = vec![
        Ink {
            id: "fine".into(),
            color: [0.; 3],
            width: 0.5,
        },
        Ink {
            id: "broad".into(),
            color: [0.; 3],
            width: 2.,
        },
    ];
    page.paint_inks = [(0, 1), (1, 2)].into();
    let p = plan::compile(
        &page,
        Settings {
            stroke_width_tolerance: Some(0.75),
            ..Settings::default()
        },
    )?;
    assert_eq!(p.pen_widths, vec![0.5, 2.]);
    assert!(p.paths.iter().any(|path| path.pen == 1));
    assert!(p.paths.iter().any(|path| path.pen == 2));
    let (text, expected) = hpgl::encode(&p)?;
    let (decoded, _) = hpgl::readback(&p, &text, &expected)?;
    p.verify_paths(&decoded)?;
    let end = decoded
        .iter()
        .filter(|path| path.pen == 2)
        .flat_map(|path| &path.points)
        .map(|p| p[0])
        .fold(0., f64::max);
    assert!(
        end < 49.,
        "Broad pen must clear the later white paint with its own radius"
    );
    let mut wrong = decoded.clone();
    wrong[0].pen = if wrong[0].pen == 1 { 2 } else { 1 };
    assert!(
        p.verify_paths(&wrong).is_err(),
        "Same RGB cannot excuse the wrong physical pen"
    );
    page.paint_inks.insert(2, 1);
    assert!(
        plan::compile(&page, Settings::default()).is_err(),
        "White is not an ink"
    );
    Ok(())
}

#[test]
fn omitted_fill_still_occludes_earlier_strokes() -> Result<()> {
    let p = plan::compile(
        &page(vec![
            stroke([10., 50.], [90., 50.], 2., [0.; 3]),
            fill(rect(40., 40., 20., 20.), [1., 0., 0.]),
        ]),
        Settings {
            fill_style: FillStyle::None,
            stroke_width_tolerance: Some(0.75),
            ..Settings::default()
        },
    )?;
    assert!(p.paths.iter().all(|path| path.paint_id == 0));
    assert!(!p.paths.is_empty());
    p.verify_paths(&p.paths)?;
    assert!(
        p.paths
            .iter()
            .flat_map(|path| &path.points)
            .all(|p| p[0] < 40. || p[0] > 60.)
    );
    Ok(())
}

#[test]
fn feathering_uses_original_boundary_and_shared_two_band_lattice() -> Result<()> {
    let mut shape = rect(10., 10., 80., 80.);
    // A source hole is an edge; a later white knockout must not become one.
    shape.extend(rect(42., 42., 16., 16.));
    let mut event = fill(shape, [0.; 3]);
    if let Event::Fill { even_odd, .. } = &mut event {
        *even_odd = true;
    }
    let p = plan::compile(
        &page(vec![event, fill(rect(25., 35., 5., 30.), [1.; 3])]),
        Settings {
            fill_style: FillStyle::Feathered,
            hatch_spacing: Some(2.),
            hatch_angle: Some(0.),
            feather_distance: Some(10.),
            ..Settings::default()
        },
    )?;
    p.verify_paths(&p.paths)?;
    let ink = g::union(
        p.paths
            .iter()
            .map(|path| g::line(&path.points))
            .collect::<Result<_>>()?,
    )?;
    assert!(
        ink.intersects(&point(15., 22.)?)?,
        "All lattice lines reach the dense band"
    );
    assert!(
        !ink.intersects(&point(18., 22.)?)?,
        "Odd lattice lines stop at the half-distance"
    );
    assert!(
        ink.intersects(&point(18., 24.)?)?,
        "Even lattice lines continue into the sparse band"
    );
    assert!(!ink.intersects(&point(32., 24.)?)?, "Interior stays blank");
    assert!(
        ink.intersects(&point(38., 48.)?)?,
        "Original holes receive feathering"
    );
    assert!(
        !ink.intersects(&point(24., 50.)?)?,
        "Later knockout cannot create a new feather band"
    );
    Ok(())
}

#[test]
fn reused_verification_matches_independent_checks_and_rechecks_changes() -> Result<()> {
    let p = plan::compile(
        &page(vec![
            stroke([10., 20.], [30., 20.], 1., [0.; 3]),
            stroke([50., 70.], [80., 70.], 1., [0.; 3]),
        ]),
        Settings {
            stroke_width_tolerance: Some(0.75),
            ..Settings::default()
        },
    )?;
    let (_, same, reused) = p.verify_path_pair(&p.paths, &p.paths)?;
    assert_eq!(reused, p.paints.len());
    assert_eq!(
        serde_json::to_value(&same)?,
        serde_json::to_value(p.verify_paths(&p.paths)?)?
    );
    // A changed route affects incoming travel on otherwise identical paint.
    let mut reordered = p.paths.clone();
    reordered.reverse();
    let (_, checked, _) = p.verify_path_pair(&p.paths, &reordered)?;
    assert_eq!(
        serde_json::to_value(&checked)?,
        serde_json::to_value(p.verify_paths(&reordered)?)?
    );
    assert!(p.paints.iter().all(|paint| paint.width_layout.is_some()));
    assert!(p.verify_path_pair(&p.paths, &[]).is_err());
    let mut outside = p.paths.clone();
    outside[0].points[0] = [99., 99.];
    assert!(p.verify_path_pair(&p.paths, &outside).is_err());
    let mut wrong_pen = p.paths.clone();
    wrong_pen[0].pen = 8;
    assert!(p.verify_path_pair(&p.paths, &wrong_pen).is_err());
    let (commands, expected) = hpgl::encode(&p)?;
    let (decoded, _) = hpgl::readback(&p, &commands, &expected)?;
    let (_, checked, _) = p.verify_path_pair(&p.paths, &decoded)?;
    assert_eq!(
        serde_json::to_value(&checked)?,
        serde_json::to_value(p.verify_paths(&decoded)?)?
    );
    Ok(())
}

#[test]
fn simple_clip_path_matches_general_pdf_fill_rules_and_degenerate_fallbacks() -> Result<()> {
    let square = vec![[0., 0.], [10., 0.], [10., 10.], [0., 10.], [0., 0.]];
    let mut reverse = square.clone();
    reverse.reverse();
    let inner = vec![[3., 3.], [7., 3.], [7., 7.], [3., 7.], [3., 3.]];
    let mut inner_reverse = inner.clone();
    inner_reverse.reverse();
    let cases = vec![
        vec![],
        vec![vec![]],
        vec![vec![[0., 0.]]],
        vec![vec![[0., 0.], [1., 1.]]],
        vec![vec![[0., 0.], [1., 1.], [0., 0.]]],
        vec![vec![[0., 0.], [0., 0.], [0., 0.], [0., 0.]]],
        vec![vec![[0., 0.], [5., 0.], [10., 0.], [0., 0.]]],
        vec![square.clone()],
        vec![reverse],
        vec![square[..4].to_vec()], // Implicit close.
        vec![vec![[0., 0.], [10., 0.], [10., 0.], [10., 10.], [0., 10.]]],
        vec![vec![
            [0., 0.],
            [10., 0.],
            [10., 3.],
            [3., 3.],
            [3., 10.],
            [0., 10.],
        ]],
        vec![vec![[0., 0.], [10., 10.], [0., 10.], [10., 0.]]], // Bowtie.
        vec![vec![[0., 0.], [10., 0.], [5., 0.], [5., 5.], [0., 5.]]], // Spike.
        vec![vec![[0., 0.], [10., 0.], [10., 1e-7], [0., 1e-7]]],
        vec![square.clone(), square.clone()], // Duplicate rings have different fill rules.
        vec![square.clone(), inner],
        vec![square.clone(), inner_reverse],
        vec![
            square.clone(),
            square.iter().map(|p| [p[0] + 20., p[1]]).collect(),
        ],
        vec![
            square
                .iter()
                .chain(square.iter().skip(1))
                .copied()
                .collect(),
        ], // Repeated traversal.
    ];
    for (case, paths) in cases.iter().enumerate() {
        for matrix in [
            I,
            [-1., 0., 0., 1., 20., 0.],
            [0.8, 0.6, -0.6, 0.8, 3., 7.],
            [2., 0.3, 0.4, 1., 5., 9.],
        ] {
            let world: Vec<Vec<Point>> = paths
                .iter()
                .map(|p| p.iter().map(|p| g::map_point(*p, matrix)).collect())
                .collect();
            for even_odd in [false, true] {
                let expected = g::fill(&world, even_odd)?;
                let actual = g::clip_fill(&world, even_odd)?;
                assert!(actual.is_valid()?, "case {case}");
                assert_eq!(actual.is_empty()?, expected.is_empty()?, "case {case}");
                assert!(
                    actual.sym_difference(&expected)?.area()? <= 1e-9,
                    "case {case}, even_odd={even_odd}"
                );
            }
        }
    }
    Ok(())
}
