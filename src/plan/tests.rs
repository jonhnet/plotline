use super::*;
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

fn fixture() -> Page {
    let style = Stroke {
        width: 1.2,
        cap: "Round".into(),
        join: "Round".into(),
        miter_limit: 10.,
        dash_phase: 0.,
        dashes: vec![],
    };
    let mut events = vec![Event::ClipFill {
        path: rect(5., 5., 190., 190.),
        matrix: I,
        even_odd: false,
    }];
    for i in 0..130 {
        events.push(Event::Stroke {
            path: vec![
                Command::Move {
                    p: [0., 10. + i as f64],
                },
                Command::Line {
                    p: [200., 10. + i as f64],
                },
            ],
            matrix: I,
            style: style.clone(),
            color: if i % 2 == 0 { [1., 0., 0.] } else { [0.; 3] },
        });
    }
    // A nested stroked clip cuts white paint, then popping restores the
    // enclosing clip; both must outlive sequential clip-stack traversal.
    events.push(Event::ClipStroke {
        path: rect(60., 40., 50., 50.),
        matrix: I,
        style: Stroke {
            width: 4.,
            ..style.clone()
        },
    });
    events.push(Event::Fill {
        path: rect(50., 30., 80., 80.),
        matrix: I,
        even_odd: false,
        color: [1.; 3],
        role: PaintRole::Artwork,
    });
    events.push(Event::PopClip);
    let mut rings = rect(20., 150., 80., 40.);
    rings.extend(rect(40., 160., 30., 20.));
    events.push(Event::Fill {
        path: rings,
        matrix: I,
        even_odd: true,
        color: [0., 1., 0.],
        role: PaintRole::Artwork,
    });
    // A color in an empty clip must not allocate a pen in the exact palette.
    events.push(Event::ClipFill {
        path: rect(210., 210., 1., 1.),
        matrix: I,
        even_odd: false,
    });
    events.push(Event::Stroke {
        path: rect(211., 211., 2., 2.),
        matrix: I,
        style,
        color: [0., 0., 1.],
    });
    events.extend([Event::PopClip, Event::PopClip]);
    Page {
        version: 1,
        engine: "parallel fixture".into(),
        units: "pt".into(),
        y_axis: "down".into(),
        bounds: [0., 0., 200., 200.],
        events,
        inks: vec![],
        paint_inks: Default::default(),
        sources: Default::default(),
    }
}

fn compare(page: &Page, settings: Settings) -> Result<()> {
    let a = compile_with_workers(page, settings, Some(1), true)?;
    for workers in [4, 8, 16] {
        let b = compile_with_workers(page, settings, Some(workers), true)?;
        assert_eq!(a.paths, b.paths);
        assert_eq!(a.palette, b.palette);
        assert_eq!(a.pen_widths, b.pen_widths);
        for (a, b) in a.paints.iter().zip(&b.paints) {
            assert_eq!(a.id, b.id);
            for (x, y) in [
                (&a.coverage, &b.coverage),
                (&a.envelope, &b.envelope),
                (&a.visible, &b.visible),
                (&a.allowed, &b.allowed),
            ] {
                assert_eq!(x.to_wkb()?, y.to_wkb()?);
            }
        }
        let (serial, _) = a.verify_paths_impl(&a.paths, "Test", None, Some(1))?;
        let (parallel, _) = b.verify_paths_impl(&b.paths, "Test", None, Some(workers))?;
        assert_eq!(
            serde_json::to_value(&serial)?,
            serde_json::to_value(parallel)?
        );
        let (hpgl, expected) = crate::hpgl::encode(&b)?;
        assert_eq!(hpgl, crate::hpgl::encode(&a)?.0);
        let (decoded, _) = crate::hpgl::readback(&b, &hpgl, &expected)?;
        b.verify_paths_impl(&decoded, "Test", None, Some(workers))?;
        let mut bad = b.paths.clone();
        bad[0].points = vec![[0., 0.], [200., 200.]];
        assert!(
            b.verify_paths_impl(&bad, "Test", None, Some(workers))
                .is_err()
        );
    }
    Ok(())
}

#[test]
fn parallel_geometry_preserves_clips_palette_order_and_all_fill_policies() -> Result<()> {
    let page = fixture();
    for fill_style in [
        FillStyle::None,
        FillStyle::Hatch,
        FillStyle::Crosshatch,
        FillStyle::Outline,
        FillStyle::Feathered,
        FillStyle::Solid,
    ] {
        for stroke_width_tolerance in [None, Some(0.75)] {
            compare(
                &page,
                Settings {
                    fill_style,
                    stroke_width_tolerance,
                    feather_distance: Some(12.),
                    ..Settings::default()
                },
            )?;
        }
    }
    let exact = compile_with_workers(&page, Settings::default(), Some(4), true)?;
    assert_eq!(exact.palette, vec![[1., 0., 0.], [0.; 3], [0., 1., 0.]]);
    Ok(())
}

#[test]
fn parallel_geometry_preserves_explicit_same_color_ink_widths_and_errors() -> Result<()> {
    let mut page = fixture();
    page.inks = vec![
        Ink {
            id: "fine".into(),
            color: [0.; 3],
            width: 0.8,
        },
        Ink {
            id: "broad".into(),
            color: [0.; 3],
            width: 1.2,
        },
    ];
    for (id, event) in page.events.iter().enumerate() {
        if let Event::Stroke { color, .. } | Event::Fill { color, .. } = event
            && !is_white(*color)
        {
            page.paint_inks.insert(id, id % 2 + 1);
        }
    }
    for thin_detail_thickening in [0., 0.75] {
        compare(
            &page,
            Settings {
                stroke_width_tolerance: Some(0.75),
                thin_detail_thickening,
                ..Settings::default()
            },
        )?;
    }
    page.paint_inks.remove(&65);
    assert!(compile_with_workers(&page, Settings::default(), Some(4), true).is_err());
    page.events.insert(0, Event::PopClip);
    assert!(compile_with_workers(&page, Settings::default(), Some(4), true).is_err());
    Ok(())
}

#[test]
fn preview_defers_only_white_area_diagnostics_and_still_rejects_hidden_ink() -> Result<()> {
    let mut page = fixture();
    // Later colored paint covers part of the clipped white ring.
    page.events.push(Event::Fill {
        path: rect(80., 35., 5., 60.),
        matrix: I,
        even_odd: false,
        color: [0., 0., 1.],
        role: PaintRole::Artwork,
    });
    let settings = Settings {
        stroke_width_tolerance: Some(0.75),
        ..Settings::default()
    };
    let full = compile(&page, settings)?;
    let full_report = full.verify_paths(&full.paths)?;
    for workers in [1, 4, 8, 16] {
        let preview = compile_with_workers(&page, settings, Some(workers), false)?;
        assert_eq!(preview.paths, full.paths);
        assert_eq!(preview.compile_stats.visibility_white_areas_deferred, 1);
        let report = preview.verify_paths(&preview.paths)?;
        for (a, b) in full_report.iter().zip(&report) {
            let mut expected = serde_json::to_value(a)?;
            if a.white {
                assert!(a.visible_area_pt2.unwrap() > 0.);
                assert!(a.visible_area_pt2.unwrap() < a.coverage_area_pt2);
                assert_eq!(b.visible_area_pt2, None);
                expected["visible_area_pt2"] = serde_json::Value::Null;
            }
            assert_eq!(serde_json::to_value(b)?, expected);
        }
        assert_eq!(
            crate::hpgl::encode(&full)?.0,
            crate::hpgl::encode(&preview)?.0
        );
        assert_eq!(
            crate::output::svg(&full, None, false)?,
            crate::output::svg(&preview, None, false)?
        );
        assert!(crate::output::svg(&preview, None, true).is_err());
        let white = preview.paints.iter().find(|p| is_white(p.color)).unwrap();
        let mut illegal = preview.paths[0].clone();
        illegal.paint_id = white.id;
        assert!(
            preview
                .verify_paths(&[illegal])
                .unwrap_err()
                .to_string()
                .contains("Incorrect pen mapping")
        );
        // The original y=40 stroke covers this segment, but the white ring
        // erases it. Its allowed-width envelope must also respect that erasure.
        let paint = preview.paints.iter().find(|p| p.id == 31).unwrap();
        assert!(
            paint
                .coverage
                .covers(&g::line(&[[70., 40.], [75., 40.]])?)?
        );
        let mut illegal = preview.paths.clone();
        illegal.push(PenPath {
            paint_id: paint.id,
            pen: paint.pen.unwrap(),
            points: vec![[70., 40.], [75., 40.]],
        });
        assert!(
            preview
                .verify_paths(&illegal)
                .unwrap_err()
                .to_string()
                .contains("escapes allowed region")
        );
    }
    Ok(())
}

// The 0.65 pt forest lines in Paine Field disappeared when a 0.700 mm
// requested pen was replaced by its measured 0.723 mm diameter. Both fit
// the 0.75 allowance; the conservative corridor was empty or just corner pockets.
#[test]
fn narrow_strokes_try_verified_clearance_after_guard_erases_corridor() -> Result<()> {
    for pen_mm in [0.7, 0.711, 0.714, 0.723] {
        for thickening in [0., 0.75] {
            for cut in [false, true] {
                for crossing in [false, true] {
                    let points: Vec<Point> = if crossing {
                        vec![
                            [20.017, 40.023],
                            [180.011, 140.019],
                            [20.017, 140.019],
                            [180.011, 40.023],
                        ]
                    } else {
                        vec![[20.017, 40.023], [180.011, 140.019]]
                    };
                    let source_length: f64 = points
                        .windows(2)
                        .map(|p| (p[1][0] - p[0][0]).hypot(p[1][1] - p[0][1]))
                        .sum();
                    let path = points
                        .iter()
                        .enumerate()
                        .map(|(i, p)| {
                            if i == 0 {
                                Command::Move { p: *p }
                            } else {
                                Command::Line { p: *p }
                            }
                        })
                        .collect();
                    let mut events = vec![
                        Event::ClipFill {
                            path: rect(10., 10., 180., 180.),
                            matrix: I,
                            even_odd: false,
                        },
                        Event::Stroke {
                            path,
                            matrix: I,
                            style: Stroke {
                                width: 0.6499999761581421,
                                cap: "Round".into(),
                                join: "Round".into(),
                                miter_limit: 10.,
                                dash_phase: 0.,
                                dashes: vec![],
                            },
                            color: [0., 0.5, 0.],
                        },
                    ];
                    if cut {
                        events.push(Event::Fill {
                            path: rect(95., 0., 10., 200.),
                            matrix: I,
                            even_odd: false,
                            color: [1.; 3],
                            role: PaintRole::Artwork,
                        });
                    }
                    events.push(Event::PopClip);
                    let mut page = Page {
                        version: 1,
                        engine: "narrow stroke clearance regression".into(),
                        units: "pt".into(),
                        y_axis: "down".into(),
                        bounds: [0., 0., 200., 200.],
                        events,
                        inks: vec![],
                        paint_inks: Default::default(),
                        sources: Default::default(),
                    };
                    let settings = Settings {
                        pen_width: pen_mm * 72. / 25.4,
                        flatten_tolerance: 0.01,
                        stroke_width_tolerance: Some(0.75),
                        thin_detail_thickening: thickening,
                        ..Settings::default()
                    };
                    let plan = compile(&page, settings)?;
                    let (hpgl, expected) = crate::hpgl::encode(&plan)?;
                    let (decoded, metrics) = crate::hpgl::readback(&plan, &hpgl, &expected)?;
                    plan.verify_paths(&decoded)?;
                    assert!(
                        metrics.pen_down_mm > source_length * 25.4 / 72. * 0.88,
                        "{pen_mm} mm, recovery {thickening}, cut {cut}, crossing {crossing}: {:?}",
                        metrics
                    );
                    assert_eq!(plan.paints[0].width_status, "width_tolerance");
                    if !crossing {
                        assert_eq!(plan.paths.len(), if cut { 2 } else { 1 });
                    }
                    if cut {
                        for path in &decoded {
                            for point in &path.points {
                                assert!(point[0] < 95. || point[0] > 105.);
                            }
                        }
                    }
                    // A hard original clip still wins over width allowances.
                    page.events[0] = Event::ClipStroke {
                        path: vec![
                            Command::Move { p: points[0] },
                            Command::Line { p: points[1] },
                        ],
                        matrix: I,
                        style: Stroke {
                            width: 0.65,
                            cap: "Round".into(),
                            join: "Round".into(),
                            miter_limit: 10.,
                            dash_phase: 0.,
                            dashes: vec![],
                        },
                    };
                    assert!(compile(&page, settings)?.paths.is_empty());
                }
            }
        }
    }
    Ok(())
}

#[test]
fn reused_allowances_match_explicit_intersections_with_clips_and_overpaint() -> Result<()> {
    // Compare against the original explicit-intersection recipe across equal
    // and unequal allowances, including text (which has no stroke allowance).
    let mut page = fixture();
    page.events = vec![
        Event::Stroke {
            path: rect(10., 20., 60., 25.),
            matrix: I,
            color: [0.; 3],
            style: Stroke {
                width: 0.8,
                cap: "Round".into(),
                join: "Miter".into(),
                miter_limit: 10.,
                dash_phase: 0.,
                dashes: vec![],
            },
        },
        Event::Fill {
            path: rect(20., 10., 0.35, 60.),
            matrix: I,
            color: [0.; 3],
            even_odd: false,
            role: PaintRole::Text,
        },
    ];
    let clip = Geometry::create_rectangle(15., 15., 65., 65.)?;
    let overpaint = Geometry::create_rectangle(35., 15., 40., 65.)?;
    for stroke_width_mode in [StrokeWidthMode::Preserve, StrokeWidthMode::Pen] {
        for stroke_width_tolerance in [None, Some(0.25), Some(0.75)] {
            for thin_detail_thickening in [0.25, 0.75, 1.] {
                let settings = Settings {
                    pen_width: 1.,
                    stroke_width_tolerance,
                    thin_detail_thickening,
                    stroke_width_mode,
                    ..Settings::default()
                };
                for (id, event) in page.events.iter().enumerate() {
                    let make = || -> Result<Paint> {
                        let mut p = build_paint(&page, id, event, &clip, &[], settings)?;
                        p.visible = p.coverage.difference(&overpaint)?;
                        p.allowed = p.envelope.difference(&overpaint)?;
                        Ok(p)
                    };
                    let mut fast = make()?;
                    let actual = plan_paint(&mut fast, page.bounds, settings)?.unwrap_or_default();
                    let mut reference = make()?;
                    let recovery = reference.allowed.intersection(&g::round_buffer(
                        &reference.coverage,
                        thin_detail_thickening / 2.,
                    )?)?;
                    let margin = if reference.stroke_width.is_some() {
                        stroke_width_tolerance.unwrap_or(0.) / 2.
                    } else {
                        0.
                    };
                    reference.allowed = reference
                        .allowed
                        .intersection(&g::round_buffer(&reference.coverage, margin)?)?;
                    let mut expected =
                        plan_paint_standard(&mut reference, page.bounds, settings, None)?
                            .unwrap_or_default();
                    let extra = crate::thin::recover(&reference, &expected, &recovery, settings)?;
                    if !extra.is_empty() {
                        reference.allowed = reference.allowed.union(&recovery)?;
                        reference.width_status = "thin_detail_recovery";
                        expected.extend(extra);
                    }
                    assert_eq!(fast.width_status, reference.width_status);
                    assert!(fast.allowed.sym_difference(&reference.allowed)?.area()? < 1e-7);
                    let footprint = |paths: &[Vec<Point>]| -> Result<Geometry> {
                        g::union(
                            paths
                                .iter()
                                .map(|p| g::round_buffer(&g::line(p)?, 0.5))
                                .collect::<Result<_>>()?,
                        )
                    };
                    let ink = footprint(&actual)?;
                    assert!(ink.sym_difference(&footprint(&expected)?)?.area()? < 1e-7);
                    assert!(ink.difference(&clip)?.area()? < 1e-7);
                    assert!(ink.intersection(&overpaint)?.area()? < 1e-7);
                }
            }
        }
    }
    Ok(())
}

#[test]
fn subgrid_clearance_preserves_crosshatch_centerlines() -> Result<()> {
    let mut events = Vec::new();
    let mut lines = Vec::new();
    for direction in [-1., 1.] {
        for i in -18..=18 {
            let y = 100.023 + i as f64 * 10.8 * std::f64::consts::SQRT_2;
            let points = [
                [0.017, y - direction * 100.],
                [200.011, y + direction * 100.],
            ];
            events.push(Event::Stroke {
                path: vec![
                    Command::Move { p: points[0] },
                    Command::Line { p: points[1] },
                ],
                matrix: I,
                color: [0., 0.5, 0.],
                style: Stroke {
                    width: 0.6499999761581421,
                    cap: "Round".into(),
                    join: "Round".into(),
                    miter_limit: 10.,
                    dash_phase: 0.,
                    dashes: vec![],
                },
            });
            lines.push(g::line(&points)?);
        }
    }
    let source =
        g::union(lines)?.intersection(&Geometry::create_rectangle(50., 50., 150., 150.)?)?;
    let page = Page {
        version: 1,
        engine: "subgrid hatch clearance".into(),
        units: "pt".into(),
        y_axis: "down".into(),
        bounds: [0., 0., 200., 200.],
        events,
        inks: vec![],
        paint_inks: Default::default(),
        sources: Default::default(),
    };
    for pen_mm in [0.710, 0.711, 0.712, 0.713, 0.714, 0.723] {
        let settings = Settings {
            pen_width: pen_mm * 72. / 25.4,
            stroke_width_tolerance: Some(0.75),
            thin_detail_thickening: 0.75,
            ..Settings::default()
        };
        let plan = compile(&page, settings)?;
        let (encoded, expected) = crate::hpgl::encode(&plan)?;
        let (decoded, _) = crate::hpgl::readback(&plan, &encoded, &expected)?;
        plan.verify_paths(&decoded)?;
        let ink = g::union(
            decoded
                .iter()
                .map(|p| g::round_buffer(&g::line(&p.points)?, settings.pen_width / 2.))
                .collect::<Result<_>>()?,
        )?;
        let missing = source.difference(&ink)?.length()?;
        assert!(
            missing < 1e-7,
            "{pen_mm} mm pen loses {missing} pt of hatch centerlines"
        );
    }
    Ok(())
}
