//! Paint-order occlusion with component-local overlays.
//!
//! A whole-paint bbox can span hundreds of distant islands. Index each polygon,
//! reuse GEOS prepared predicates, and restrict complex occluders to a padded
//! target bbox before union. The padding only keeps artificial clip edges away
//! from the target; it never enlarges permitted ink. The occluder index retains every color.
//! Coverage and width envelopes retain separate, exact set differences.
use crate::{
    geometry as g,
    model::is_white,
    plan::{CompileStats, Paint},
};
use anyhow::Result;
use geos::{Geom, Geometry, GeometryTypes, PreparedGeometry, STRtree, SpatialIndex};
use std::{
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc,
    },
    time::Instant,
};

fn polygons<G: Geom>(geometry: &G, out: &mut Vec<Geometry>) -> Result<()> {
    if geometry.is_empty()? {
        return Ok(());
    }
    match geometry.geometry_type()? {
        GeometryTypes::Polygon => out.push(Geom::clone(geometry)?),
        GeometryTypes::MultiPolygon | GeometryTypes::GeometryCollection => {
            for i in 0..geometry.get_num_geometries()? {
                polygons(&geometry.get_geometry_n(i)?, out)?;
            }
        }
        _ => {} // Zero-area clipping remnants cannot occlude polygonal paint.
    }
    Ok(())
}
const LOCALIZE_ABOVE_COORDINATES: usize = 256;
const BOUNDS_GUARD_PT: f64 = 0.01;

struct Part {
    owner: usize,
    geometry: Geometry,
    coordinates: usize,
}
struct Index<'a> {
    parts: &'a [Part],
    prepared: Vec<PreparedGeometry<'a>>,
    tree: STRtree<usize>,
}
impl<'a> Index<'a> {
    fn new(parts: &'a [Part]) -> Result<Self> {
        let mut tree = STRtree::with_capacity(10)?;
        for (i, p) in parts.iter().enumerate() {
            tree.insert(&p.geometry, i);
        }
        tree.build()?;
        Ok(Self {
            parts,
            prepared: parts
                .iter()
                .map(|p| PreparedGeometry::new(&p.geometry))
                .collect::<std::result::Result<_, _>>()?,
            tree,
        })
    }
    /// None means a single later component completely covers hidden_target.
    /// Query target may be a wider pen envelope; that original-coverage shortcut
    /// must still erase the entire stroke, including permitted width excess.
    fn occluders(
        &self,
        target: &Geometry,
        hidden_target: &Geometry,
        owner: usize,
        stats: &mut CompileStats,
    ) -> Result<Option<Geometry>> {
        let start = Instant::now();
        let coordinates = target.get_num_coordinates()?;
        let prepared = PreparedGeometry::new(target)?;
        let mut candidates = Vec::new();
        self.tree.query(target, |i| {
            if self.parts[*i].owner > owner {
                candidates.push(*i)
            }
        });
        candidates.sort_unstable();
        stats.visibility_candidates += candidates.len();
        let mut later = Vec::new();
        let mut hidden = false;
        for i in candidates {
            if self.prepared[i].covers(hidden_target)? {
                hidden = true;
                break;
            }
            let intersects = if self.parts[i].coordinates > coordinates {
                self.prepared[i].intersects(target)?
            } else {
                prepared.intersects(&self.parts[i].geometry)?
            };
            if intersects {
                stats.visibility_intersections += 1;
                later.push(i);
            }
        }
        stats.visibility_predicate_seconds += start.elapsed().as_secs_f64();
        if hidden {
            return Ok(None);
        }
        let start = Instant::now();
        let mask = if later.is_empty() {
            g::empty()?
        } else {
            let bounds = target.get_extent()?;
            let clip = Geometry::create_rectangle(
                bounds[0] - BOUNDS_GUARD_PT,
                bounds[1] - BOUNDS_GUARD_PT,
                bounds[2] + BOUNDS_GUARD_PT,
                bounds[3] + BOUNDS_GUARD_PT,
            )?;
            let localized = Instant::now();
            let later = later
                .into_iter()
                .map(|i| -> Result<Geometry> {
                    stats.visibility_full_union_coordinates += self.parts[i].coordinates;
                    let geometry = if self.parts[i].coordinates > LOCALIZE_ABOVE_COORDINATES {
                        stats.visibility_localized_overlays += 1;
                        self.parts[i].geometry.intersection(&clip)?
                    } else {
                        Geom::clone(&self.parts[i].geometry)?
                    };
                    stats.visibility_union_coordinates += geometry.get_num_coordinates()?;
                    Ok(geometry)
                })
                .collect::<Result<Vec<_>>>()?;
            stats.visibility_localize_seconds += localized.elapsed().as_secs_f64();
            let union_start = Instant::now();
            let mask = g::union(later)?;
            stats.visibility_union_seconds += union_start.elapsed().as_secs_f64();
            mask
        };
        stats.visibility_overlay_seconds += start.elapsed().as_secs_f64();
        Ok(Some(mask))
    }

    fn subtract(
        &self,
        target: &Geometry,
        owner: usize,
        stats: &mut CompileStats,
    ) -> Result<Geometry> {
        let mut targets = Vec::new();
        polygons(target, &mut targets)?;
        let mut result = Vec::new();
        for target in targets {
            if let Some(mask) = self.occluders(&target, &target, owner, stats)? {
                let start = Instant::now();
                if mask.is_empty()? {
                    result.push(target);
                } else {
                    polygons(&target.difference(&mask)?, &mut result)?;
                }
                let elapsed = start.elapsed().as_secs_f64();
                stats.visibility_overlay_seconds += elapsed;
                stats.visibility_difference_seconds += elapsed;
            }
        }
        // Disjoint components stay disjoint under subtraction; no extra union.
        Ok(match result.len() {
            0 => g::empty()?,
            1 => result.pop().unwrap(),
            _ => Geometry::create_multipolygon(result)?,
        })
    }

    fn subtract_pair(
        &self,
        coverage: &Geometry,
        envelope: &Geometry,
        owner: usize,
        stats: &mut CompileStats,
    ) -> Result<(Geometry, Geometry)> {
        stats.visibility_shared_queries += 1;
        let Some(mask) = self.occluders(envelope, coverage, owner, stats)? else {
            return Ok((g::empty()?, g::empty()?));
        };
        let start = Instant::now();
        let result = if mask.is_empty()? {
            (Geom::clone(coverage)?, Geom::clone(envelope)?)
        } else {
            (coverage.difference(&mask)?, envelope.difference(&mask)?)
        };
        let elapsed = start.elapsed().as_secs_f64();
        stats.visibility_overlay_seconds += elapsed;
        stats.visibility_difference_seconds += elapsed;
        Ok(result)
    }
}
fn resolve_paint(
    index: &Index<'_>,
    prepared: &[PreparedGeometry<'_>],
    paints: &[Paint],
    i: usize,
    stats: &mut CompileStats,
) -> Result<(Geometry, Geometry)> {
    let p = &paints[i];
    // White still participates in the complete index as an occluder. Only its
    // own surviving area is unused by the plot: white can never request ink.
    if !p.visibility_resolved {
        return Ok((g::empty()?, g::empty()?));
    }
    if !p.coverage.is_empty()?
        && !p.coverage.equals_exact(&p.envelope, 0.)?
        && p.coverage.geometry_type()? == GeometryTypes::Polygon
        && p.envelope.geometry_type()? == GeometryTypes::Polygon
    {
        let (visible, allowed) = index.subtract_pair(&p.coverage, &p.envelope, i, stats)?;
        g::validate(&visible, "visible coverage")?;
        return Ok((visible, allowed));
    }
    let visible = index.subtract(&p.coverage, i, stats)?;
    let allowed = if p.coverage.equals_exact(&p.envelope, 0.)? {
        Geom::clone(&visible)?
    } else {
        let mut hidden = false;
        if visible.is_empty()? && !p.coverage.is_empty()? {
            let mut owners = Vec::new();
            index.tree.query(&p.coverage, |j| {
                let owner = index.parts[*j].owner;
                if owner > i {
                    owners.push(owner)
                }
            });
            owners.sort_unstable();
            owners.dedup();
            for j in owners {
                if prepared[j].covers(&p.coverage)? {
                    hidden = true;
                    break;
                }
            }
        }
        if hidden {
            g::empty()?
        } else {
            index.subtract(&p.envelope, i, stats)?
        }
    };
    g::validate(&visible, "visible coverage")?;
    Ok((visible, allowed))
}

pub fn resolve(
    paints: &mut [Paint],
    stats: &mut CompileStats,
    include_white_visibility: bool,
) -> Result<()> {
    let available = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    let limit = std::env::var("PLOTLINE_VISIBILITY_WORKERS")
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(16)
        .clamp(1, 16);
    let workers = if paints.len() < 2048 {
        1
    } else {
        available.min(limit)
    };
    resolve_with_workers(paints, stats, workers, include_white_visibility)
}

fn resolve_with_workers(
    paints: &mut [Paint],
    stats: &mut CompileStats,
    workers: usize,
    include_white_visibility: bool,
) -> Result<()> {
    for paint in paints.iter_mut() {
        paint.visibility_resolved = include_white_visibility || !is_white(paint.color);
    }
    stats.visibility_white_areas_deferred =
        paints.iter().filter(|p| !p.visibility_resolved).count();
    let mut progress = crate::progress::Progress::new(
        "indexing",
        "Indexing paint components",
        paints.len(),
        "paints",
    );
    let mut parts = Vec::new();
    for (owner, p) in paints.iter().enumerate() {
        progress.update(owner);
        if p.is_raster {
            continue;
        }
        let mut shapes = Vec::new();
        polygons(&p.coverage, &mut shapes)?;
        for geometry in shapes {
            let coordinates = geometry.get_num_coordinates()?;
            parts.push(Part {
                owner,
                geometry,
                coordinates,
            });
        }
    }
    stats.visibility_components = parts.len();
    // GEOS wrappers support Send/Sync. Each worker still owns its spatial and
    // prepared-predicate indexes, including their lazy caches. Compute input
    // envelopes on this thread before sharing immutable geometry references.
    for part in &parts {
        part.geometry.get_extent()?;
    }
    for p in paints.iter() {
        if !p.coverage.is_empty()? {
            p.coverage.get_extent()?;
        }
        if !p.envelope.is_empty()? {
            p.envelope.get_extent()?;
        }
    }
    progress.finish();
    let mut progress = crate::progress::Progress::new(
        "visibility",
        "Resolving paint visibility",
        paints.len(),
        "paints",
    );
    let mut results: Vec<_> = std::iter::repeat_with(|| None).take(paints.len()).collect();
    stats.visibility_workers = workers;
    if workers == 1 {
        let index = Index::new(&parts)?;
        let prepared = paints
            .iter()
            .map(|p| PreparedGeometry::new(&p.coverage))
            .collect::<std::result::Result<Vec<_>, _>>()?;
        for (i, result) in results.iter_mut().enumerate() {
            progress.update(i);
            *result = Some(resolve_paint(&index, &prepared, paints, i, stats)?);
        }
    } else {
        let next = AtomicUsize::new(0);
        let cancelled = AtomicBool::new(false);
        std::thread::scope(|scope| -> Result<()> {
            let (sender, receiver) = mpsc::sync_channel(2 * workers);
            let shared_paints = &*paints;
            let shared_parts = &parts;
            let mut handles = Vec::new();
            for _ in 0..workers {
                let sender = sender.clone();
                let next = &next;
                let cancelled = &cancelled;
                handles.push(scope.spawn(move || -> Result<CompileStats> {
                    let outcome = (|| -> Result<CompileStats> {
                        let index = Index::new(shared_parts)?;
                        let prepared = shared_paints
                            .iter()
                            .map(|p| PreparedGeometry::new(&p.coverage))
                            .collect::<std::result::Result<Vec<_>, _>>()?;
                        let mut local = CompileStats::default();
                        while !cancelled.load(Ordering::Relaxed) {
                            let start = next.fetch_add(64, Ordering::Relaxed);
                            if start >= shared_paints.len() {
                                break;
                            }
                            let end = (start + 64).min(shared_paints.len());
                            let batch = (start..end)
                                .map(|i| {
                                    resolve_paint(&index, &prepared, shared_paints, i, &mut local)
                                })
                                .collect::<Result<Vec<_>>>()?;
                            if sender.send((start, batch)).is_err() {
                                break;
                            }
                        }
                        Ok(local)
                    })();
                    if outcome.is_err() {
                        cancelled.store(true, Ordering::Relaxed);
                    }
                    outcome
                }));
            }
            drop(sender);
            let mut completed = 0;
            for (start, batch) in receiver {
                completed += batch.len();
                for (i, result) in batch.into_iter().enumerate() {
                    results[start + i] = Some(result);
                }
                progress.update(completed);
            }
            for handle in handles {
                let local = handle
                    .join()
                    .map_err(|_| anyhow::anyhow!("Visibility worker panicked"))??;
                stats.add_visibility(&local);
            }
            Ok(())
        })?;
    }
    progress.finish();
    for (p, result) in paints.iter_mut().zip(results) {
        let (visible, allowed) =
            result.ok_or_else(|| anyhow::anyhow!("Incomplete visibility result"))?;
        p.visible = visible;
        p.allowed = allowed;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn paint(id: usize, coverage: Geometry, envelope: Geometry) -> Result<Paint> {
        Ok(Paint {
            id,
            source: format!("fixture/{id}"),
            color: if id.is_multiple_of(2) {
                [0.; 3]
            } else {
                [1.; 3]
            },
            pen: None,
            pen_width: 1.,
            visible: g::empty()?,
            visibility_resolved: true,
            allowed: g::empty()?,
            coverage,
            envelope,
            stroke_width: None,
            stroke_style: None,
            width_layout: None,
            width_status: "not-applicable",
            source_lines: vec![],
            is_stroke: false,
            is_text: false,
            is_raster: false,
        })
    }
    fn check_reference(paints: &[Paint]) -> Result<()> {
        // Deliberately unindexed oracle: all later paints, in original order.
        for (i, p) in paints.iter().enumerate() {
            let later = g::union(
                paints[i + 1..]
                    .iter()
                    .map(|q| Ok(Geom::clone(&q.coverage)?))
                    .collect::<Result<_>>()?,
            )?;
            let visible = p.coverage.difference(&later)?;
            let hidden = paints[i + 1..]
                .iter()
                .try_fold(false, |yes, q| -> Result<bool> {
                    Ok(yes || q.coverage.covers(&p.coverage)?)
                })?;
            let allowed = if hidden {
                g::empty()?
            } else {
                p.envelope.difference(&later)?
            };
            assert!(
                visible.sym_difference(&p.visible)?.area()? < 1e-7,
                "visible paint {}",
                p.id
            );
            assert!(
                allowed.sym_difference(&p.allowed)?.area()? < 1e-7,
                "allowed paint {}",
                p.id
            );
            assert!(p.visible.is_valid()? && p.allowed.is_valid()?);
        }
        Ok(())
    }
    #[test]
    fn parallel_visibility_matches_serial_in_original_paint_order() -> Result<()> {
        fn fixture() -> Result<Vec<Paint>> {
            (0..150)
                .map(|i| {
                    let x = (i * 13 % 80) as f64;
                    let y = (i * 29 % 80) as f64;
                    let coverage = Geometry::create_rectangle(x, y, x + 12., y + 16.)?;
                    paint(i, Geom::clone(&coverage)?, g::round_buffer(&coverage, 0.4)?)
                })
                .collect()
        }
        let mut serial = fixture()?;
        let mut a = CompileStats::default();
        resolve_with_workers(&mut serial, &mut a, 1, true)?;
        for workers in [4, 8, 16] {
            let mut parallel = fixture()?;
            let mut b = CompileStats::default();
            resolve_with_workers(&mut parallel, &mut b, workers, true)?;
            assert_eq!(a.visibility_candidates, b.visibility_candidates);
            assert_eq!(a.visibility_shared_queries, b.visibility_shared_queries);
            assert!(b.visibility_shared_queries > 0);
            for (x, y) in serial.iter().zip(&parallel) {
                assert!(x.visible.equals_exact(&y.visible, 0.)?);
                assert!(x.allowed.equals_exact(&y.allowed, 0.)?);
            }
            check_reference(&parallel)?;
            // Preview may omit white self-visibility, never white occlusion.
            resolve_with_workers(&mut parallel, &mut b, workers, false)?;
            for (x, y) in serial
                .iter()
                .zip(&parallel)
                .filter(|(x, _)| !is_white(x.color))
            {
                assert!(x.visible.equals_exact(&y.visible, 0.)?);
                assert!(x.allowed.equals_exact(&y.allowed, 0.)?);
            }
        }
        Ok(())
    }

    #[test]
    fn parallel_visibility_propagates_geometry_errors() -> Result<()> {
        let bad = Geometry::new_from_wkt("POLYGON ((0 0,2 2,0 2,2 0,0 0))")?;
        let cut = Geometry::create_rectangle(1., -1., 3., 3.)?;
        let mut paints = vec![
            paint(0, Geom::clone(&bad)?, bad)?,
            paint(1, Geom::clone(&cut)?, cut)?,
        ];
        assert!(resolve_with_workers(&mut paints, &mut CompileStats::default(), 16, true).is_err());
        Ok(())
    }

    #[test]
    fn scattered_parts_prune_unrelated_islands() -> Result<()> {
        let mut paints = vec![];
        let mut islands = vec![];
        for i in 0..120 {
            let x = i as f64 * 10.;
            let coverage = Geometry::create_rectangle(x + 3., 0., x + 4., 1.)?;
            paints.push(paint(i, Geom::clone(&coverage)?, coverage)?);
            islands.push(Geometry::create_rectangle(x, 0., x + 1., 1.)?);
        }
        let coverage = Geometry::create_multipolygon(islands)?;
        paints.push(paint(120, Geom::clone(&coverage)?, coverage)?);
        let mut stats = CompileStats::default();
        resolve(&mut paints, &mut stats, true)?;
        assert_eq!(
            stats.visibility_candidates, 0,
            "Far-away islands must not enter local overlays"
        );
        check_reference(&paints)
    }
    #[test]
    fn holes_multipart_order_and_width_envelopes_match_global_oracle() -> Result<()> {
        let shapes = [
            "MULTIPOLYGON (((0 0,10 0,10 10,0 10,0 0)),((20 0,30 0,30 10,20 10,20 0)))",
            "POLYGON ((-2 -2,32 -2,32 12,-2 12,-2 -2),(2 2,2 8,8 8,8 2,2 2),(22 2,22 8,28 8,28 2,22 2))",
            "POLYGON ((4 -5,6 -5,6 15,4 15,4 -5))",
            "MULTIPOLYGON (((20 0,25 0,25 10,20 10,20 0)),((26 0,30 0,30 10,26 10,26 0)))",
            "POLYGON ((24 -1,27 -1,27 11,24 11,24 -1))",
        ];
        let mut paints = vec![];
        for (i, wkt) in shapes.iter().enumerate() {
            let coverage = Geometry::new_from_wkt(wkt)?;
            paints.push(paint(
                i,
                Geom::clone(&coverage)?,
                g::round_buffer(&coverage, 0.4)?,
            )?);
        }
        resolve(&mut paints, &mut CompileStats::default(), true)?;
        check_reference(&paints)
    }
    #[test]
    fn single_paint_cover_keeps_conservative_width_rule_for_all_parts() -> Result<()> {
        let coverage = Geometry::new_from_wkt(
            "MULTIPOLYGON (((0 0,1 0,1 1,0 1,0 0)),((5 0,6 0,6 1,5 1,5 0)))",
        )?;
        let mut paints = vec![
            paint(0, Geom::clone(&coverage)?, g::round_buffer(&coverage, 0.4)?)?,
            paint(1, Geom::clone(&coverage)?, coverage)?,
        ];
        resolve(&mut paints, &mut CompileStats::default(), true)?;
        assert!(paints[0].allowed.is_empty()?);
        check_reference(&paints)
    }
    #[test]
    fn complex_occluder_localization_preserves_holes_and_small_strokes() -> Result<()> {
        let outer = Geometry::new_from_wkt("POINT (0 0)")?.buffer(30., 128)?;
        // Many vertices at both the outer boundary and a hole; clip through both.
        let inner = Geometry::new_from_wkt("POINT (0 0)")?.buffer(28., 128)?;
        let ring = outer.difference(&inner)?;
        assert!(ring.get_num_coordinates()? > 256);
        let mut paints = vec![];
        for (i, x) in [27., 28., 29., 30.].iter().enumerate() {
            let coverage = Geometry::create_rectangle(*x, -1., x + 0.6, 1.)?;
            paints.push(paint(
                i,
                Geom::clone(&coverage)?,
                g::round_buffer(&coverage, 0.4)?,
            )?);
        }
        paints.push(paint(4, Geom::clone(&ring)?, ring)?);
        let mut stats = CompileStats::default();
        resolve(&mut paints, &mut stats, true)?;
        assert!(stats.visibility_localized_overlays > 0);
        assert!(stats.visibility_union_coordinates * 10 < stats.visibility_full_union_coordinates);
        check_reference(&paints)
    }
}
