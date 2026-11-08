//! Named operator recipes reference retained HPGL; saving never touches hardware.
use crate::{layout::Setup, store::Store};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::Mutex,
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;
static SAVING: Mutex<()> = Mutex::new(());
#[derive(Debug)]
pub struct Conflict;
impl std::fmt::Display for Conflict {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "This document was saved elsewhere. Reopen it or use Save As to preserve your changes."
        )
    }
}
impl std::error::Error for Conflict {}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Recipe {
    pub id: String,
    pub job_id: String,
    pub name: String,
    pub revision: u64,
    pub created_at: u64,
    pub updated_at: u64,
    pub setup: Setup,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Save {
    pub id: Option<String>,
    pub expected_revision: Option<u64>,
    pub name: String,
    pub setup: Setup,
}
fn root(store: &Store, id: &str) -> Result<PathBuf> {
    store.get(id)?;
    Ok(store.path(id)?.join("recipes"))
}
fn path(store: &Store, id: &str, key: &str) -> Result<PathBuf> {
    ensure!(
        Uuid::parse_str(key).is_ok_and(|u| u.to_string() == key),
        "Invalid recipe ID"
    );
    Ok(root(store, id)?.join(key))
}
pub fn list(store: &Store, id: &str) -> Result<Vec<Recipe>> {
    let root = root(store, id)?;
    let mut rows = vec![];
    if root.exists() {
        for entry in fs::read_dir(root)? {
            let file = entry?.path().join("record.json");
            if file.is_file() {
                rows.push(serde_json::from_slice::<Recipe>(&fs::read(file)?)?);
            }
        }
    }
    rows.sort_by(|a, b| b.updated_at.cmp(&a.updated_at).then(a.id.cmp(&b.id)));
    Ok(rows)
}
pub fn get(store: &Store, id: &str, key: &str) -> Result<Recipe> {
    Ok(serde_json::from_slice(&fs::read(
        path(store, id, key)?.join("record.json"),
    )?)?)
}
fn write(path: &Path, value: &Recipe) -> Result<()> {
    let temp = path.with_file_name(format!(".recipe-{}", Uuid::new_v4()));
    let result = (|| {
        let mut file = fs::File::create(&temp)?;
        file.write_all(&serde_json::to_vec_pretty(value)?)?;
        file.sync_all()?;
        fs::rename(&temp, path)?;
        fs::File::open(path.parent().unwrap())?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}
pub fn save(store: &Store, id: &str, request: Save) -> Result<Recipe> {
    let _guard = SAVING.lock().unwrap();
    request.setup.validate(&store.get(id)?)?;
    let mut name = request.name.trim().to_owned();
    ensure!(
        !name.is_empty() && name.chars().count() <= 160 && !name.chars().any(char::is_control),
        "Document name must contain 1–160 printable characters"
    );
    let key = request
        .id
        .clone()
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    let previous = if request.id.is_some() {
        Some(get(store, id, &key)?)
    } else {
        None
    };
    if let Some(prior) = &previous {
        if request.expected_revision != Some(prior.revision) {
            return Err(Conflict.into());
        }
    } else {
        let names: Vec<_> = list(store, id)?
            .iter()
            .map(|r| r.name.to_lowercase())
            .collect();
        let base = name.clone();
        let mut n = 2;
        while names.contains(&name.to_lowercase()) {
            name = format!("{} ({n})", base.chars().take(150).collect::<String>());
            n += 1;
        }
    }
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let row = Recipe {
        id: key.clone(),
        job_id: id.into(),
        name,
        revision: previous.as_ref().map_or(1, |r| r.revision + 1),
        created_at: previous.as_ref().map_or(now, |r| r.created_at),
        updated_at: now,
        setup: request.setup,
    };
    let dir = path(store, id, &key)?;
    fs::create_dir_all(&dir)?;
    write(&dir.join(format!("{:08}.json", row.revision)), &row)?;
    write(&dir.join("record.json"), &row)?;
    Ok(row)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn saved_copies_share_source_and_reject_conflicts_and_invalid_edits() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(temp.path().into()).unwrap();
        let job = store
            .import("Map.hpgl", b"IN;SP1;PU0,0;PD400,400;PU;", 40.)
            .unwrap();
        let original = store.setup(&job.id).unwrap();
        let request = |id, rev, setup| Save {
            id,
            expected_revision: rev,
            name: "Map".into(),
            setup,
        };
        let first = save(&store, &job.id, request(None, None, original.clone())).unwrap();
        let mut edited = original.clone();
        edited.rotation = 90;
        let second = save(
            &store,
            &job.id,
            request(Some(first.id.clone()), Some(1), edited.clone()),
        )
        .unwrap();
        assert_eq!(second.revision, 2);
        assert!(
            save(
                &store,
                &job.id,
                request(Some(first.id.clone()), Some(1), original.clone())
            )
            .unwrap_err()
            .is::<Conflict>()
        );
        let copy = save(&store, &job.id, request(None, None, original.clone())).unwrap();
        assert_ne!(copy.id, first.id);
        assert_eq!(copy.name, "Map (2)");
        edited.rotation = 45;
        assert!(
            save(
                &store,
                &job.id,
                request(Some(first.id.clone()), Some(2), edited)
            )
            .is_err()
        );
        assert_eq!(get(&store, &job.id, &first.id).unwrap().setup.rotation, 90);
        assert_eq!(store.setup(&job.id).unwrap().rotation, original.rotation);
        assert_eq!(store.list_all().unwrap().len(), 1);
        assert!(get(&store, &job.id, "../oops").is_err());
        let reopened = Store::open(temp.path().into()).unwrap();
        assert_eq!(list(&reopened, &job.id).unwrap().len(), 2);
        let history: Recipe = serde_json::from_slice(
            &fs::read(
                path(&store, &job.id, &first.id)
                    .unwrap()
                    .join("00000001.json"),
            )
            .unwrap(),
        )
        .unwrap();
        assert_eq!(history.setup.rotation, 0);
    }
}
