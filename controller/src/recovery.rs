//! Stroke boundaries in the immutable, normalized device program.
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub attempt: String,
    pub source_sha256: String,
    pub start_stroke: usize,
    pub pause_before_pen_change: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Origin {
    pub attempt: String,
    pub sha256: String,
    pub start_stroke: usize,
    pub start_byte: usize,
}

pub struct Stroke {
    pub offset: usize,
    pub pen: u8,
    pub speed: u8,
    pub force: u8,
}

pub fn index(program: &str) -> Result<Vec<Stroke>> {
    ensure!(
        program.starts_with("IN;\nPA;\n") || program.starts_with("PU;\nIN;\nPA;\n"),
        "Missing prepared plot initialization"
    );
    let (mut offset, mut pen, mut speed, mut force) = (0, 0, 0, 0);
    let mut pending = None;
    let mut drawing = false;
    let mut strokes = Vec::new();
    for line in program.split_inclusive('\n') {
        let command = line
            .strip_suffix(";\n")
            .context("Invalid prepared command framing")?;
        ensure!(
            command.len() >= 2 && line.len() <= 80,
            "Invalid prepared command"
        );
        let (op, args) = command.split_at(2);
        match op {
            "IN" | "PA" => {
                ensure!(args.is_empty(), "Invalid initialization");
                pending = None;
                drawing = false;
            }
            "SP" => {
                pen = args.parse()?;
                ensure!(pen <= 8, "Invalid prepared pen");
                pending = None;
                drawing = false;
            }
            "VS" => {
                speed = args.parse()?;
                ensure!((1..=60).contains(&speed), "Invalid speed");
            }
            "FS" => {
                force = args.parse()?;
                ensure!((1..=8).contains(&force), "Invalid force");
            }
            "PU" | "PD" => {
                if args.is_empty() {
                    ensure!(op == "PU", "Unexpected bare pen-down");
                    pending = None;
                    drawing = false;
                } else {
                    let (x, y) = args.split_once(',').context("Missing prepared position")?;
                    let _: i32 = x.parse()?;
                    let _: i32 = y.parse()?;
                    if op == "PU" {
                        pending = Some(offset);
                        drawing = false;
                    } else if let Some(start) = pending.take() {
                        ensure!(
                            pen > 0 && speed > 0 && force > 0,
                            "Missing stroke pen settings"
                        );
                        strokes.push(Stroke {
                            offset: start,
                            pen,
                            speed,
                            force,
                        });
                        drawing = true;
                    } else {
                        ensure!(drawing, "Pen-down without a start position");
                    }
                }
            }
            _ => anyhow::bail!("Unsupported prepared command {op}"),
        }
        offset += line.len();
    }
    Ok(strokes)
}

pub fn suffix(program: &str, stroke: &Stroke) -> String {
    // Settings are restored before a pen-up move to the original stroke start.
    format!(
        "PU;\nIN;\nPA;\nPU;\nSP{};\nVS{};\nFS{};\n{}",
        stroke.pen,
        stroke.speed,
        stroke.force,
        &program[stroke.offset..]
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cuts_only_at_complete_strokes_and_restores_modal_settings() {
        let p = "IN;\nPA;\nSP2;\nVS5;\nFS4;\nPU10,20;\nPD30,40;\nPD50,60;\nPU;\nPU70,80;\nPD90,100;\nPU;\nSP5;\nVS3;\nFS2;\nPU1,2;\nPD3,4;\nPU;\nSP0;\n";
        let s = index(p).unwrap();
        assert_eq!(s.len(), 3);
        for (i, stroke) in s.iter().enumerate() {
            let recovered = suffix(p, stroke);
            assert!(recovered.ends_with(&p[stroke.offset..]));
            assert_eq!(index(&recovered).unwrap().len(), 3 - i);
        }
        assert!(suffix(p, &s[1]).starts_with("PU;\nIN;\nPA;\nPU;\nSP2;\nVS5;\nFS4;\nPU70,80;"));
        assert!(suffix(p, &s[2]).contains("SP5;\nVS3;\nFS2;\nPU1,2;"));
        assert!(index("IN;\nPA;\nPR1,2;\n").is_err());
        assert!(index(&p.replace("SP0;", "PD3,4;")).is_err());
    }
}
