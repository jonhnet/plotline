//! Native evaluation of the adopted Snoqualmie model. Never contacts hardware.
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::sync::OnceLock;

const MODEL_ID: &str = "hp7585-snoqualmie-v1";
#[derive(Deserialize)]
struct Model {
    parameters: Parameters,
    serial_byte_rate: f64,
    host_turnaround_s: f64,
}
#[derive(Deserialize)]
struct Parameters {
    draw_speed_scale: f64,
    pen_up_mm_s: f64,
    acceleration_mm_s2: f64,
    lift_lower_cycle_s: f64,
    pen_change_s: f64,
    minimum_vector_s: f64,
    buffer_bytes: f64,
}
fn model() -> &'static Model {
    static MODEL: OnceLock<Model> = OnceLock::new();
    MODEL.get_or_init(|| {
        serde_json::from_str(include_str!("../models/hp7585-snoqualmie-v1.json"))
            .expect("embedded timing model")
    })
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Estimate {
    pub model: String,
    pub prepared_sha256: String,
    pub feed_seconds: f64,
    pub motion_seconds: f64,
    pub speed_extrapolation: bool,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Remaining {
    pub seconds: f64,
    pub updated_at_ms: u64,
}
pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
#[derive(Debug, Serialize)]
pub struct Forecast {
    pub estimate: Estimate,
    pub boundaries: Vec<Boundary>,
}
#[derive(Debug, Serialize)]
pub struct Boundary {
    pub byte: usize,
    pub feed_seconds: f64,
}
impl Forecast {
    pub fn remaining(&self, sent: usize) -> f64 {
        let index = self.boundaries.partition_point(|b| b.byte <= sent);
        let elapsed = index
            .checked_sub(1)
            .map_or(0., |i| self.boundaries[i].feed_seconds);
        (self.estimate.motion_seconds - elapsed).max(0.)
    }
}

struct Move {
    index: usize,
    length: f64,
    down: bool,
    speed: f64,
    direction: [f64; 2],
}

pub fn forecast(source: &str) -> Result<Forecast> {
    let m = model();
    let p = &m.parameters;
    let (mut ends, mut costs, mut moves) = (Vec::new(), Vec::new(), Vec::<Move>::new());
    let (mut position, mut down, mut slot, mut speed) = (None::<[f64; 2]>, false, 0u8, 50.);
    let (mut offset, mut extrapolation) = (0, false);
    for (i, line) in source.split_inclusive('\n').enumerate() {
        ensure!(
            line.is_ascii() && line.len() >= 4 && line.len() <= 80 && line.ends_with(";\n"),
            "Timing requires normalized prepared HPGL"
        );
        let (op, arg) = (&line[..2], &line[2..line.len() - 2]);
        let mut cost = 0.;
        match op {
            "IN" => ensure!(
                (i == 0 || (i == 1 && source.starts_with("PU;\nIN;\n"))) && arg.is_empty(),
                "Unexpected timing reset"
            ),
            "PA" => ensure!(arg.is_empty(), "Normalize coordinate commands first"),
            "FS" => {
                let _: u8 = arg.parse()?;
            }
            "VS" => {
                speed = arg.parse::<f64>()? * 10.;
                ensure!(speed > 0. && speed <= 1000., "Invalid speed");
            }
            "SP" => {
                let next: u8 = arg.parse()?;
                ensure!(next <= 8, "Invalid slot");
                if next != slot {
                    cost += p.pen_change_s;
                }
                slot = next;
            }
            "PU" | "PD" => {
                let next = op == "PD";
                if next != down {
                    cost += p.lift_lower_cycle_s / 2.;
                }
                down = next;
                if !arg.is_empty() {
                    let (x, y) = arg
                        .split_once(',')
                        .ok_or_else(|| anyhow::anyhow!("Invalid move"))?;
                    let q = [x.parse::<f64>()? / 40., y.parse::<f64>()? / 40.];
                    ensure!(q.iter().all(|v| v.is_finite()), "Nonfinite move");
                    let delta = position.map_or([0., 0.], |v| [q[0] - v[0], q[1] - v[1]]);
                    let length = delta[0].hypot(delta[1]);
                    moves.push(Move {
                        index: i,
                        length,
                        down,
                        speed,
                        direction: if length > 0. {
                            [delta[0] / length, delta[1] / length]
                        } else {
                            [0., 0.]
                        },
                    });
                    position = Some(q);
                    extrapolation |= down && speed != 50.;
                }
            }
            _ => anyhow::bail!("Unsupported timing instruction {op}"),
        }
        offset += line.len();
        ends.push(offset);
        costs.push(cost);
    }
    ensure!(!moves.is_empty(), "Empty drawing");
    // Same forward/backward velocity envelope as the offline fitter. The
    // adopted acceleration is its no-correction limit, not a measured g limit.
    let n = moves.len();
    let a = p.acceleration_mm_s2;
    let mut velocity = vec![0.; n + 1];
    let vmax: Vec<_> = moves
        .iter()
        .map(|v| {
            if v.down {
                v.speed * p.draw_speed_scale
            } else {
                p.pen_up_mm_s
            }
        })
        .collect();
    for k in 1..n {
        let (v, u) = (&moves[k], &moves[k - 1]);
        if v.down && u.down && v.index == u.index + 1 {
            let dot = v.direction[0] * u.direction[0] + v.direction[1] * u.direction[1];
            let sine = ((1. - dot) / 2.).max(0.).sqrt();
            velocity[k] = (a * v.length.min(u.length) / (2. * sine.max(1e-12))).sqrt();
        }
    }
    for k in 0..n {
        velocity[k] = velocity[k].min(vmax[k]);
        velocity[k + 1] = velocity[k + 1].min(vmax[k]);
    }
    for k in 0..n {
        velocity[k + 1] =
            velocity[k + 1].min((velocity[k].powi(2) + 2. * a * moves[k].length).sqrt());
    }
    for k in (0..n).rev() {
        velocity[k] = velocity[k].min((velocity[k + 1].powi(2) + 2. * a * moves[k].length).sqrt());
    }
    for k in 0..n {
        let (u, v) = (velocity[k], velocity[k + 1]);
        let peak = vmax[k].min((a * moves[k].length + (u * u + v * v) / 2.).sqrt());
        let ramp = (2. * peak * peak - u * u - v * v) / (2. * a);
        let duration = (2. * peak - u - v) / a + (moves[k].length - ramp).max(0.) / vmax[k];
        costs[moves[k].index] += duration.max(p.minimum_vector_s);
    }
    let (mut cursor, mut previous, mut packet_start) = (0, 0, 0);
    let (mut incoming, mut executed) = (0f64, 0f64);
    let mut finish = Vec::with_capacity(ends.len());
    let mut boundaries = Vec::new();
    for (i, end) in ends.iter().copied().enumerate() {
        let gap = if end - packet_start > 512 {
            boundaries.push(Boundary {
                byte: previous,
                feed_seconds: incoming,
            });
            packet_start = previous;
            m.host_turnaround_s
        } else {
            0.
        };
        let boundary = end as f64 - p.buffer_bytes;
        while cursor < i && (ends[cursor] as f64) < boundary {
            cursor += 1;
        }
        let freed = if cursor < i && boundary > 0. {
            finish[cursor]
        } else {
            0.
        };
        incoming = (incoming + gap + (end - previous) as f64 / m.serial_byte_rate).max(freed);
        executed = executed.max(incoming) + costs[i];
        finish.push(executed);
        previous = end;
    }
    boundaries.push(Boundary {
        byte: source.len(),
        feed_seconds: incoming,
    });
    Ok(Forecast {
        estimate: Estimate {
            model: MODEL_ID.into(),
            prepared_sha256: format!("{:x}", Sha256::digest(source.as_bytes())),
            feed_seconds: incoming,
            motion_seconds: executed,
            speed_extrapolation: extrapolation,
        },
        boundaries,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn prediction_matches_offline_reference_and_remaining_follows_bytes() {
        let source = include_str!("../samples/timing-reference.hpgl");
        let expected: serde_json::Value =
            serde_json::from_str(include_str!("../samples/timing-reference.json")).unwrap();
        let f = forecast(source).unwrap();
        assert!(
            (f.estimate.feed_seconds - expected["feed_seconds"].as_f64().unwrap()).abs() < 1e-7
        );
        assert!(
            (f.estimate.motion_seconds - expected["motion_seconds"].as_f64().unwrap()).abs() < 1e-7
        );
        assert_eq!(
            f.estimate.prepared_sha256,
            expected["prepared_sha256"].as_str().unwrap()
        );
        assert!(f.estimate.speed_extrapolation);
        assert_eq!(
            f.boundaries.len(),
            expected["boundaries"].as_array().unwrap().len()
        );
        for (b, e) in f
            .boundaries
            .iter()
            .zip(expected["boundaries"].as_array().unwrap())
        {
            assert_eq!(b.byte, e[0].as_u64().unwrap() as usize);
            assert!((b.feed_seconds - e[1].as_f64().unwrap()).abs() < 1e-7);
            assert!(f.remaining(b.byte) >= 0.);
        }
        assert_eq!(f.remaining(0), f.estimate.motion_seconds);
        assert!(f.remaining(source.len()) < f.remaining(0));
        assert!(forecast("IN;PA;PU0,0;PD2,3;").is_err());
        assert!(forecast("IN;\nPR;\nPU0,0;\n").is_err());
    }
}
