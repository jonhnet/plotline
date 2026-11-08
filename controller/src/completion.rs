//! Pen-up barriers stay on the feeding connection, after pause or final PU/SP0.
//! An empty host/device input buffer is never used as the completion barrier.
use crate::serial::Port;
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Completion {
    pub method: String,
    pub position: [i32; 2],
    pub status: u8,
    pub confirmed_at: u64,
}

fn reply(port: &mut dyn Port, timeout: Duration) -> Result<String> {
    let deadline = Instant::now() + timeout;
    let mut bytes = Vec::new();
    while Instant::now() < deadline {
        let wait = deadline.saturating_duration_since(Instant::now());
        bytes.extend(port.read(wait.as_millis().clamp(1, 1000) as u64)?);
        ensure!(bytes.len() <= 128, "Oversized completion reply");
        // CR/LF can arrive separately. Ignore a previous reply's trailing LF,
        // but reject multiple/non-ASCII responses instead of guessing identity.
        let start = bytes.iter().position(|b| !b"\r\n".contains(b));
        if let Some(start) = start
            && let Some(end) = bytes[start..].iter().position(|b| b"\r\n".contains(b))
        {
            let end = start + end;
            ensure!(
                bytes[end..].iter().all(|b| b"\r\n".contains(b)),
                "Unexpected extra completion reply"
            );
            ensure!(bytes[start..end].is_ascii(), "Non-ASCII completion reply");
            return Ok(std::str::from_utf8(&bytes[start..end])?.trim().to_owned());
        }
    }
    bail!("Timed out waiting for the plotter's completion reply")
}

fn ask(
    port: &mut dyn Port,
    command: &[u8],
    sent: usize,
    timeout: Duration,
    kind: &str,
) -> Result<String> {
    ensure!(
        port.read(0)?.iter().all(|b| b"\r\n".contains(b)),
        "Unexpected serial input before completion query"
    );
    port.write_plot(command, kind, sent)?;
    reply(port, timeout)
}

pub fn wait(port: &mut dyn Port, sent: usize) -> Result<Completion> {
    // Slow vectors, a large device buffer or View mode may defer this reply.
    // Timeout is an unresolved result, never permission to start another job.
    confirm(port, sent, Duration::from_secs(30 * 60))
}

fn confirm(port: &mut dyn Port, sent: usize, timeout: Duration) -> Result<Completion> {
    confirm_kind(port, sent, timeout, "completion-query")
}

pub fn wait_paused(port: &mut dyn Port, sent: usize) -> Result<Completion> {
    confirm_kind(port, sent, Duration::from_secs(30 * 60), "pause-query")
}

pub fn wait_cancelled(port: &mut dyn Port, sent: usize) -> Result<Completion> {
    confirm_kind(port, sent, Duration::from_secs(30 * 60), "cancel-query")
}

fn confirm_kind(
    port: &mut dyn Port,
    sent: usize,
    timeout: Duration,
    kind: &str,
) -> Result<Completion> {
    let actual = ask(port, b"OA;\n", sent, timeout, kind)?;
    let fields: Vec<i32> = actual
        .split(',')
        .map(|s| s.trim().parse())
        .collect::<std::result::Result<_, _>>()
        .context("Invalid OA completion reply")?;
    ensure!(
        fields.len() == 3
            && fields[..2].iter().all(|n| (-32767..=32767).contains(n))
            && fields[2] == 0,
        "Completion reply did not confirm a valid position with the pen up: {actual}"
    );
    let status: u8 = ask(port, b"OS;\n", sent, Duration::from_secs(5), kind)?
        .parse()
        .context("Invalid OS completion reply")?;
    ensure!(
        status & 32 == 0,
        "The plotter reports an HP-GL error at completion (status {status})"
    );
    ensure!(
        status & 1 == 0 && status & 16 != 0,
        "The plotter did not confirm pen-up and an empty command buffer (status {status})"
    );
    Ok(Completion {
        method: "hpgl-oa-os".into(),
        position: [fields[0], fields[1]],
        status,
        confirmed_at: SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    struct Fake {
        answers: VecDeque<Vec<Vec<u8>>>,
        reads: VecDeque<Vec<u8>>,
        writes: Vec<Vec<u8>>,
        offsets: Vec<usize>,
        fail_read: bool,
    }
    impl Fake {
        fn new(oa: &[&[u8]], os: &[&[u8]]) -> Self {
            Self {
                answers: [oa, os]
                    .iter()
                    .map(|chunks| chunks.iter().map(|b| b.to_vec()).collect())
                    .collect(),
                reads: VecDeque::new(),
                writes: vec![],
                offsets: vec![],
                fail_read: false,
            }
        }
    }
    impl Port for Fake {
        fn write(&mut self, _: &[u8]) -> Result<()> {
            panic!("Queries must be traced");
        }
        fn write_plot(&mut self, bytes: &[u8], kind: &str, offset: usize) -> Result<()> {
            assert_eq!(kind, "completion-query");
            self.writes.push(bytes.to_vec());
            self.offsets.push(offset);
            self.reads = self.answers.pop_front().unwrap().into();
            Ok(())
        }
        fn read(&mut self, wait: u64) -> Result<Vec<u8>> {
            if wait > 0 && self.fail_read {
                bail!("Disconnected");
            }
            if wait > 0 && self.reads.is_empty() {
                std::thread::sleep(Duration::from_millis(1));
            }
            Ok(self.reads.pop_front().unwrap_or_default())
        }
        fn ping(&mut self) -> Result<()> {
            Ok(())
        }
        fn close(&mut self) -> Result<()> {
            Ok(())
        }
    }
    #[test]
    fn delayed_split_replies_do_not_confuse_host_delivery_with_completion() {
        let mut port = Fake::new(
            &[b"", b"\n", b"-12", b"3,456,", b"0", b"\r", b"\n"],
            &[b"2", b"4\r\n"],
        );
        let result = confirm(&mut port, 456, Duration::from_secs(1)).unwrap();
        assert_eq!(result.position, [-123, 456]);
        assert_eq!(result.status, 24);
        assert_eq!(port.writes, vec![b"OA;\n".to_vec(), b"OS;\n".to_vec()]);
        assert_eq!(port.offsets, vec![456, 456]);
    }
    #[test]
    fn malformed_error_and_incomplete_replies_never_confirm_completion() {
        for oa in [
            b"".as_slice(),
            b"1,2,0",
            b"1,2,1\r",
            b"1,2,0,0\r",
            b"32768,2,0\r",
            b"1,2,0\r16\r",
            b"7585B\r",
            b"\xff\r",
        ] {
            assert!(
                confirm(
                    &mut Fake::new(&[oa], &[b"16\r"]),
                    0,
                    Duration::from_millis(5)
                )
                .is_err()
            );
        }
        for status in [b"48\r".as_slice(), b"17\r", b"0\r", b"256\r", b"16\r16\r"] {
            assert!(
                confirm(
                    &mut Fake::new(&[b"1,2,0\r"], &[status]),
                    0,
                    Duration::from_millis(5)
                )
                .is_err()
            );
        }
        let mut port = Fake::new(&[b"1,2,0\r"], &[b"16\r"]);
        port.fail_read = true;
        assert!(confirm(&mut port, 0, Duration::from_millis(5)).is_err());
        assert_eq!(port.writes.len(), 1);
    }
    #[test]
    fn stale_and_oversized_input_is_rejected_without_blind_retries() {
        let mut port = Fake::new(&[b"1,2,0\r"], &[b"16\r"]);
        port.reads.push_back(b"stale\r".to_vec());
        assert!(confirm(&mut port, 0, Duration::from_millis(5)).is_err());
        assert!(port.writes.is_empty());
        let big = vec![b'1'; 129];
        let mut port = Fake::new(&[&big], &[b"16\r"]);
        assert!(confirm(&mut port, 0, Duration::from_millis(5)).is_err());
        assert_eq!(port.writes.len(), 1);
    }
}
