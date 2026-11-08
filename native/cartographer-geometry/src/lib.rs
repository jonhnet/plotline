//! Batched GEOS visibility overlays. No geometry pointers cross the Python ABI.
use geos::{Geom, Geometry};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rayon::prelude::*;

fn geometry_error(e: geos::Error) -> PyErr {
    PyRuntimeError::new_err(e.to_string())
}

#[pyclass]
struct Occluders {
    // Each worker owns a complete set: even lazily cached GEOS envelopes are
    // never accessed concurrently on a shared geometry. geos uses a TLS context.
    geometry: Vec<Vec<Geometry>>,
    pool: rayon::ThreadPool,
}

#[pymethods]
impl Occluders {
    #[new]
    fn new(py: Python<'_>, wkb: Vec<Vec<u8>>, workers: usize) -> PyResult<Self> {
        if !(1..=16).contains(&workers) {
            return Err(PyValueError::new_err(
                "geometry workers must be between 1 and 16",
            ));
        }
        py.detach(|| {
            let pool = rayon::ThreadPoolBuilder::new()
                .num_threads(workers)
                .build()
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
            let geometry = pool
                .install(|| {
                    (0..workers)
                        .into_par_iter()
                        .map(|_| {
                            wkb.iter()
                                .map(|raw| Geometry::new_from_wkb(raw))
                                .collect::<geos::GResult<Vec<_>>>()
                        })
                        .collect::<geos::GResult<Vec<_>>>()
                })
                .map_err(geometry_error)?;
            Ok(Self { geometry, pool })
        })
    }

    fn resolve<'py>(
        &self,
        py: Python<'py>,
        faces: Vec<Vec<u8>>,
        offsets: Vec<usize>,
        near: Vec<usize>,
    ) -> PyResult<(Vec<Bound<'py, PyBytes>>, usize)> {
        if offsets.len() != faces.len() + 1
            || offsets.first() != Some(&0)
            || offsets.last() != Some(&near.len())
            || offsets.windows(2).any(|v| v[0] > v[1])
            || near.iter().any(|&i| i >= self.geometry[0].len())
        {
            return Err(PyValueError::new_err("invalid occluder offsets or indices"));
        }
        let result = py
            .detach(|| {
                self.pool.install(|| {
                    faces
                        .par_iter()
                        .enumerate()
                        .map(|(i, raw)| {
                            let projected = &self.geometry[rayon::current_thread_index().unwrap()];
                            let face = Geometry::new_from_wkb(raw)?;
                            let neighbors = &near[offsets[i]..offsets[i + 1]];
                            for &idx in neighbors {
                                if projected[idx].covers(&face)? {
                                    return Ok((Geometry::create_empty_polygon()?.to_wkb()?, true));
                                }
                            }
                            // Boundary-only contacts cannot occlude any face area.
                            // Besides shrinking the union, removing these avoids a
                            // GEOS contact-ring degeneracy that can expose a hidden face.
                            let interior = neighbors
                                .iter()
                                .filter_map(|&idx| match projected[idx].touches(&face) {
                                    Ok(true) => None,
                                    Ok(false) => Some(Ok(idx)),
                                    Err(error) => Some(Err(error)),
                                })
                                .collect::<geos::GResult<Vec<_>>>()?;
                            let neighbors = interior.as_slice();
                            let visible = match neighbors {
                                [] => face,
                                [idx] => face.difference(&projected[*idx])?,
                                _ => {
                                    let copies = neighbors
                                        .iter()
                                        .map(|&idx| Geom::clone(&projected[idx]))
                                        .collect::<geos::GResult<Vec<_>>>()?;
                                    let union = Geometry::create_geometry_collection(copies)?
                                        .unary_union()?;
                                    face.difference(&union)?
                                }
                            };
                            Ok((visible.to_wkb()?, false))
                        })
                        .collect::<geos::GResult<Vec<_>>>()
                })
            })
            .map_err(geometry_error)?;
        let hidden = result.iter().filter(|(_, hidden)| *hidden).count();
        Ok((
            result
                .iter()
                .map(|(raw, _)| PyBytes::new(py, raw))
                .collect(),
            hidden,
        ))
    }
}

#[pyfunction]
fn geos_version() -> PyResult<String> {
    geos::version().map_err(geometry_error)
}

#[pymodule]
fn _cartographer_geometry(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("visibility_revision", 2)?;
    m.add_class::<Occluders>()?;
    m.add_function(wrap_pyfunction!(geos_version, m)?)?;
    Ok(())
}
