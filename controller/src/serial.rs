//! A persistent byte connection. The remote endpoint knows nothing about HPGL.
use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use std::{
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{Arc, Mutex, mpsc},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub trait Port: Send {
    fn write(&mut self, bytes: &[u8]) -> Result<()>;
    fn read(&mut self, timeout_ms: u64) -> Result<Vec<u8>>;
    fn ping(&mut self) -> Result<()>;
    fn close(&mut self) -> Result<()>;
    fn start_trace(&mut self, _path: &Path, _metadata: Value) -> Result<()> {
        Ok(())
    }
    fn write_plot(&mut self, bytes: &[u8], _kind: &str, _prepared_offset: usize) -> Result<()> {
        self.write(bytes)
    }
}
pub trait Transport: Send + Sync {
    fn status(&self) -> Result<Value>;
    fn open(&self) -> Result<Box<dyn Port>>;
}

pub struct Bridge {
    pub config: PathBuf,
    pub helper: Option<PathBuf>,
    pub control: PathBuf,
}
impl Bridge {
    fn process(&self) -> Result<Process> {
        let mut command = if let Some(helper) = &self.helper {
            let mut c = Command::new("python3");
            c.arg(helper)
                .arg("--config")
                .arg(&self.config)
                .arg("--control-dir")
                .arg(&self.control);
            c
        } else {
            let config: Value = serde_json::from_slice(&fs::read(&self.config)?)?;
            let field = |name: &str| -> Result<String> {
                Ok(config[name]
                    .as_str()
                    .with_context(|| format!("Missing {name} in device connection"))?
                    .to_owned())
            };
            let destination = field("destination")?;
            ensure!(
                !destination.starts_with('-')
                    && !destination.is_empty()
                    && destination
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || "_.@:-".contains(c)),
                "Invalid SSH destination"
            );
            let mut c = Command::new("ssh");
            c.args(["-T", "-i"])
                .arg(field("identity_file")?)
                .args([
                    "-o",
                    "IdentitiesOnly=yes",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "-o",
                    "ServerAliveInterval=5",
                    "-o",
                    "ServerAliveCountMax=2",
                    "-o",
                    "ControlMaster=auto",
                    "-o",
                    "ControlPersist=60",
                    "-o",
                ])
                .arg(format!(
                    "ControlPath={}",
                    self.control.join("ssh-%C").display()
                ))
                .arg("-o")
                .arg(format!("UserKnownHostsFile={}", field("known_hosts_file")?))
                .arg(destination);
            c
        };
        Process::spawn(
            command
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped()),
        )
    }
}
impl Transport for Bridge {
    fn status(&self) -> Result<Value> {
        let mut connection = self.process()?;
        let result = connection.call(json!({"op":"status"}))?;
        ensure!(
            result["protocol"] == 2,
            "Update stain to the serial bridge before connecting"
        );
        connection.finish()?;
        Ok(result)
    }
    fn open(&self) -> Result<Box<dyn Port>> {
        let mut connection = self.process()?;
        let result = connection.call(json!({"op":"open"}))?;
        ensure!(
            result["opened"] == true && result["protocol"] == 2,
            "Serial bridge did not open the port"
        );
        connection.timing_capable = result["capabilities"]
            .as_array()
            .is_some_and(|caps| caps.iter().any(|c| c == "tx_queue_timing_v1"));
        Ok(Box::new(connection))
    }
}

struct Process {
    child: Child,
    input: ChildStdin,
    replies: mpsc::Receiver<std::result::Result<Value, String>>,
    errors: Arc<Mutex<String>>,
    timing_capable: bool,
    trace: Option<Trace>,
    stream_offset: usize,
}

// JSONL is append-only: a disconnected transfer retains its valid prefix.
// The Pi never needs a spool, job identity, HPGL parser, or persistent log.
struct Trace {
    file: File,
    origin: Instant,
}
impl Trace {
    fn record(&mut self, mut event: Value) -> Result<()> {
        event["host_elapsed_ns"] = json!(self.origin.elapsed().as_nanos() as u64);
        let mut line = serde_json::to_vec(&event)?;
        line.push(b'\n');
        self.file.write_all(&line)?;
        Ok(())
    }
}
impl Process {
    fn spawn(command: &mut Command) -> Result<Self> {
        let mut child = command
            .spawn()
            .context("Could not open the serial connection")?;
        let input = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let mut stderr = child.stderr.take().unwrap();
        let errors = Arc::new(Mutex::new(String::new()));
        let captured = errors.clone();
        std::thread::spawn(move || {
            let mut buffer = [0; 1024];
            while let Ok(n) = stderr.read(&mut buffer) {
                if n == 0 {
                    break;
                }
                let mut message = captured.lock().unwrap();
                if message.len() < 4096 {
                    message.push_str(&String::from_utf8_lossy(&buffer[..n]));
                }
            }
        });
        let (send, replies) = mpsc::sync_channel(2);
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                let mut line = Vec::new();
                let parsed = match reader.by_ref().take(16385).read_until(b'\n', &mut line) {
                    Ok(0) => break,
                    Ok(_) if line.len() > 16384 || !line.ends_with(b"\n") => {
                        Err("Invalid serial bridge response".into())
                    }
                    Ok(_) => serde_json::from_slice(&line)
                        .map_err(|e| format!("Invalid serial bridge response: {e}")),
                    Err(error) => Err(error.to_string()),
                };
                let failed = parsed.is_err();
                if send.send(parsed).is_err() || failed {
                    break;
                }
            }
        });
        Ok(Self {
            child,
            input,
            replies,
            errors,
            timing_capable: false,
            trace: None,
            stream_offset: 0,
        })
    }
    fn record(&mut self, event: Value) -> Result<()> {
        if let Some(trace) = &mut self.trace {
            trace.record(event)?;
        }
        Ok(())
    }
    fn call(&mut self, mut request: Value) -> Result<Value> {
        let op = request["op"].clone();
        let result = self.exchange(&mut request);
        match &result {
            Ok(value) => self.record(json!({"event":"reply", "op":op, "result":value}))?,
            Err(error) => {
                let _ = self.record(json!({"event":"error", "op":op, "error":error.to_string()}));
            }
        }
        if let Some(trace) = &self.trace {
            trace.file.sync_data()?;
        }
        result
    }
    fn exchange(&mut self, request: &mut Value) -> Result<Value> {
        request["protocol"] = json!(2);
        let mut line = serde_json::to_vec(request)?;
        line.push(b'\n');
        self.input
            .write_all(&line)
            .context("Serial connection closed while sending")?;
        self.input.flush()?;
        // A powered-off plotter may hold CTS low and stall even a tiny query
        // write. Bound identity/paper probes independently of drawing packets.
        let query = request["op"] == "write"
            && [b"OI;".as_slice(), b"OH;"]
                .iter()
                .any(|bytes| request["data"] == json!(bytes));
        let deadline = Instant::now() + Duration::from_secs(if query { 5 } else { 150 });
        let reply = loop {
            let reply = match self
                .replies
                .recv_timeout(deadline.saturating_duration_since(Instant::now()))
            {
                Ok(Ok(value)) => value,
                Ok(Err(error)) => bail!("{error}"),
                Err(error) => {
                    let detail = self.errors.lock().unwrap().trim().to_owned();
                    bail!(
                        "Serial connection interrupted ({error}){}",
                        if detail.is_empty() {
                            String::new()
                        } else {
                            format!(": {detail}")
                        }
                    );
                }
            };
            if reply["event"] == "tx_queue_timing_v1" && self.trace.is_some() {
                self.record(reply)?;
                continue;
            }
            break reply;
        };
        ensure!(
            reply["ok"] == true,
            "{}",
            reply["error"]
                .as_str()
                .unwrap_or("Serial bridge rejected the request")
        );
        Ok(reply["result"].clone())
    }
    fn finish(&mut self) -> Result<()> {
        self.call(json!({"op":"close"}))?;
        Ok(())
    }
}
impl Port for Process {
    fn write(&mut self, bytes: &[u8]) -> Result<()> {
        ensure!(
            !bytes.is_empty() && bytes.len() <= 512,
            "Serial packets contain 1..512 bytes"
        );
        let response = self.call(json!({"op":"write", "data":bytes}))?;
        ensure!(
            response["written"] == bytes.len(),
            "Serial write was not fully acknowledged"
        );
        Ok(())
    }
    fn read(&mut self, timeout_ms: u64) -> Result<Vec<u8>> {
        Ok(serde_json::from_value(
            self.call(json!({"op":"read", "size":1024, "timeout_ms":timeout_ms}))?["data"].clone(),
        )?)
    }
    fn ping(&mut self) -> Result<()> {
        self.call(json!({"op":"status"}))?;
        Ok(())
    }
    fn close(&mut self) -> Result<()> {
        self.finish()
    }
    fn start_trace(&mut self, path: &Path, metadata: Value) -> Result<()> {
        ensure!(self.trace.is_none(), "Serial trace already started");
        self.trace = Some(Trace {
            file: OpenOptions::new().write(true).create_new(true).open(path)?,
            origin: Instant::now(),
        });
        self.record(json!({
            "event":"header", "schema":"plotline.serial-timing.v1",
            "wall_time_ns":SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos() as u64,
            "pi_timing_available":self.timing_capable, "metadata":metadata,
        }))?;
        self.trace.as_ref().unwrap().file.sync_all()?;
        File::open(path.parent().context("Trace needs a parent directory")?)?.sync_all()?;
        if self.timing_capable {
            self.call(json!({"op":"timing"}))?;
        }
        Ok(())
    }
    fn write_plot(&mut self, bytes: &[u8], kind: &str, prepared_offset: usize) -> Result<()> {
        // Intent precedes the write; a missing acknowledgement never proves
        // that bytes were not delivered. Include injected pause/resume bytes.
        self.record(json!({"event":"write", "kind":kind,
            "stream_offset":self.stream_offset, "prepared_offset":prepared_offset, "data":bytes}))?;
        self.write(bytes)?;
        self.stream_offset += bytes.len();
        Ok(())
    }
}
impl Drop for Process {
    fn drop(&mut self) {
        // Parent shutdown/drop closes this stream, never leaves an autonomous job.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
