//! Byte ranges of the actual prepared pen runs. No device access or reordering.
use serde::Serialize;
use serde_json::Value;

#[derive(Debug, Serialize)]
pub struct Section {
    pub slot: u8,
    pub color: String,
    pub label: String,
    pub start_byte: usize,
    pub end_byte: usize,
}
#[derive(Debug, Serialize)]
pub struct Progress {
    pub total_bytes: usize,
    pub sections: Vec<Section>,
}

pub fn describe(commands: &str, inks: &[Value]) -> Progress {
    let mut sections = Vec::new();
    let (mut slot, mut start, mut offset) = (0, 0, 0);
    let mut append = |slot, start_byte, end_byte| {
        if start_byte == end_byte {
            return;
        }
        let ink = inks
            .iter()
            .find(|ink| ink["slot"].as_u64() == Some(slot as u64));
        // Old records may lack a physical color; use neutral rather than infer
        // installed ink from the source's requested color.
        let color = ink
            .and_then(|ink| ink["loaded"]["color"].as_str())
            .unwrap_or("#aab6b2")
            .to_owned();
        let name = ink
            .and_then(|ink| ink["loaded"]["label"].as_str())
            .filter(|s| !s.is_empty());
        let label = if slot == 0 {
            if start_byte == 0 { "Setup" } else { "Park pen" }.to_owned()
        } else {
            name.map_or_else(
                || format!("Slot {slot}"),
                |name| format!("Slot {slot} · {name}"),
            )
        };
        sections.push(Section {
            slot,
            color,
            label,
            start_byte,
            end_byte,
        });
    };
    for line in commands.split_inclusive('\n') {
        if let Some(next) = line
            .trim()
            .strip_prefix("SP")
            .and_then(|s| s.strip_suffix(';'))
            .and_then(|s| s.parse::<u8>().ok())
            && next != slot
        {
            append(slot, start, offset);
            slot = next;
            start = offset;
        }
        offset += line.len();
    }
    append(slot, start, offset);
    Progress {
        total_bytes: commands.len(),
        sections,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn byte_ranges_preserve_repeated_runs_and_physical_colors() {
        let commands = "IN;\nPA;\nSP6;\nVS5;\nFS4;\nPU0,0;\nPD40,40;\nPU;\nSP2;\nPD0,0;\nPU;\nSP6;\nPD40,40;\nPU;\nSP0;\n";
        let inks = vec![
            json!({"slot":6,"color":"#000000","loaded":{"color":"#800080","label":"purple"}}),
            json!({"slot":2,"loaded":{"color":"#ff0000","label":"red"}}),
        ];
        let progress = describe(commands, &inks);
        assert_eq!(progress.total_bytes, commands.len());
        assert_eq!(
            progress.sections.iter().map(|s| s.slot).collect::<Vec<_>>(),
            vec![0, 6, 2, 6, 0]
        );
        assert_eq!(progress.sections[1].color, "#800080");
        assert_eq!(progress.sections[1].label, "Slot 6 · purple");
        assert_eq!(
            progress.sections[2].start_byte,
            commands.find("SP2;").unwrap()
        );
        assert_eq!(progress.sections.first().unwrap().start_byte, 0);
        assert_eq!(progress.sections.last().unwrap().end_byte, commands.len());
        for pair in progress.sections.windows(2) {
            assert_eq!(pair[0].end_byte, pair[1].start_byte);
        }
        assert!(describe("", &[]).sections.is_empty());
        assert_eq!(describe("SP1;\nPU;\n", &[]).sections[0].color, "#aab6b2");
    }
}
