//! A shared admission lock lets an installer close the check/restart race.
use std::{fs::File, io, time::Duration};

pub fn lease() -> io::Result<Option<File>> {
    let Some(path) = std::env::var_os("PLOTLINE_ADMISSION_LOCK") else {
        return Ok(None);
    };
    let file = File::open(path)?;
    file.try_lock_shared().map_err(|error| match error {
        std::fs::TryLockError::WouldBlock => io::Error::from(io::ErrorKind::WouldBlock),
        std::fs::TryLockError::Error(error) => error,
    })?;
    Ok(Some(file))
}

pub async fn wait() -> io::Result<Option<File>> {
    loop {
        match lease() {
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                tokio::time::sleep(Duration::from_millis(100)).await
            }
            result => return result,
        }
    }
}

pub fn workers(variable: &str, default: usize) -> usize {
    std::env::var(variable)
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
        .clamp(1, 4)
}
