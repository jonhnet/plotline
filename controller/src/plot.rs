//! Plotline Studios owns the plan, measurements, execution, controls, and recovery.
//! Stain only transports byte packets over one connection for each plot.
use crate::{
    layout::{Observation, Setup},
    prepare,
    serial::{Bridge, Port, Transport},
    store::Store,
};
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
    sync::{Arc, Condvar, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Execution {
    pub attempt: String,
    pub state: String,
    pub submitted_bytes: usize,
    pub submitted_commands: usize,
    pub total_bytes: usize,
    pub commands: usize,
    pub error: Option<String>,
    #[serde(default)]
    pub cancelled: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completion: Option<crate::completion::Completion>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timing: Option<crate::timing::Remaining>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Ticket {
    pub id: String,
    pub job_id: String,
    pub name: String,
    pub created_at: u64,
    pub setup: Setup,
    pub observation: Option<Observation>,
    #[serde(default)]
    pub request_sha256: String,
    pub inks: Vec<Value>,
    pub phase: String,
    pub error: Option<String>,
    pub bytes: usize,
    pub commands: usize,
    pub sha256: String,
    #[serde(default)]
    pub execution: Option<Execution>,
    #[serde(default)]
    pub carousel: Option<crate::carousel::Carousel>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub estimate: Option<crate::timing::Estimate>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recovery: Option<crate::recovery::Origin>,
    #[serde(default)]
    pub pause_before_pen_change: bool,
}
#[derive(Default)]
struct Control {
    attempt: Option<String>,
    pause: bool,
    cancel: bool,
}
pub struct Plotter {
    root: PathBuf,
    transport: Arc<dyn Transport>,
    carousel: Arc<crate::carousel::Inventory>,
    enabled: bool,
    gate: Mutex<()>,
    control: Mutex<Control>,
    wake: Condvar,
}
fn durable(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = File::create(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}
fn atomic(path: &Path, value: &impl Serialize) -> Result<()> {
    let temp = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    durable(&temp, &serde_json::to_vec_pretty(value)?)?;
    fs::rename(&temp, path)?;
    File::open(path.parent().unwrap())?.sync_all()?;
    Ok(())
}
fn query(port: &mut dyn Port, command: &[u8]) -> Result<String> {
    ensure!(
        port.read(0)?.iter().all(|c| b"\r\n".contains(c)),
        "Unexpected serial input before query"
    );
    port.write(command).map_err(|e| {
        anyhow::anyhow!(
            "Plotter query failed; check its power, Remote button, and serial connection: {e}"
        )
    })?;
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut bytes = Vec::new();
    while Instant::now() < deadline {
        bytes.extend(port.read(1000)?);
        ensure!(bytes.len() <= 1024, "Oversized plotter reply");
        if bytes.iter().any(|c| b"\r\n".contains(c)) {
            return Ok(String::from_utf8(bytes)?
                .trim_matches(['\r', '\n'])
                .to_owned());
        }
    }
    bail!("No plotter reply; check its power, Remote button, and serial connection")
}
fn measure(port: &mut dyn Port) -> Result<Observation> {
    let identity = query(port, b"OI;")?;
    ensure!(
        identity == "7585B",
        "Expected HP 7585B; received {identity:?}"
    );
    let first = query(port, b"OH;")?;
    let parsed: Vec<i32> = first
        .split(',')
        .map(str::parse)
        .collect::<std::result::Result<_, _>>()?;
    let limits: [i32; 4] = parsed
        .try_into()
        .map_err(|_| anyhow::anyhow!("Invalid plotter limits"))?;
    ensure!(
        limits.iter().all(|n| (-32767..=32767).contains(n))
            && limits[0] < limits[2]
            && limits[1] < limits[3],
        "Invalid plotter limits"
    );
    let date = std::process::Command::new("date")
        .args(["-u", "+%Y-%m-%dT%H:%M:%SZ"])
        .output()?;
    ensure!(date.status.success(), "Could not timestamp the measurement");
    Ok(Observation {
        identity,
        limits,
        units_per_mm: 40.,
        observed_at: String::from_utf8(date.stdout)?.trim().to_owned(),
    })
}
impl Plotter {
    pub fn open(
        root: PathBuf,
        config: PathBuf,
        helper: Option<PathBuf>,
        carousel: Arc<crate::carousel::Inventory>,
    ) -> Result<Arc<Self>> {
        let config = config.canonicalize()?;
        let value: Value = serde_json::from_slice(&fs::read(&config)?)?;
        carousel.migrate(&value["carousel"])?;
        fs::create_dir_all(&root)?;
        let root = root.canonicalize()?;
        let bridge = Bridge {
            config,
            helper: helper.map(|p| p.canonicalize()).transpose()?,
            control: root.clone(),
        };
        Self::new(
            root,
            Arc::new(bridge),
            carousel,
            value["plotting_enabled"] == true,
        )
    }
    fn new(
        root: PathBuf,
        transport: Arc<dyn Transport>,
        carousel: Arc<crate::carousel::Inventory>,
        enabled: bool,
    ) -> Result<Arc<Self>> {
        fs::create_dir_all(&root)?;
        let this = Arc::new(Self {
            root,
            transport,
            carousel,
            enabled,
            gate: Mutex::new(()),
            control: Mutex::new(Control::default()),
            wake: Condvar::new(),
        });
        if let Some(mut ticket) = this.active()? {
            if ticket
                .execution
                .as_ref()
                .is_some_and(|e| e.state == "finishing")
            {
                let e = ticket.execution.as_mut().unwrap();
                let message = if e.cancelled {
                    "Service restarted while waiting for cancellation to finish. Parking was not confirmed. Reset the plotter before another attempt; no bytes were replayed."
                } else {
                    "Service restarted while waiting for completion. All drawing data was sent, but completion was not confirmed. Reset the plotter before another attempt; no bytes were replayed."
                };
                e.state = "completion_unknown".into();
                e.error = Some(message.into());
                ticket.error = Some(message.into());
                this.save(&ticket)?;
            } else if ticket.phase == "uploading"
                || (ticket.phase == "starting"
                    && ticket.execution.as_ref().is_some_and(|e| {
                        ["connecting", "pausing", "cancelling"].contains(&e.state.as_str())
                    }))
            {
                ticket.phase = "failed".into();
                ticket.execution = None;
                ticket.error =
                    Some("Service restarted before feeding; nothing was replayed".into());
                this.save(&ticket)?;
            } else if ["starting", "accepted", "uncertain"].contains(&ticket.phase.as_str())
                && ticket.execution.as_ref().is_none_or(|e| {
                    [
                        "connecting",
                        "feeding",
                        "pausing",
                        "paused",
                        "cancelling",
                        "interrupted",
                        "uncertain",
                    ]
                    .contains(&e.state.as_str())
                })
            {
                ticket.phase = "uncertain".into();
                let message = "Service restarted or legacy execution needs reconciliation. Reset the plotter before another attempt; no bytes were replayed.";
                ticket.error = Some(message.into());
                let mut execution = ticket
                    .execution
                    .take()
                    .unwrap_or_else(|| this.execution(&ticket, "interrupted"));
                execution.state = "interrupted".into();
                execution.error = Some(message.into());
                ticket.execution = Some(execution);
                this.save(&ticket)?;
            }
        }
        Ok(this)
    }
    fn execution(&self, ticket: &Ticket, state: &str) -> Execution {
        Execution {
            attempt: ticket.id.clone(),
            state: state.into(),
            submitted_bytes: 0,
            submitted_commands: 0,
            total_bytes: ticket.bytes,
            commands: ticket.commands,
            error: None,
            cancelled: false,
            completion: None,
            timing: None,
        }
    }
    fn path(&self, id: &str) -> Result<PathBuf> {
        ensure!(
            Uuid::parse_str(id).is_ok_and(|v| v.to_string() == id),
            "Invalid plot ID"
        );
        Ok(self.root.join(id))
    }
    fn read(&self, id: &str) -> Result<Ticket> {
        Ok(serde_json::from_slice(&fs::read(
            self.path(id)?.join("record.json"),
        )?)?)
    }
    fn save(&self, ticket: &Ticket) -> Result<()> {
        atomic(&self.path(&ticket.id)?.join("record.json"), ticket)
    }
    fn active(&self) -> Result<Option<Ticket>> {
        let path = self.root.join("active.json");
        if !path.exists() {
            return Ok(None);
        }
        let id: String = serde_json::from_slice(&fs::read(path)?)?;
        Ok(Some(self.read(&id)?))
    }
    fn free(&self) -> Result<()> {
        ensure!(
            self.control.lock().unwrap().attempt.is_none(),
            "A plot is already starting or feeding"
        );
        if let Some(ticket) = self.active()? {
            ensure!(
                ticket
                    .execution
                    .as_ref()
                    .is_none_or(|e| ["finished", "reset_acknowledged"].contains(&e.state.as_str())),
                "Finish or resolve the current plot before starting another"
            );
            ensure!(
                !["starting", "uncertain"].contains(&ticket.phase.as_str()),
                "Resolve the current plot before starting another"
            );
        }
        Ok(())
    }
    pub fn status(&self) -> Result<Value> {
        // The worker owns the persistent connection. Browser polling reads local
        // execution state and never competes with that connection for serial I/O.
        let live = self.control.lock().unwrap().attempt.is_some();
        let result = if live {
            Ok(json!({"protocol":2,"serial_present":true,"busy":true}))
        } else {
            self.transport.status()
        };
        let _guard = self.gate.lock().unwrap();
        let ticket = self.active()?;
        let execution = ticket.as_ref().and_then(|t| t.execution.clone());
        let (connected, mut device, error) = match result {
            Ok(value) => (true, value, None),
            Err(error) => (false, json!({}), Some(error.to_string())),
        };
        device["plotting_enabled"] = json!(self.enabled);
        device["execution"] = json!(execution);
        Ok(
            json!({"configured":true,"connected":connected,"device":device,"submission":ticket,"error":error,"server_time_ms":crate::timing::now_ms()}),
        )
    }
    pub fn lookup(&self, id: &str) -> Result<Option<Ticket>> {
        let _guard = self.gate.lock().unwrap();
        if !self.path(id)?.exists() {
            return Ok(None);
        }
        Ok(Some(self.read(id)?))
    }
    pub fn check_paper(&self, store: &Store) -> Result<Observation> {
        // Serialize with submission, and refuse while any plot owns the port.
        // A check never creates a ticket, initializes the plotter, or moves a pen.
        let _guard = self.gate.try_lock().map_err(|_| {
            anyhow::anyhow!("Another plotter operation is in progress; try again shortly")
        })?;
        self.free()?;
        let mut port = self.transport.open()?;
        let observation = measure(port.as_mut())?;
        port.close()?;
        atomic(&store.root.join("device-observation.json"), &observation)?;
        Ok(observation)
    }
    pub fn progress(&self, id: &str) -> Result<crate::progress::Progress> {
        let ticket = self.lookup(id)?.context("Unknown plot attempt")?;
        if ticket.bytes == 0 {
            return Ok(crate::progress::describe("", &[]));
        }
        // Prepared commands and recorded inks are immutable. Read on demand,
        // independently of frequent execution-state writes and status polling.
        let commands = crate::storage::read_to_string(self.path(id)?.join("prepared.hpgl"))?;
        ensure!(commands.len() == ticket.bytes, "Prepared plot size changed");
        Ok(crate::progress::describe(&commands, &ticket.inks))
    }
    pub fn history(&self, job: &str) -> Result<Vec<Ticket>> {
        let mut tickets = Vec::new();
        for entry in fs::read_dir(&self.root)? {
            let entry = entry?;
            let id = entry.file_name().to_string_lossy().into_owned();
            if Uuid::parse_str(&id).is_ok() && entry.path().join("record.json").exists() {
                // Other documents can have receipts from the retired Pi spooler
                // (including textual completion notes). Identify the document
                // before asking today's execution model to decode its record.
                let record: Value =
                    serde_json::from_slice(&fs::read(entry.path().join("record.json"))?)?;
                if record["job_id"].as_str() == Some(job)
                    && record["bytes"].as_u64().is_some_and(|bytes| bytes > 0)
                {
                    tickets.push(serde_json::from_value::<Ticket>(record)?);
                }
            }
        }
        tickets.sort_by(|a, b| b.created_at.cmp(&a.created_at).then(b.id.cmp(&a.id)));
        Ok(tickets)
    }
    pub fn program(&self, id: &str) -> Result<String> {
        let ticket = self.read(id)?;
        let program = crate::storage::read_to_string(self.path(id)?.join("prepared.hpgl"))?;
        ensure!(
            program.len() == ticket.bytes
                && format!("{:x}", Sha256::digest(program.as_bytes())) == ticket.sha256,
            "Archived plot program failed its integrity check"
        );
        Ok(program)
    }
    fn recovery_origin(&self, id: &str) -> Result<(Ticket, usize)> {
        let mut ticket = self.read(id)?;
        let mut start = 0usize;
        let mut seen = std::collections::BTreeSet::new();
        while let Some(origin) = &ticket.recovery {
            ensure!(
                seen.insert(ticket.id.clone()) && seen.len() <= 64,
                "Invalid recovery ancestry"
            );
            let parent = self.read(&origin.attempt)?;
            ensure!(
                parent.job_id == ticket.job_id && parent.sha256 == origin.sha256,
                "Archived recovery ancestry changed"
            );
            start = start
                .checked_add(origin.start_stroke)
                .context("Invalid recovery stroke offset")?;
            ticket = parent;
        }
        Ok((ticket, start))
    }
    pub fn recovery_preview(&self, id: &str) -> Result<Value> {
        let (ticket, start_stroke) = self.recovery_origin(id)?;
        let program = self.program(&ticket.id)?;
        let strokes = crate::recovery::index(&program)?;
        ensure!(
            start_stroke < strokes.len(),
            "Recorded recovery stroke is outside the original drawing"
        );
        Ok(
            json!({"submission":ticket,"selected_attempt":id,"start_stroke":start_stroke,"progress":crate::progress::describe(&program,&ticket.inks),
            "strokes":strokes.iter().map(|s|[s.offset,s.pen as usize]).collect::<Vec<_>>()}),
        )
    }
    pub fn recover(
        self: &Arc<Self>,
        store: Store,
        original: String,
        request: crate::recovery::Request,
    ) -> Result<Ticket> {
        let _guard = self.gate.lock().unwrap();
        let path = self.path(&request.attempt)?;
        let request_sha256 = format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(&(&original, &request))?)
        );
        if path.exists() {
            let existing = self.read(&request.attempt)?;
            ensure!(
                existing.request_sha256 == request_sha256,
                "This recovery request ID belongs to different settings"
            );
            return Ok(existing);
        }
        self.free()?;
        ensure!(
            self.enabled,
            "Plotting is disabled in the connection configuration"
        );
        let selected = self.read(&original)?;
        ensure!(
            request.source_sha256 == selected.sha256,
            "The selected archived plot has changed"
        );
        // New UIs always address the full original. Translate requests from an
        // older UI's trimmed attempt too, then retain one stable reference.
        let (parent, base_stroke) = self.recovery_origin(&original)?;
        let start_stroke = base_stroke
            .checked_add(request.start_stroke)
            .context("Invalid recovery stroke offset")?;
        let program = self.program(&parent.id)?;
        let strokes = crate::recovery::index(&program)?;
        let stroke = strokes
            .get(start_stroke)
            .context("Choose an existing stroke to resume")?;
        let commands = crate::recovery::suffix(&program, stroke);
        // A repair deliberately reuses geometry with a replacement pen. Preserve
        // the recorded slots/settings; never resolve today's recipe or recenter.
        let carousel = self.carousel.get()?;
        for s in &strokes[start_stroke..] {
            ensure!(
                carousel.slots.get(&s.pen).is_some_and(|p| p.usable()),
                "Load a pen in recorded slot {} before resuming",
                s.pen
            );
        }
        let mut ticket = parent.clone();
        ticket.id = request.attempt.clone();
        ticket.created_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
        ticket.request_sha256 = request_sha256;
        ticket.phase = "starting".into();
        ticket.error = None;
        ticket.observation = None;
        ticket.estimate = None;
        ticket.bytes = commands.len();
        ticket.commands = commands.lines().count();
        ticket.sha256 = format!("{:x}", Sha256::digest(commands.as_bytes()));
        ticket.recovery = Some(crate::recovery::Origin {
            attempt: parent.id,
            sha256: parent.sha256,
            start_stroke,
            start_byte: stroke.offset,
        });
        ticket.pause_before_pen_change = request.pause_before_pen_change;
        ticket.execution = Some(self.execution(&ticket, "connecting"));
        fs::create_dir(&path)?;
        durable(&path.join("prepared.hpgl"), commands.as_bytes())?;
        self.save(&ticket)?;
        File::open(&path)?.sync_all()?;
        File::open(&self.root)?.sync_all()?;
        atomic(&self.root.join("active.json"), &ticket.id)?;
        *self.control.lock().unwrap() = Control {
            attempt: Some(ticket.id.clone()),
            pause: false,
            cancel: false,
        };
        let this = Arc::clone(self);
        let id = ticket.id.clone();
        std::thread::spawn(move || this.feed(id, store));
        Ok(ticket)
    }
    pub fn submit(
        self: &Arc<Self>,
        store: Store,
        id: String,
        attempt: String,
        mut setup: Setup,
    ) -> Result<Ticket> {
        let _guard = self.gate.lock().unwrap();
        let path = self.path(&attempt)?;
        let request_sha256 = format!("{:x}", Sha256::digest(serde_json::to_vec(&(&id, &setup))?));
        // A retry identifies the original click, even after completion/restart.
        // Do this before checking current inventory or another active job.
        if path.exists() {
            let ticket = self.read(&attempt)?;
            ensure!(
                ticket.request_sha256 == request_sha256,
                "This plot request ID belongs to different settings"
            );
            return Ok(ticket);
        }
        self.free()?;
        ensure!(
            self.enabled,
            "Plotting is disabled in the Plotline Studios connection configuration"
        );
        let job = store.get(&id)?;
        ensure!(
            store.is_current(&job)?,
            "PDF Studio prepared a newer version of this drawing. Choose Open latest version in HPGL Studio, or Open… and select the drawing again"
        );
        setup.upgrade(&job);
        setup.validate(&job)?;
        let carousel = self.carousel.get()?;
        let colors = carousel.resolve(&mut setup, &job)?;
        let inks = job.summary.pens.iter().filter(|p| setup.pens.contains(&p.id)).map(|pen| {
            let slot = setup.pen_map[&pen.id];
            json!({"source":pen.id,"requested_color":pen.color,"color":colors[&pen.id],"slot":slot,
                "settings":setup.ink_settings[&pen.id],"loaded":carousel.slots[&slot]})
        }).collect();
        let mut ticket = Ticket {
            id: attempt.clone(),
            job_id: id,
            name: job.name,
            created_at: SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
            setup,
            observation: None,
            request_sha256,
            inks,
            phase: "starting".into(),
            execution: None,
            carousel: Some(carousel),
            estimate: None,
            error: None,
            bytes: 0,
            commands: 0,
            sha256: String::new(),
            recovery: None,
            pause_before_pen_change: false,
        };
        ticket.execution = Some(self.execution(&ticket, "connecting"));
        fs::create_dir(&path)?;
        self.save(&ticket)?;
        File::open(&self.root)?.sync_all()?;
        atomic(&self.root.join("active.json"), &attempt)?;
        *self.control.lock().unwrap() = Control {
            attempt: Some(attempt.clone()),
            pause: false,
            cancel: false,
        };
        let this = Arc::clone(self);
        std::thread::spawn(move || this.feed(attempt, store));
        Ok(ticket)
    }
    fn prepare(
        &self,
        id: &str,
        store: &Store,
        observation: Observation,
    ) -> Result<(Vec<u8>, crate::timing::Forecast)> {
        let mut ticket = self.read(id)?;
        // Publish the actual limits even if the drawing subsequently fails to fit.
        atomic(&store.root.join("device-observation.json"), &observation)?;
        {
            let _guard = self.gate.lock().unwrap();
            let mut latest = self.read(id)?;
            latest.observation = Some(observation.clone());
            self.save(&latest)?;
        }
        if ticket.recovery.is_some() {
            let source = self.program(id)?;
            prepare::check_loaded_window(&source, observation.limits)?;
            let forecast = crate::timing::forecast(&source)?;
            durable(
                &self.path(id)?.join("timing-model.json"),
                &serde_json::to_vec_pretty(&forecast)?,
            )?;
            let _guard = self.gate.lock().unwrap();
            let mut latest = self.read(id)?;
            latest.estimate = Some(forecast.estimate.clone());
            self.save(&latest)?;
            return Ok((source.into_bytes(), forecast));
        }
        let job = store.get(&ticket.job_id)?;
        let carousel = ticket
            .carousel
            .as_ref()
            .context("Missing carousel snapshot")?;
        let colors = carousel.resolve(&mut ticket.setup, &job)?;
        let source =
            crate::storage::read_to_string(store.path(&ticket.job_id)?.join("source.hpgl"))?;
        let context = ticket.setup.fit_context(&job, Some(&observation));
        let plan = prepare::prepare_registered(
            &source,
            job.units_per_mm,
            ticket.setup.profile(Some(&observation))?,
            ticket.setup.rotation,
            ticket.setup.pens.clone(),
            ticket.setup.pen_map.clone(),
            ticket.setup.ink_settings.clone(),
            colors,
            job.registration().is_some(),
            job.calibration.is_some().then_some(5.),
        )
        .map_err(|error| crate::layout::explain_fit(error, &context))?;
        prepare::check_loaded_window(&plan.commands, observation.limits)
            .map_err(|error| crate::layout::explain_fit(error, &context))?;
        ensure!(
            self.carousel.get()?.slots == carousel.slots,
            "Carousel changed while starting. Check the preview and click Plot again"
        );
        let path = self.path(id)?;
        let forecast = crate::timing::forecast(&plan.commands)?;
        durable(&path.join("prepared.hpgl"), plan.commands.as_bytes())?;
        durable(
            &path.join("timing-model.json"),
            &serde_json::to_vec_pretty(&forecast)?,
        )?;
        durable(
            &path.join("plan.json"),
            &serde_json::to_vec_pretty(&plan.report)?,
        )?;
        File::open(&path)?.sync_all()?;
        {
            let _guard = self.gate.lock().unwrap();
            // Preserve concurrent pause/cancel requests while publishing totals.
            let mut latest = self.read(id)?;
            latest.bytes = plan.commands.len();
            latest.commands = plan.commands.lines().count();
            latest.sha256 = format!("{:x}", Sha256::digest(plan.commands.as_bytes()));
            latest.estimate = Some(forecast.estimate.clone());
            let e = latest
                .execution
                .as_mut()
                .context("Missing execution state")?;
            e.total_bytes = latest.bytes;
            e.commands = latest.commands;
            self.save(&latest)?;
        }
        Ok((plan.commands.into_bytes(), forecast))
    }
    fn update(
        &self,
        id: &str,
        state: &str,
        bytes: usize,
        commands: usize,
        forecast: &crate::timing::Forecast,
    ) -> Result<()> {
        let _guard = self.gate.lock().unwrap();
        let mut ticket = self.read(id)?;
        let execution = ticket
            .execution
            .as_mut()
            .context("Missing execution state")?;
        execution.state = state.into();
        execution.submitted_bytes = bytes;
        execution.submitted_commands = commands;
        execution.timing = Some(crate::timing::Remaining {
            seconds: forecast.remaining(bytes),
            updated_at_ms: crate::timing::now_ms(),
        });
        ticket.phase = "accepted".into();
        self.save(&ticket)
    }
    fn finish(&self, id: &str, completion: Option<crate::completion::Completion>) -> Result<()> {
        let _guard = self.gate.lock().unwrap();
        let mut ticket = self.read(id)?;
        let execution = ticket.execution.as_mut().context("Missing execution")?;
        execution.state = "finished".into();
        execution.completion = completion;
        execution.timing = Some(crate::timing::Remaining {
            seconds: 0.,
            updated_at_ms: crate::timing::now_ms(),
        });
        ticket.phase = "accepted".into();
        self.save(&ticket)
    }
    fn feed(&self, id: String, store: Store) {
        let mut attempted = false;
        let mut completion_pending = false;
        let mut cancelling = false;
        let result = (|| -> Result<()> {
            // The only port opened for this plot: query, prepare, then feed.
            let mut port = self.transport.open()?;
            let observation = measure(port.as_mut())?;
            let (source, forecast) = self.prepare(&id, &store, observation)?;
            let ticket = self.read(&id)?;
            port.start_trace(
                &self.path(&id)?.join("serial-timing.jsonl"),
                json!({
                    "attempt":id, "job_id":ticket.job_id, "prepared_sha256":ticket.sha256,
                    "prepared_bytes":source.len(), "units_per_mm":40,
                    "inks":ticket.inks,
                }),
            )?;
            let mut lines = source.split_inclusive(|b| *b == b'\n').peekable();
            let (mut sent, mut commands, mut down, mut selected) = (0, 0, false, 0u8);
            let mut allowed_boundary = None;
            let next_pen = |line: &[u8]| -> Option<u8> {
                std::str::from_utf8(line)
                    .ok()?
                    .strip_prefix("SP")?
                    .strip_suffix(";\n")?
                    .parse()
                    .ok()
            };
            while lines.peek().is_some() {
                let mut control = self.control.lock().unwrap();
                let boundary = ticket.pause_before_pen_change
                    && selected > 0
                    && allowed_boundary != Some(sent)
                    && lines
                        .peek()
                        .and_then(|line| next_pen(line))
                        .is_some_and(|p| p > 0 && p != selected);
                if boundary && !control.cancel {
                    control.pause = true;
                    allowed_boundary = Some(sent);
                }
                if control.pause && !control.cancel {
                    drop(control);
                    self.update(&id, "pausing", sent, commands, &forecast)?;
                    attempted = true;
                    port.write_plot(b"PU;\n", "pause", sent)?;
                    // "Paused" means queued motion has drained and pen-up was
                    // confirmed, not merely that a lift command was submitted.
                    crate::completion::wait_paused(port.as_mut(), sent)?;
                    self.update(&id, "paused", sent, commands, &forecast)?;
                    let mut last_ping = Instant::now();
                    control = self.control.lock().unwrap();
                    while control.pause && !control.cancel {
                        control = self
                            .wake
                            .wait_timeout(control, Duration::from_millis(250))
                            .unwrap()
                            .0;
                        if last_ping.elapsed() >= Duration::from_secs(5) {
                            drop(control);
                            port.ping()?;
                            last_ping = Instant::now();
                            control = self.control.lock().unwrap();
                        }
                    }
                    if down && !control.cancel {
                        drop(control);
                        port.write_plot(b"PD;\n", "resume", sent)?;
                        control = self.control.lock().unwrap();
                    }
                }
                if control.cancel {
                    drop(control);
                    cancelling = true;
                    let completion = if attempted {
                        port.write_plot(b"PU;\nSP0;\n", "cancel", sent)?;
                        completion_pending = true;
                        self.update(&id, "finishing", sent, commands, &forecast)?;
                        Some(crate::completion::wait_cancelled(port.as_mut(), sent)?)
                    } else {
                        None
                    };
                    port.close()?;
                    self.finish(&id, completion)?;
                    return Ok(());
                }
                drop(control);
                let mut block = Vec::with_capacity(512);
                let mut count = 0;
                while let Some(line) = lines.peek() {
                    ensure!(
                        line.len() <= 80 && line.ends_with(b";\n"),
                        "Invalid prepared command framing"
                    );
                    if block.len() + line.len() > 512 {
                        break;
                    }
                    if ticket.pause_before_pen_change
                        && selected > 0
                        && next_pen(line).is_some_and(|p| p > 0 && p != selected)
                        && (!block.is_empty() || allowed_boundary != Some(sent))
                    {
                        break;
                    }
                    let line = lines.next().unwrap();
                    if let Some(p) = next_pen(line) {
                        selected = p;
                    }
                    if line.starts_with(b"PD") {
                        down = true;
                    }
                    if line.starts_with(b"PU") || line == b"IN;\n" {
                        down = false;
                    }
                    block.extend_from_slice(line);
                    count += 1;
                }
                // Persist uncertainty BEFORE the first byte can cross the link.
                if !attempted {
                    self.update(&id, "feeding", sent, commands, &forecast)?;
                }
                attempted = true;
                port.write_plot(&block, "prepared", sent)?;
                sent += block.len();
                commands += count;
                self.update(&id, "feeding", sent, commands, &forecast)?;
            }
            completion_pending = true;
            self.update(&id, "finishing", sent, commands, &forecast)?;
            let completion = crate::completion::wait(port.as_mut(), sent)?;
            port.close()?;
            self.finish(&id, Some(completion))
        })();
        if let Err(error) = result {
            let _guard = self.gate.lock().unwrap();
            let saved = (|| -> Result<()> {
                let mut ticket = self.read(&id)?;
                ticket.phase = if completion_pending {
                    "accepted"
                } else if attempted {
                    "uncertain"
                } else {
                    "failed"
                }
                .into();
                let message = if completion_pending && cancelling {
                    format!(
                        "Cancellation was sent, but parking could not be confirmed: {error}. Reset the plotter before another attempt."
                    )
                } else if completion_pending {
                    format!(
                        "All drawing data was sent, but completion could not be confirmed: {error}. Reset the plotter before another attempt."
                    )
                } else {
                    error.to_string()
                };
                ticket.error = Some(message.clone());
                if attempted {
                    let e = ticket.execution.as_mut().context("Missing execution")?;
                    e.state = if completion_pending {
                        "completion_unknown"
                    } else {
                        "uncertain"
                    }
                    .into();
                    e.error = Some(message);
                } else {
                    ticket.execution = None;
                }
                self.save(&ticket)
            })();
            if let Err(save_error) = saved {
                eprintln!("Could not record plot interruption: {save_error:#}; {error:#}");
            }
        }
        self.control.lock().unwrap().attempt = None;
    }
    pub fn control(&self, action: &str, attempt: &str) -> Result<Value> {
        let _guard = self.gate.lock().unwrap();
        let mut ticket = self.active()?.context("No current plot")?;
        ensure!(ticket.id == attempt, "No matching current plot");
        let e = ticket
            .execution
            .as_mut()
            .context("No execution to control")?;
        let mut control = self.control.lock().unwrap();
        match action {
            "pause" | "resume" | "cancel" => {
                ensure!(
                    control.attempt.as_deref() == Some(attempt),
                    "No matching active feed"
                );
                ensure!(
                    e.state != "finishing",
                    "Waiting for the plotter to confirm completion"
                );
                match action {
                    "pause" => {
                        control.pause = true;
                        if e.state != "paused" {
                            e.state = "pausing".into();
                        }
                    }
                    "resume" => {
                        control.pause = false;
                    }
                    _ => {
                        control.cancel = true;
                        e.cancelled = true;
                        e.state = "cancelling".into();
                    }
                }
                self.save(&ticket)?;
                self.wake.notify_all();
            }
            "complete" => {
                ensure!(
                    control.attempt.is_none()
                        && ["submitted", "cancelled", "finished"].contains(&e.state.as_str()),
                    "Wait for the feed and pen to finish before confirming completion"
                );
                e.state = "finished".into();
                self.save(&ticket)?;
            }
            "acknowledge_reset" => {
                ensure!(
                    control.attempt.is_none()
                        && ["uncertain", "interrupted", "completion_unknown"]
                            .contains(&e.state.as_str()),
                    "No interrupted plot to resolve"
                );
                e.state = "reset_acknowledged".into();
                e.error = None;
                ticket.error = None;
                ticket.phase = "accepted".into();
                self.save(&ticket)?;
            }
            _ => bail!("Unknown plotter action"),
        }
        Ok(json!({"execution":ticket.execution}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    struct Fake {
        limits: Mutex<[i32; 4]>,
        writes: Mutex<Vec<Vec<u8>>>,
        block: AtomicBool,
        entered: AtomicBool,
        release: AtomicBool,
        lose_ack: bool,
        opens: AtomicUsize,
        queries: Mutex<Vec<Vec<u8>>>,
        block_query: AtomicBool,
        query_entered: AtomicBool,
        query_release: AtomicBool,
        completion_block: AtomicBool,
        completion_asked: AtomicBool,
        completion_release: AtomicBool,
        bad_completion: AtomicBool,
    }
    impl Fake {
        fn new(block: bool, lose_ack: bool) -> Arc<Self> {
            Arc::new(Self {
                limits: Mutex::new([-8000, -5000, 8000, 5000]),
                writes: Mutex::new(vec![]),
                block: AtomicBool::new(block),
                entered: AtomicBool::new(false),
                release: AtomicBool::new(false),
                lose_ack,
                opens: AtomicUsize::new(0),
                queries: Mutex::new(vec![]),
                block_query: AtomicBool::new(false),
                query_entered: AtomicBool::new(false),
                query_release: AtomicBool::new(false),
                completion_block: AtomicBool::new(false),
                completion_asked: AtomicBool::new(false),
                completion_release: AtomicBool::new(false),
                bad_completion: AtomicBool::new(false),
            })
        }
    }
    struct MockTransport(Arc<Fake>);
    struct MockPort {
        fake: Arc<Fake>,
        reply: Vec<u8>,
    }
    impl Transport for MockTransport {
        fn status(&self) -> Result<Value> {
            Ok(json!({"protocol":2,"serial_present":true,"busy":false}))
        }
        fn open(&self) -> Result<Box<dyn Port>> {
            self.0.opens.fetch_add(1, Ordering::SeqCst);
            Ok(Box::new(MockPort {
                fake: self.0.clone(),
                reply: vec![],
            }))
        }
    }
    impl Port for MockPort {
        fn write(&mut self, bytes: &[u8]) -> Result<()> {
            if [b"OI;".as_slice(), b"OH;", b"OA;\n", b"OS;\n"].contains(&bytes) {
                self.fake.queries.lock().unwrap().push(bytes.to_vec());
            }
            if bytes == b"OI;" {
                if self.fake.block_query.swap(false, Ordering::SeqCst) {
                    self.fake.query_entered.store(true, Ordering::SeqCst);
                    until(|| self.fake.query_release.load(Ordering::SeqCst));
                }
                self.reply = b"7585B\r".to_vec();
                return Ok(());
            }
            if bytes == b"OH;" {
                let l = self.fake.limits.lock().unwrap();
                self.reply = format!("{},{},{},{}\r", l[0], l[1], l[2], l[3]).into_bytes();
                return Ok(());
            }
            if bytes == b"OA;\n" {
                self.fake.completion_asked.store(true, Ordering::SeqCst);
                self.reply = if self.fake.bad_completion.load(Ordering::SeqCst) {
                    b"0,0,1\r"
                } else {
                    b"0,0,0\r"
                }
                .to_vec();
                return Ok(());
            }
            if bytes == b"OS;\n" {
                self.reply = b"16\r".to_vec();
                return Ok(());
            }
            self.fake.writes.lock().unwrap().push(bytes.to_vec());
            if self.fake.block.swap(false, Ordering::SeqCst) {
                self.fake.entered.store(true, Ordering::SeqCst);
                until(|| self.fake.release.load(Ordering::SeqCst));
            }
            if self.fake.lose_ack {
                bail!("Wi-Fi dropped after bytes crossed the link");
            }
            Ok(())
        }
        fn read(&mut self, _: u64) -> Result<Vec<u8>> {
            if self.fake.completion_asked.load(Ordering::SeqCst)
                && self.fake.completion_block.load(Ordering::SeqCst)
                && !self.fake.completion_release.load(Ordering::SeqCst)
            {
                std::thread::sleep(Duration::from_millis(5));
                return Ok(vec![]);
            }
            Ok(std::mem::take(&mut self.reply))
        }
        fn ping(&mut self) -> Result<()> {
            Ok(())
        }
        fn close(&mut self) -> Result<()> {
            Ok(())
        }
    }
    fn until(mut condition: impl FnMut() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(4);
        while !condition() {
            assert!(Instant::now() < deadline, "Timed out");
            std::thread::sleep(Duration::from_millis(5));
        }
    }
    fn fixture(root: &Path, fake: Arc<Fake>) -> (Store, String, Setup, Arc<Plotter>) {
        let store = Store::open(root.join("jobs")).unwrap();
        let source = format!("IN;SP1;PU0,0;{}PU;SP0;", "PD4000,3000;PD0,0;".repeat(100));
        let job = store.import("Proof.hpgl", source.as_bytes(), 40.).unwrap();
        // All transport tests also exercise jobs archived by retention: serial
        // bytes and offsets must be identical to the original plain HPGL.
        let path = store.path(&job.id).unwrap().join("source.hpgl");
        let mut zipped = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        std::io::Write::write_all(&mut zipped, source.as_bytes()).unwrap();
        fs::write(crate::storage::archive(&path), zipped.finish().unwrap()).unwrap();
        fs::remove_file(path).unwrap();
        let mut setup = Setup::new(&job);
        setup.pen_map.insert(1, 2);
        let mut carousel = store.carousel.get().unwrap();
        carousel.slots.insert(
            2,
            crate::carousel::Slot {
                label: "red".into(),
                state: "loaded".into(),
                color: Some("#ff0000".into()),
                measured_color: None,
                width_mm: Some(0.7),
                speed_cm_s: 12,
                force: 2,
            },
        );
        store.carousel.save(carousel).unwrap();
        let p = Plotter::new(
            root.join("plots"),
            Arc::new(MockTransport(fake)),
            store.carousel.clone(),
            true,
        )
        .unwrap();
        (store, job.id, setup, p)
    }
    fn finished(p: &Plotter) {
        until(|| p.control.lock().unwrap().attempt.is_none());
    }
    #[test]
    fn paper_check_only_queries_and_cannot_compete_with_a_plot() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        let (store, _, _, p) = fixture(dir.path(), fake.clone());
        let observation = p.check_paper(&store).unwrap();
        assert_eq!(observation.limits, [-8000, -5000, 8000, 5000]);
        assert_eq!(
            *fake.queries.lock().unwrap(),
            vec![b"OI;".to_vec(), b"OH;".to_vec()]
        );
        assert!(fake.writes.lock().unwrap().is_empty());
        assert!(p.active().unwrap().is_none());
        assert_eq!(
            store.observation().unwrap().unwrap().limits,
            observation.limits
        );
        p.control.lock().unwrap().attempt = Some("another plot".into());
        assert!(p.check_paper(&store).is_err());
        assert_eq!(fake.opens.load(Ordering::SeqCst), 1);
    }
    #[test]
    fn one_click_freezes_layout_and_uses_one_connection_without_duplicate_replays() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        fake.block_query.store(true, Ordering::SeqCst);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        let attempt = Uuid::new_v4().to_string();
        let ticket = p
            .submit(store.clone(), id.clone(), attempt.clone(), setup.clone())
            .unwrap();
        until(|| fake.query_entered.load(Ordering::SeqCst));
        assert!(ticket.observation.is_none());
        assert_eq!(ticket.execution.as_ref().unwrap().state, "connecting");
        assert!(
            p.submit(
                store.clone(),
                id.clone(),
                Uuid::new_v4().to_string(),
                setup.clone()
            )
            .is_err()
        );
        let mut edited = setup.clone();
        edited.rotation = 180;
        edited.pen_map.insert(1, 5);
        store.save_setup(&id, &edited).unwrap();
        assert!(
            p.submit(store.clone(), id.clone(), attempt.clone(), edited)
                .is_err()
        );
        p.submit(store.clone(), id.clone(), attempt.clone(), setup.clone())
            .unwrap();
        fake.query_release.store(true, Ordering::SeqCst);
        finished(&p);
        let bytes = fake.writes.lock().unwrap().concat();
        assert_eq!(
            bytes,
            fs::read(p.path(&attempt).unwrap().join("prepared.hpgl")).unwrap()
        );
        assert!(
            String::from_utf8(bytes.clone())
                .unwrap()
                .contains("SP2;\nVS12;\nFS2;")
        );
        assert_eq!(fake.opens.load(Ordering::SeqCst), 1);
        assert_eq!(
            *fake.queries.lock().unwrap(),
            vec![
                b"OI;".to_vec(),
                b"OH;".to_vec(),
                b"OA;\n".to_vec(),
                b"OS;\n".to_vec()
            ]
        );
        assert_eq!(
            p.read(&attempt).unwrap().execution.unwrap().state,
            "finished"
        );
        assert!(p.free().is_ok());
        assert_eq!(
            p.read(&attempt)
                .unwrap()
                .execution
                .unwrap()
                .completion
                .unwrap()
                .method,
            "hpgl-oa-os"
        );
        let reopened = Plotter::new(
            p.root.clone(),
            Arc::new(MockTransport(fake.clone())),
            p.carousel.clone(),
            true,
        )
        .unwrap();
        reopened.submit(store, id, attempt.clone(), setup).unwrap();
        assert_eq!(fake.writes.lock().unwrap().concat(), bytes);
        reopened.control("complete", &attempt).unwrap();
        assert!(reopened.free().is_ok());
    }
    #[test]
    fn physical_settings_are_frozen_and_preflight_changes_fail_before_drawing() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        fake.block_query.store(true, Ordering::SeqCst);
        let (store, id, mut setup, p) = fixture(dir.path(), fake.clone());
        setup.ink_settings.get_mut(&1).unwrap().speed_cm_s = 55;
        setup.carousel_revision = Some(store.carousel.get().unwrap().revision);
        let ticket = p
            .submit(
                store.clone(),
                id.clone(),
                Uuid::new_v4().to_string(),
                setup.clone(),
            )
            .unwrap();
        until(|| fake.query_entered.load(Ordering::SeqCst));
        assert_eq!(ticket.inks[0]["color"], "#ff0000");
        assert_eq!(ticket.setup.ink_settings[&1].speed_cm_s, 12);
        assert_eq!(ticket.setup.ink_settings[&1].width_mm, Some(0.7));
        let mut inventory = store.carousel.get().unwrap();
        inventory.slots.get_mut(&2).unwrap().color = Some("#800080".into());
        store.carousel.save(inventory).unwrap();
        fake.query_release.store(true, Ordering::SeqCst);
        finished(&p);
        let record = p.read(&ticket.id).unwrap();
        assert_eq!(record.phase, "failed");
        assert!(record.error.unwrap().contains("Carousel changed"));
        assert!(
            p.submit(store, id, Uuid::new_v4().to_string(), setup)
                .unwrap_err()
                .to_string()
                .contains("Carousel changed")
        );
        assert!(fake.writes.lock().unwrap().is_empty());
    }
    #[test]
    fn cancelling_during_startup_never_sends_drawing_bytes() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        fake.block_query.store(true, Ordering::SeqCst);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        let ticket = p
            .submit(store, id, Uuid::new_v4().to_string(), setup)
            .unwrap();
        until(|| fake.query_entered.load(Ordering::SeqCst));
        p.control("cancel", &ticket.id).unwrap();
        fake.query_release.store(true, Ordering::SeqCst);
        finished(&p);
        assert!(fake.writes.lock().unwrap().is_empty());
        let execution = p.read(&ticket.id).unwrap().execution.unwrap();
        assert_eq!(execution.state, "finished");
        assert!(execution.cancelled && execution.completion.is_none());
        assert!(p.free().is_ok());
    }
    #[test]
    fn cancel_waits_for_parking_confirmation_and_preserves_partial_delivery() {
        for bad_reply in [false, true] {
            let dir = tempfile::tempdir().unwrap();
            let fake = Fake::new(true, false);
            fake.completion_block.store(true, Ordering::SeqCst);
            fake.bad_completion.store(bad_reply, Ordering::SeqCst);
            let (store, id, setup, p) = fixture(dir.path(), fake.clone());
            let ticket = p
                .submit(
                    store.clone(),
                    id.clone(),
                    Uuid::new_v4().to_string(),
                    setup.clone(),
                )
                .unwrap();
            until(|| fake.entered.load(Ordering::SeqCst));
            p.control("cancel", &ticket.id).unwrap();
            fake.release.store(true, Ordering::SeqCst);
            until(|| fake.completion_asked.load(Ordering::SeqCst));
            let waiting = p.read(&ticket.id).unwrap().execution.unwrap();
            assert_eq!(waiting.state, "finishing");
            assert!(waiting.cancelled);
            assert!(waiting.submitted_bytes > 0 && waiting.submitted_bytes < waiting.total_bytes);
            assert_eq!(fake.writes.lock().unwrap().last().unwrap(), b"PU;\nSP0;\n");
            assert!(p.free().is_err());
            assert!(p.control("complete", &ticket.id).is_err());
            assert!(p.control("resume", &ticket.id).is_err());
            fake.completion_release.store(true, Ordering::SeqCst);
            finished(&p);
            let result = p.read(&ticket.id).unwrap().execution.unwrap();
            assert_eq!(result.submitted_bytes, waiting.submitted_bytes);
            assert!(result.cancelled);
            if bad_reply {
                assert_eq!(result.state, "completion_unknown");
                assert!(
                    result
                        .error
                        .unwrap()
                        .contains("parking could not be confirmed")
                );
                assert!(p.free().is_err());
                assert!(p.control("complete", &ticket.id).is_err());
            } else {
                assert_eq!(result.state, "finished");
                assert_eq!(result.completion.unwrap().method, "hpgl-oa-os");
                assert!(p.free().is_ok());
            }
            let writes = fake.writes.lock().unwrap().clone();
            p.submit(store, id, ticket.id.clone(), setup).unwrap();
            assert_eq!(
                *fake.writes.lock().unwrap(),
                writes,
                "Retry cannot replay cancellation or drawing"
            );
            assert_eq!(fake.opens.load(Ordering::SeqCst), 1);
        }
    }
    #[test]
    fn recovery_numbering_stays_on_original_even_through_legacy_repair_chains() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        let (store, _, _, p) = fixture(dir.path(), fake.clone());
        let job=store.import("Numbered.hpgl",b"IN;SP1;PU0,0;PD40,0;PU;PU0,40;PD40,40;PU;PU0,80;PD40,80;PU;PU0,120;PD40,120;PU;SP0;",40.).unwrap();
        let mut setup = Setup::new(&job);
        setup.pen_map.insert(1, 2);
        let original = p
            .submit(
                store.clone(),
                job.id.clone(),
                Uuid::new_v4().to_string(),
                setup,
            )
            .unwrap();
        finished(&p);
        let original = p.read(&original.id).unwrap();
        let source = p.program(&original.id).unwrap();
        let strokes = crate::recovery::index(&source).unwrap();
        let r1 = p
            .recover(
                store.clone(),
                original.id.clone(),
                crate::recovery::Request {
                    attempt: Uuid::new_v4().to_string(),
                    source_sha256: original.sha256.clone(),
                    start_stroke: 1,
                    pause_before_pen_change: false,
                },
            )
            .unwrap();
        finished(&p);
        let first = p.recovery_preview(&r1.id).unwrap();
        assert_eq!(first["submission"]["id"], original.id);
        assert_eq!(first["start_stroke"], 1);
        assert_eq!(first["strokes"].as_array().unwrap().len(), 4);
        let r2 = p
            .recover(
                store.clone(),
                r1.id.clone(),
                crate::recovery::Request {
                    attempt: Uuid::new_v4().to_string(),
                    source_sha256: r1.sha256.clone(),
                    start_stroke: 1,
                    pause_before_pen_change: false,
                },
            )
            .unwrap();
        finished(&p);
        assert_eq!(r2.recovery.as_ref().unwrap().attempt, original.id);
        assert_eq!(r2.recovery.as_ref().unwrap().start_stroke, 2);
        assert_eq!(
            p.program(&r2.id).unwrap(),
            crate::recovery::suffix(&source, &strokes[2])
        );
        // Receipts produced before stable numbering linked each shortened file.
        let mut legacy = p.read(&r2.id).unwrap();
        let shortened = p.program(&r1.id).unwrap();
        legacy.recovery = Some(crate::recovery::Origin {
            attempt: r1.id.clone(),
            sha256: r1.sha256.clone(),
            start_stroke: 1,
            start_byte: crate::recovery::index(&shortened).unwrap()[1].offset,
        });
        p.save(&legacy).unwrap();
        let second = p.recovery_preview(&r2.id).unwrap();
        assert_eq!(second["submission"]["id"], original.id);
        assert_eq!(second["start_stroke"], 2);
        assert_eq!(second["strokes"], first["strokes"]);
        assert_eq!(second["progress"], first["progress"]);
        let opens = fake.opens.load(Ordering::SeqCst);
        legacy.recovery.as_mut().unwrap().attempt = legacy.id.clone();
        legacy.recovery.as_mut().unwrap().sha256 = legacy.sha256.clone();
        p.save(&legacy).unwrap();
        assert!(p.recovery_preview(&legacy.id).is_err());
        assert_eq!(fake.opens.load(Ordering::SeqCst), opens);
    }
    #[test]
    fn explicit_pause_and_resume_are_generated_on_the_service() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(true, false);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        let ticket = p
            .submit(
                store.clone(),
                id.clone(),
                Uuid::new_v4().to_string(),
                setup.clone(),
            )
            .unwrap();
        until(|| fake.entered.load(Ordering::SeqCst));
        p.control("pause", &ticket.id).unwrap();
        fake.release.store(true, Ordering::SeqCst);
        until(|| p.read(&ticket.id).unwrap().execution.unwrap().state == "paused");
        assert_eq!(fake.writes.lock().unwrap().last().unwrap(), b"PU;\n");
        p.control("resume", &ticket.id).unwrap();
        finished(&p);
        assert!(fake.writes.lock().unwrap().iter().any(|w| w == b"PD;\n"));
        assert_eq!(
            p.read(&ticket.id).unwrap().execution.unwrap().state,
            "finished"
        );
    }
    #[test]
    fn repair_preserves_archive_and_pauses_before_enqueuing_a_new_pen() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        let (store, _, _, p) = fixture(dir.path(), fake.clone());
        let mut carousel = store.carousel.get().unwrap();
        carousel.slots.insert(5, carousel.slots[&2].clone());
        store.carousel.save(carousel).unwrap();
        let job = store.import("Repair.hpgl", b"IN;SP1;PU0,0;PD40,0;PU;PU0,40;PD40,40;PU;SP5;PU0,80;PD40,80;PU;SP1;PU0,120;PD40,120;PU;SP0;", 40.).unwrap();
        let mut setup = Setup::new(&job);
        setup.pen_map.insert(1, 2);
        let original = p
            .submit(
                store.clone(),
                job.id.clone(),
                Uuid::new_v4().to_string(),
                setup,
            )
            .unwrap();
        finished(&p);
        let original = p.read(&original.id).unwrap();
        let record = fs::read(p.path(&original.id).unwrap().join("record.json")).unwrap();
        let source = p.program(&original.id).unwrap();
        let index = crate::recovery::index(&source).unwrap();
        assert_eq!(index.len(), 4);
        let path = p.path(&original.id).unwrap().join("prepared.hpgl");
        let mut zipped = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        std::io::Write::write_all(&mut zipped, source.as_bytes()).unwrap();
        fs::write(crate::storage::archive(&path), zipped.finish().unwrap()).unwrap();
        fs::remove_file(path).unwrap();
        assert_eq!(p.program(&original.id).unwrap(), source);
        let old = p.path(&Uuid::new_v4().to_string()).unwrap();
        fs::create_dir(&old).unwrap();
        fs::write(
            old.join("record.json"),
            serde_json::to_vec(&json!({
                "job_id":Uuid::new_v4().to_string(),"bytes":100,
                "execution":{"completion":"operator confirmed pen parked before bridge migration"}
            }))
            .unwrap(),
        )
        .unwrap();
        assert_eq!(p.history(&job.id).unwrap().len(), 1);
        assert_eq!(
            p.recovery_preview(&original.id).unwrap()["strokes"]
                .as_array()
                .unwrap()
                .len(),
            4
        );
        assert_eq!(
            fake.opens.load(Ordering::SeqCst),
            1,
            "Offline preview never opens serial"
        );
        let request = crate::recovery::Request {
            attempt: Uuid::new_v4().to_string(),
            source_sha256: original.sha256.clone(),
            start_stroke: 1,
            pause_before_pen_change: true,
        };
        let mut invalid = request.clone();
        invalid.source_sha256 = "wrong".into();
        assert!(
            p.recover(store.clone(), original.id.clone(), invalid)
                .is_err()
        );
        let mut invalid = request.clone();
        invalid.start_stroke = 4;
        assert!(
            p.recover(store.clone(), original.id.clone(), invalid)
                .is_err()
        );
        assert_eq!(fake.opens.load(Ordering::SeqCst), 1);
        // Subsequent document/inventory edits must not regenerate this repair.
        let mut carousel = store.carousel.get().unwrap();
        carousel.slots.get_mut(&2).unwrap().speed_cm_s = 3;
        store.carousel.save(carousel).unwrap();
        fake.writes.lock().unwrap().clear();
        fake.completion_asked.store(false, Ordering::SeqCst);
        fake.completion_block.store(true, Ordering::SeqCst);
        let repair = p
            .recover(store.clone(), original.id.clone(), request.clone())
            .unwrap();
        assert_eq!(
            p.recover(store.clone(), original.id.clone(), request.clone())
                .unwrap()
                .id,
            repair.id
        );
        let mut duplicate = request.clone();
        duplicate.attempt = Uuid::new_v4().to_string();
        assert!(
            p.recover(store.clone(), original.id.clone(), duplicate)
                .is_err()
        );
        until(|| {
            assert!(
                p.control.lock().unwrap().attempt.is_some(),
                "repair stopped: {:?}",
                p.read(&repair.id).unwrap()
            );
            fake.completion_asked.load(Ordering::SeqCst)
        });
        assert_eq!(
            p.read(&repair.id).unwrap().execution.unwrap().state,
            "pausing"
        );
        let writes = String::from_utf8(fake.writes.lock().unwrap().concat()).unwrap();
        assert!(writes.starts_with("PU;\nIN;\nPA;\nPU;\nSP2;\nVS12;\nFS2;\n"));
        assert!(
            !writes.contains("SP5;"),
            "Next pen stays out of all buffers"
        );
        assert_eq!(fake.writes.lock().unwrap().last().unwrap(), b"PU;\n");
        fake.completion_release.store(true, Ordering::SeqCst);
        until(|| p.read(&repair.id).unwrap().execution.unwrap().state == "paused");
        let paused_bytes = p
            .read(&repair.id)
            .unwrap()
            .execution
            .unwrap()
            .submitted_bytes;
        assert_eq!(
            p.program(&repair.id).unwrap(),
            crate::recovery::suffix(&source, &index[1])
        );
        assert_eq!(
            repair.recovery.as_ref().unwrap().start_byte,
            index[1].offset
        );
        p.control("resume", &repair.id).unwrap();
        until(|| {
            let e = p.read(&repair.id).unwrap().execution.unwrap();
            e.state == "paused" && e.submitted_bytes > paused_bytes
        });
        let writes = String::from_utf8(fake.writes.lock().unwrap().concat()).unwrap();
        assert_eq!(writes.matches("SP5;").count(), 1);
        assert_eq!(
            writes.matches("SP2;").count(),
            1,
            "Second pen boundary also stops before SP"
        );
        p.control("cancel", &repair.id).unwrap();
        finished(&p);
        let writes = fake.writes.lock().unwrap();
        assert_eq!(writes.last().unwrap(), b"PU;\nSP0;\n");
        assert!(
            !writes.iter().any(|w| w == b"PD;\n"),
            "Cancel never restores pen-down"
        );
        assert_eq!(
            fake.opens.load(Ordering::SeqCst),
            2,
            "Idempotent retry never opens another connection"
        );
        assert_eq!(
            fs::read(p.path(&original.id).unwrap().join("record.json")).unwrap(),
            record
        );
    }
    #[test]
    fn pause_waits_for_lift_confirmation_and_rejects_pen_down_reply() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(true, false);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        fake.bad_completion.store(true, Ordering::SeqCst);
        let ticket = p
            .submit(store, id, Uuid::new_v4().to_string(), setup)
            .unwrap();
        until(|| fake.entered.load(Ordering::SeqCst));
        p.control("pause", &ticket.id).unwrap();
        fake.release.store(true, Ordering::SeqCst);
        finished(&p);
        assert_eq!(
            p.read(&ticket.id).unwrap().execution.unwrap().state,
            "uncertain"
        );
        assert_eq!(fake.writes.lock().unwrap().last().unwrap(), b"PU;\n");
        assert!(!fake.writes.lock().unwrap().iter().any(|w| w == b"PD;\n"));
        assert!(p.free().is_err());
    }
    #[test]
    fn all_bytes_sent_stays_exclusive_until_completion_and_survives_browser_polling() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        fake.completion_block.store(true, Ordering::SeqCst);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        let ticket = p
            .submit(
                store.clone(),
                id.clone(),
                Uuid::new_v4().to_string(),
                setup.clone(),
            )
            .unwrap();
        until(|| fake.completion_asked.load(Ordering::SeqCst));
        let e = p.read(&ticket.id).unwrap().execution.unwrap();
        assert_eq!(e.state, "finishing");
        assert_eq!(e.submitted_bytes, e.total_bytes);
        assert!(e.completion.is_none());
        assert!(p.free().is_err());
        assert!(p.control("complete", &ticket.id).is_err());
        assert!(p.control("cancel", &ticket.id).is_err());
        for _ in 0..3 {
            assert_eq!(
                p.status().unwrap()["device"]["execution"]["state"],
                "finishing"
            );
        }
        assert_eq!(fake.opens.load(Ordering::SeqCst), 1);
        p.submit(store.clone(), id.clone(), ticket.id.clone(), setup.clone())
            .unwrap();
        assert!(
            p.submit(store, id, Uuid::new_v4().to_string(), setup)
                .is_err()
        );
        fake.completion_release.store(true, Ordering::SeqCst);
        finished(&p);
        assert_eq!(
            p.read(&ticket.id).unwrap().execution.unwrap().state,
            "finished"
        );
        assert!(p.free().is_ok());
    }
    #[test]
    fn missing_completion_is_distinct_from_uncertain_drawing_delivery_and_never_replays() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        fake.bad_completion.store(true, Ordering::SeqCst);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        let ticket = p
            .submit(
                store.clone(),
                id.clone(),
                Uuid::new_v4().to_string(),
                setup.clone(),
            )
            .unwrap();
        finished(&p);
        let e = p.read(&ticket.id).unwrap().execution.unwrap();
        assert_eq!(e.state, "completion_unknown");
        assert_eq!(e.submitted_bytes, e.total_bytes);
        assert!(e.completion.is_none());
        assert!(p.free().is_err());
        assert!(p.control("complete", &ticket.id).is_err());
        let writes = fake.writes.lock().unwrap().clone();
        p.submit(store, id, ticket.id.clone(), setup).unwrap();
        assert_eq!(*fake.writes.lock().unwrap(), writes);
        p.control("acknowledge_reset", &ticket.id).unwrap();
        assert!(p.free().is_ok());
    }
    #[test]
    fn restart_while_finishing_never_reopens_port_or_assumes_completion() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        let ticket = p
            .submit(store, id, Uuid::new_v4().to_string(), setup)
            .unwrap();
        finished(&p);
        let mut record = p.read(&ticket.id).unwrap();
        let e = record.execution.as_mut().unwrap();
        e.state = "finishing".into();
        e.completion = None;
        p.save(&record).unwrap();
        let reopened = Plotter::new(
            p.root.clone(),
            Arc::new(MockTransport(fake.clone())),
            p.carousel.clone(),
            true,
        )
        .unwrap();
        let record = reopened.read(&ticket.id).unwrap();
        assert_eq!(record.execution.unwrap().state, "completion_unknown");
        assert!(reopened.free().is_err());
        assert_eq!(fake.opens.load(Ordering::SeqCst), 1);
        reopened.control("acknowledge_reset", &ticket.id).unwrap();
        assert!(reopened.free().is_ok());
    }
    #[test]
    fn legacy_submitted_attempt_still_requires_operator_confirmation() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        let ticket = p
            .submit(store, id, Uuid::new_v4().to_string(), setup)
            .unwrap();
        finished(&p);
        let mut record = p.read(&ticket.id).unwrap();
        let e = record.execution.as_mut().unwrap();
        e.state = "submitted".into();
        e.completion = None;
        p.save(&record).unwrap();
        let reopened = Plotter::new(
            p.root.clone(),
            Arc::new(MockTransport(fake.clone())),
            p.carousel.clone(),
            true,
        )
        .unwrap();
        assert!(reopened.free().is_err());
        assert_eq!(fake.opens.load(Ordering::SeqCst), 1);
        reopened.control("complete", &ticket.id).unwrap();
        assert!(reopened.free().is_ok());
    }
    #[test]
    fn cancel_while_paused_stows_pen_without_resuming_drawing() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(true, false);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        let ticket = p
            .submit(
                store.clone(),
                id.clone(),
                Uuid::new_v4().to_string(),
                setup.clone(),
            )
            .unwrap();
        until(|| fake.entered.load(Ordering::SeqCst));
        p.control("pause", &ticket.id).unwrap();
        fake.release.store(true, Ordering::SeqCst);
        until(|| p.read(&ticket.id).unwrap().execution.unwrap().state == "paused");
        p.control("cancel", &ticket.id).unwrap();
        finished(&p);
        let writes = fake.writes.lock().unwrap();
        assert_eq!(writes.last().unwrap(), b"PU;\nSP0;\n");
        assert!(!writes.iter().any(|w| w == b"PD;\n"));
        assert_eq!(
            p.read(&ticket.id).unwrap().execution.unwrap().state,
            "finished"
        );
    }
    #[test]
    fn disconnect_does_not_send_pen_commands_or_replay_ambiguous_bytes() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, true);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        let ticket = p
            .submit(
                store.clone(),
                id.clone(),
                Uuid::new_v4().to_string(),
                setup.clone(),
            )
            .unwrap();
        finished(&p);
        let record = p.read(&ticket.id).unwrap();
        assert_eq!(record.phase, "uncertain");
        assert_eq!(record.execution.unwrap().submitted_bytes, 0);
        assert_eq!(fake.writes.lock().unwrap().len(), 1); // No automatic PU or retry.
        p.status().unwrap();
        p.submit(store, id, ticket.id.clone(), setup).unwrap();
        assert!(p.free().is_err());
        assert_eq!(fake.writes.lock().unwrap().len(), 1);
        p.control("acknowledge_reset", &ticket.id).unwrap();
        assert!(p.free().is_ok());
        assert_eq!(fake.writes.lock().unwrap().len(), 1);
    }
    #[test]
    fn paper_change_and_disabled_connection_fail_before_drawing() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        *fake.limits.lock().unwrap() = [-1000, -1000, 1000, 1000];
        let ticket = p
            .submit(
                store.clone(),
                id.clone(),
                Uuid::new_v4().to_string(),
                setup.clone(),
            )
            .unwrap();
        finished(&p);
        assert_eq!(p.read(&ticket.id).unwrap().phase, "failed");
        assert!(p.free().is_ok());
        assert!(fake.writes.lock().unwrap().is_empty());
        let disabled = Plotter::new(
            dir.path().join("disabled"),
            Arc::new(MockTransport(fake.clone())),
            p.carousel.clone(),
            false,
        )
        .unwrap();
        assert!(
            disabled
                .submit(
                    store.clone(),
                    id.clone(),
                    Uuid::new_v4().to_string(),
                    setup.clone()
                )
                .is_err()
        );
        let mut inventory = store.carousel.get().unwrap();
        inventory.slots.get_mut(&2).unwrap().state = "unavailable".into();
        inventory.slots.get_mut(&2).unwrap().label = "broken".into();
        store.carousel.save(inventory).unwrap();
        let blocked = Plotter::new(
            dir.path().join("blocked"),
            Arc::new(MockTransport(fake)),
            store.carousel.clone(),
            true,
        )
        .unwrap();
        assert!(
            blocked
                .submit(store, id, Uuid::new_v4().to_string(), setup)
                .unwrap_err()
                .to_string()
                .contains("Slot 2")
        );
    }
    #[test]
    fn service_restart_marks_an_active_feed_interrupted_without_opening_port() {
        let dir = tempfile::tempdir().unwrap();
        let fake = Fake::new(false, false);
        let (store, id, setup, p) = fixture(dir.path(), fake.clone());
        let mut ticket = p
            .submit(
                store.clone(),
                id.clone(),
                Uuid::new_v4().to_string(),
                setup.clone(),
            )
            .unwrap();
        finished(&p);
        let bytes = fake.writes.lock().unwrap().concat();
        let opens = fake.opens.load(Ordering::SeqCst);
        ticket.phase = "accepted".into();
        ticket.execution = Some(p.execution(&ticket, "paused"));
        p.save(&ticket).unwrap();
        atomic(&p.root.join("active.json"), &ticket.id).unwrap();
        let reopened = Plotter::new(
            p.root.clone(),
            Arc::new(MockTransport(fake.clone())),
            p.carousel.clone(),
            true,
        )
        .unwrap();
        assert_eq!(
            reopened.read(&ticket.id).unwrap().execution.unwrap().state,
            "interrupted"
        );
        assert!(reopened.free().is_err());
        assert_eq!(fake.writes.lock().unwrap().concat(), bytes);
        assert_eq!(fake.opens.load(Ordering::SeqCst), opens);
        // A pending connection from a prior process never starts on recovery.
        ticket.phase = "starting".into();
        ticket.execution = Some(p.execution(&ticket, "connecting"));
        p.save(&ticket).unwrap();
        let pending = Plotter::new(
            p.root.clone(),
            Arc::new(MockTransport(fake.clone())),
            p.carousel.clone(),
            true,
        )
        .unwrap();
        assert_eq!(pending.read(&ticket.id).unwrap().phase, "failed");
        assert!(pending.free().is_ok());
        assert_eq!(fake.opens.load(Ordering::SeqCst), opens);
    }
}
