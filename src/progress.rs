//! Best-effort, atomic phase snapshots for the PDF worker. No output unless opted in.
use serde_json::{Value, json};
use std::{
    fs,
    path::PathBuf,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub struct Progress {
    path: Option<PathBuf>,
    context: Value,
    phase: &'static str,
    label: &'static str,
    total: usize,
    unit: &'static str,
    started: Instant,
    updated: Instant,
}
impl Progress {
    pub fn new(phase: &'static str, label: &'static str, total: usize, unit: &'static str) -> Self {
        let now = Instant::now();
        let mut progress = Self {
            path: std::env::var_os("PLOTLINE_PROGRESS_FILE").map(PathBuf::from),
            context: std::env::var("PLOTLINE_PROGRESS_CONTEXT")
                .ok()
                .and_then(|s| serde_json::from_str(&s).ok())
                .unwrap_or(json!({})),
            phase,
            label,
            total,
            unit,
            started: now,
            updated: now,
        };
        progress.write(0);
        progress
    }
    pub fn update(&mut self, completed: usize) {
        if self.path.is_some() && self.updated.elapsed() >= Duration::from_millis(500) {
            self.write(completed);
        }
    }
    pub fn finish(&mut self) {
        self.write(self.total);
    }
    fn write(&mut self, completed: usize) {
        let Some(path) = &self.path else {
            return;
        };
        let mut value = self.context.clone();
        if !value.is_object() {
            value = json!({});
        }
        for (key, item) in json!({"version":1, "phase":self.phase, "label":self.label,
            "completed":completed, "total":self.total, "unit":self.unit,
            "phase_elapsed_seconds":self.started.elapsed().as_secs_f64(),
            "updated_at":SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64()
        }).as_object().unwrap() { value[key] = item.clone(); }
        // A lost progress update must never fail an otherwise valid conversion.
        let temporary = path.with_extension("progress.tmp");
        if let Ok(bytes) = serde_json::to_vec(&value)
            && fs::write(&temporary, bytes).is_ok()
        {
            let _ = fs::rename(temporary, path);
        }
        self.updated = Instant::now();
    }
}
