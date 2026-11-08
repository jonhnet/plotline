use anyhow::Result;
use geos::Geom;
use plotline::{continuity, geometry as g, hpgl, model::*, plan};
use std::collections::BTreeMap;
fn rect(x: f64, y: f64, w: f64, h: f64) -> Vec<Command> {
    vec![
        Command::Move { p: [x, y] },
        Command::Line { p: [x + w, y] },
        Command::Line { p: [x + w, y + h] },
        Command::Line { p: [x, y + h] },
        Command::Close,
    ]
}
const I: Matrix = [1., 0., 0., 1., 0., 0.];

#[test]
fn scanned_raster_swatch_colors_preview_without_changing_hpgl_pen_identity() -> Result<()> {
    let page = Page {
        version: 1,
        engine: "scanned swatch fixture".into(),
        units: "pt".into(),
        y_axis: "down".into(),
        bounds: [0., 0., 100., 100.],
        sources: BTreeMap::new(),
        inks: vec![Ink {
            id: "yellow".into(),
            color: [1., 1., 0.],
            width: 1.,
        }],
        paint_inks: BTreeMap::from([(0, 1)]),
        events: vec![Event::RasterPaths {
            path: rect(0., 0., 100., 100.),
            matrix: I,
            color: [237. / 255., 174. / 255., 96. / 255.],
            lines: vec![vec![[10., 10.], [90., 10.]]],
        }],
    };
    let plan = plan::compile(&page, Settings::default())?;
    let (hpgl, decoded) = hpgl::encode(&plan)?;
    assert!(hpgl.contains("pen id=1 r=255 g=255 b=0"));
    for paths in [None, Some(decoded.as_slice())] {
        let svg = plotline::output::svg(&plan, paths, false)?;
        assert!(svg.contains("stroke=\"rgb(237,174,96)\""));
        assert!(!svg.contains("stroke=\"rgb(255,255,0)\""));
    }
    Ok(())
}

fn fill(path: Vec<Command>, color: Color) -> Event {
    Event::Fill {
        path,
        matrix: I,
        color,
        even_odd: false,
        role: PaintRole::Artwork,
    }
}
#[test]
fn image_channels_overlap_but_opaque_footprint_and_later_paints_occlude() -> Result<()> {
    let page = Page {
        version: 1,
        engine: "raster fixture".into(),
        units: "pt".into(),
        y_axis: "down".into(),
        bounds: [0., 0., 100., 100.],
        sources: BTreeMap::new(),
        inks: vec![
            Ink {
                id: "c".into(),
                color: [0., 0.6, 0.5],
                width: 1.,
            },
            Ink {
                id: "m".into(),
                color: [1., 0., 0.5],
                width: 2.,
            },
        ],
        paint_inks: BTreeMap::from([(0, 1), (2, 1), (3, 2)]),
        events: vec![
            fill(rect(0., 0., 100., 100.), [0., 0.6, 0.5]),
            fill(rect(10., 10., 80., 80.), [1.; 3]),
            Event::RasterPaths {
                path: rect(10., 10., 80., 80.),
                matrix: I,
                color: [0., 0.6, 0.5],
                lines: vec![vec![[0., 50.], [100., 50.]]],
            },
            Event::RasterPaths {
                path: rect(10., 10., 80., 80.),
                matrix: I,
                color: [1., 0., 0.5],
                lines: vec![vec![[50., 0.], [50., 100.]]],
            },
            fill(rect(70., 0., 30., 100.), [1.; 3]),
        ],
    };
    let settings = Settings {
        fill_style: FillStyle::None,
        thin_detail_thickening: 1.,
        ..Settings::default()
    };
    let plan = plan::compile(&page, settings)?;
    assert!(plan.paths.iter().all(|p| p.paint_id != 0));
    let c = plan.paths.iter().find(|p| p.paint_id == 2).unwrap();
    let m = plan.paths.iter().find(|p| p.paint_id == 3).unwrap();
    assert!(
        g::line(&c.points)?.intersects(&g::line(&m.points)?)?,
        "sibling channels must cross"
    );
    let (_, decoded) = hpgl::encode(&plan)?;
    let reports = plan.verify_paths(&decoded)?;
    assert!(reports.iter().all(|p| p.outside_allowed_area_pt2 <= 1e-7));
    for p in &decoded {
        let footprint = g::round_buffer(&g::line(&p.points)?, plan.pen_widths[p.pen - 1] / 2.)?;
        assert!(footprint.get_extent()?[2] <= 70.);
        assert!(footprint.get_extent()?[0] >= 10.);
    }
    assert!(
        reports
            .iter()
            .filter(|r| r.representation == "raster_paths")
            .all(|r| r.missing_detail_bounds.is_empty())
    );
    // Deliberately move a decoded raster stroke outside the image. The
    // independent footprint audit must reject it even if planning was bypassed.
    let mut escaped = decoded.clone();
    escaped[0].points[0] = [5., 5.];
    assert!(plan.verify_paths(&escaped).is_err());
    assert!(
        reports
            .iter()
            .filter(|r| r.coverage_kind == "image_footprint")
            .all(|r| r.ink_area_pt2.is_none() && r.unrepresented_area_pt2.is_none())
    );
    Ok(())
}
#[test]
fn highlight_gaps_are_not_joined_by_generic_continuity() -> Result<()> {
    let page = Page {
        version: 1,
        engine: "raster fixture".into(),
        units: "pt".into(),
        y_axis: "down".into(),
        bounds: [0., 0., 100., 100.],
        sources: BTreeMap::new(),
        inks: vec![Ink {
            id: "k".into(),
            color: [0.; 3],
            width: 1.,
        }],
        paint_inks: BTreeMap::from([(0, 1)]),
        events: vec![Event::RasterPaths {
            path: rect(0., 0., 100., 100.),
            matrix: I,
            color: [0.; 3],
            lines: vec![vec![[10., 10.], [20., 10.]], vec![[20.8, 10.], [30., 10.]]],
        }],
    };
    let plan = plan::compile(&page, Settings::default())?;
    let encoded = continuity::encode(&plan)?;
    assert_eq!(encoded.report.lifts_removed, 0);
    assert_eq!(encoded.report.physical_runs, 2);
    encoded.readback(&plan)?;
    Ok(())
}
