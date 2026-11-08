//! Bounded, ordered work on disjoint inputs. Geometry stays owned by one task;
//! the coordinator alone publishes progress and restores input order.
use anyhow::{Result, anyhow};
use std::sync::{
    Mutex,
    atomic::{AtomicBool, Ordering},
    mpsc,
};

/// Count paths inside compound paints, with extra weight for long polylines.
/// Eight vertices approximate one modest path; this selects concurrency only,
/// never geometry precision or policy. Empty paints still cost one work item.
pub(crate) fn path_work(lengths: impl IntoIterator<Item = usize>) -> usize {
    lengths
        .into_iter()
        .map(|n| n.div_ceil(8).max(1))
        .sum::<usize>()
        .max(1)
}

pub(crate) fn workers(len: usize) -> usize {
    if len < 2048 {
        return 1;
    }
    let limit = std::env::var("PLOTLINE_GEOMETRY_WORKERS")
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(16)
        .clamp(1, 16);
    std::thread::available_parallelism()
        .map(|n| n.get().min(limit))
        .unwrap_or(1)
}

pub(crate) fn ordered_mut<T: Send, R: Send>(
    items: &mut [T],
    workers: usize,
    progress: &mut crate::progress::Progress,
    operation: impl Fn(usize, &mut T) -> Result<R> + Sync,
) -> Result<Vec<R>> {
    if workers <= 1 {
        return items
            .iter_mut()
            .enumerate()
            .map(|(i, item)| {
                progress.update(i);
                operation(i, item)
            })
            .collect();
    }
    const BATCH: usize = 32;
    let mut results: Vec<_> = std::iter::repeat_with(|| None).take(items.len()).collect();
    let chunks = Mutex::new(items.chunks_mut(BATCH).enumerate());
    let cancelled = AtomicBool::new(false);
    std::thread::scope(|scope| -> Result<()> {
        let (sender, receiver) = mpsc::sync_channel(2 * workers);
        let mut handles = Vec::new();
        for _ in 0..workers {
            let sender = sender.clone();
            let (chunks, cancelled, operation) = (&chunks, &cancelled, &operation);
            handles.push(scope.spawn(move || -> Result<()> {
                let outcome = (|| {
                    while !cancelled.load(Ordering::Relaxed) {
                        let chunk = chunks
                            .lock()
                            .map_err(|_| anyhow!("Work queue poisoned"))?
                            .next();
                        let Some((batch, items)) = chunk else { break };
                        let start = batch * BATCH;
                        let output = items
                            .iter_mut()
                            .enumerate()
                            .map(|(i, item)| operation(start + i, item))
                            .collect::<Result<Vec<_>>>()?;
                        if sender.send((start, output)).is_err() {
                            break;
                        }
                    }
                    Ok(())
                })();
                if outcome.is_err() {
                    cancelled.store(true, Ordering::Relaxed);
                }
                outcome
            }));
        }
        drop(sender);
        let mut completed = 0;
        for (start, output) in receiver {
            completed += output.len();
            for (i, value) in output.into_iter().enumerate() {
                results[start + i] = Some(value);
            }
            progress.update(completed);
        }
        // Join every worker even on failure, before returning any result.
        let mut outcome = Ok(());
        for handle in handles {
            let result = handle
                .join()
                .map_err(|_| anyhow!("Geometry worker panicked"))
                .and_then(|r| r);
            outcome = outcome.and(result);
        }
        outcome
    })?;
    results
        .into_iter()
        .map(|r| r.ok_or_else(|| anyhow!("Incomplete geometry result")))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compound_paths_count_toward_the_same_parallel_work_as_separate_paints() {
        // PDF exporters may batch many subpaths into one paint. That packaging
        // must not make thousands of identical physical paths look like a toy job.
        let separate: usize = (0..4096).map(|_| path_work([2])).sum();
        let compound: usize = (0..256).map(|_| path_work([2; 16])).sum();
        assert_eq!(compound, separate);
        assert_eq!(workers(compound), workers(separate));
        assert!(path_work([32768]) >= separate);
        assert_eq!(workers(path_work([2])), 1);
    }

    #[test]
    fn work_is_ordered_and_mutations_are_disjoint() -> Result<()> {
        let mut items: Vec<_> = (0..4097).collect();
        let mut progress = crate::progress::Progress::new("test", "Test", items.len(), "items");
        let output = ordered_mut(&mut items, 16, &mut progress, |i, item| {
            assert_eq!(i, *item);
            *item *= 2;
            Ok(i * 3)
        })?;
        assert_eq!(items, (0..4097).map(|i| i * 2).collect::<Vec<_>>());
        assert_eq!(output, (0..4097).map(|i| i * 3).collect::<Vec<_>>());
        Ok(())
    }

    #[test]
    fn failed_or_panicking_worker_cannot_return_partial_results() {
        for panic in [false, true] {
            let mut items = vec![0; 4097];
            let mut progress = crate::progress::Progress::new("test", "Test", items.len(), "items");
            let result = ordered_mut(&mut items, 16, &mut progress, |i, _| {
                if i == 65 {
                    assert!(!panic, "test worker panic");
                    anyhow::bail!("test geometry failure");
                }
                Ok(i)
            });
            assert!(result.is_err());
        }
    }
}
