use std::{env, fs, path::PathBuf, process::Command};

fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap())
        .parent()
        .unwrap()
        .to_owned();
    for file in ["tools/build-pattern-thumbnails.mjs", "package-lock.json"] {
        println!("cargo:rerun-if-changed={}", root.join(file).display());
    }
    // Include new generator modules as well as edits to existing ones.
    println!(
        "cargo:rerun-if-changed={}",
        root.join("patterns/web").display()
    );
    for entry in fs::read_dir(root.join("patterns/web")).unwrap() {
        let path = entry.unwrap().path();
        if matches!(
            path.extension().and_then(|e| e.to_str()),
            Some("js" | "json")
        ) {
            println!("cargo:rerun-if-changed={}", path.display());
        }
    }
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("pattern-thumbnails");
    let status = Command::new("node")
        .arg(root.join("tools/build-pattern-thumbnails.mjs"))
        .arg(&out)
        .current_dir(&root)
        .status()
        .expect("Pattern thumbnails need Node 20+ and the root npm dependencies; see docs/GETTING_STARTED.md");
    assert!(
        status.success(),
        "Pattern thumbnail build failed; run npm ci in the checkout root and retry"
    );
}
