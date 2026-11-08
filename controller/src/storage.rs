//! Lossless archives keep the public filenames and decoded bytes unchanged.
use std::{
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
};

pub fn archive(path: &Path) -> PathBuf {
    let mut name = path.as_os_str().to_owned();
    name.push(".gz");
    name.into()
}

pub fn exists(path: &Path) -> bool {
    path.is_file() || archive(path).is_file()
}

pub fn read(path: impl AsRef<Path>) -> io::Result<Vec<u8>> {
    let path = path.as_ref();
    if path.extension().is_some_and(|s| s == "gz") {
        return decode(path);
    }
    match fs::read(path) {
        Ok(bytes) => Ok(bytes),
        Err(e) if e.kind() == io::ErrorKind::NotFound => decode(&archive(path)),
        Err(e) => Err(e),
    }
}

fn decode(path: &Path) -> io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    flate2::read::MultiGzDecoder::new(fs::File::open(path)?).read_to_end(&mut bytes)?;
    Ok(bytes)
}

pub fn read_to_string(path: impl AsRef<Path>) -> io::Result<String> {
    String::from_utf8(read(path)?).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
}

/// Cache access is advisory; its failure must not prevent reading retained data.
pub fn touch(directory: &Path) {
    let _ = fs::write(directory.join(".last-access"), b"");
}

/// One sweep at a time, off the HTTP/serial tasks. A fresh checkout carries the
/// policy and collector; no external cron configuration is needed.
pub fn start(root: PathBuf, project: PathBuf) {
    tokio::spawn(async move {
        loop {
            let root = root.clone();
            let script = project.join("controller/scripts/retention.py");
            let result = tokio::task::spawn_blocking(move || {
                std::process::Command::new("python3")
                    .arg(script)
                    .arg("--state")
                    .arg(root)
                    .arg("--apply")
                    .output()
            })
            .await;
            match result {
                Ok(Ok(out)) if out.status.success() => {
                    if !out.stdout.is_empty() {
                        eprintln!("Storage: {}", String::from_utf8_lossy(&out.stdout).trim());
                    }
                }
                other => eprintln!("Storage cleanup failed: {other:?}"),
            }
            tokio::time::sleep(std::time::Duration::from_secs(60)).await;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    #[test]
    fn archive_reads_preserve_bytes_and_detect_corruption() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("source.hpgl");
        let data = b"IN;\nSP2;\nPU12,34;\nPD56,78;\n";
        let mut out = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        out.write_all(data).unwrap();
        let mut gz = out.finish().unwrap();
        fs::write(archive(&path), &gz).unwrap();
        assert!(exists(&path));
        assert_eq!(read(&path).unwrap(), data);
        assert_eq!(read(archive(&path)).unwrap(), data);
        gz[15] ^= 127;
        fs::write(archive(&path), gz).unwrap();
        assert!(read(&path).is_err());
        fs::write(&path, data).unwrap();
        assert_eq!(read(&path).unwrap(), data); // An interrupted archive never hides the original.
    }
}
