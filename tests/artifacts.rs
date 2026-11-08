use std::{fs, process::Command};

#[test]
fn preview_keeps_verification_and_full_remains_the_cli_default() {
    let root = std::env::temp_dir().join(format!("plotline-artifacts-{}", std::process::id()));
    fs::create_dir_all(&root).unwrap();
    let input = root.join("input.json");
    fs::write(&input, include_str!("fixtures/house-width-corner.json")).unwrap();
    for mode in ["full", "preview"] {
        let output = root.join(mode);
        let mut command = Command::new(env!("CARGO_BIN_EXE_plotline"));
        command.arg(&input).arg(&output);
        if mode == "preview" {
            command.args(["--artifacts", mode]);
        }
        let result = command.output().unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let summary: serde_json::Value =
            serde_json::from_slice(&fs::read(output.join("summary.json")).unwrap()).unwrap();
        assert!(
            summary["verification"]["decoded_hpgl"]["paints_checked"]
                .as_u64()
                .unwrap()
                > 0
        );
        assert_eq!(output.join("report.json").exists(), mode == "full");
    }
    for name in ["plot.hpgl", "coverage.svg", "hpgl.svg"] {
        assert_eq!(
            fs::read(root.join("full").join(name)).unwrap(),
            fs::read(root.join("preview").join(name)).unwrap()
        );
    }
    // Preview mode must still reject invalid clipping, before creating artifacts.
    let mut invalid: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/house-width-corner.json")).unwrap();
    invalid["events"]
        .as_array_mut()
        .unwrap()
        .insert(0, serde_json::json!({"kind":"pop_clip"}));
    fs::write(&input, serde_json::to_vec(&invalid).unwrap()).unwrap();
    let output = root.join("invalid");
    let result = Command::new(env!("CARGO_BIN_EXE_plotline"))
        .arg(&input)
        .arg(&output)
        .args(["--artifacts", "preview"])
        .output()
        .unwrap();
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("Clip stack underflow"));
    assert!(!output.exists());
    fs::remove_dir_all(root).unwrap();
}
