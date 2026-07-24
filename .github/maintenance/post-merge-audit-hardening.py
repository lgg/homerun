from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Recover the last complete state file if Windows stopped between moving the old
# file to its backup and installing the fully-synced replacement.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''    pub async fn load_from_disk(&self) -> Result<Vec<String>> {
        let path = self.config.runners_json_path();
        if !path.exists() {
            return Ok(Vec::new());
        }
        let json = std::fs::read_to_string(&path)?;''',
    '''    pub async fn load_from_disk(&self) -> Result<Vec<String>> {
        let path = self.config.runners_json_path();
        #[cfg(windows)]
        if !path.exists() {
            let backup_path = path.with_extension("json.bak");
            if backup_path.exists() {
                tracing::warn!(
                    "Recovering runner state from interrupted Windows swap: {}",
                    backup_path.display()
                );
                std::fs::rename(&backup_path, &path)
                    .context("restoring backed-up runner state")?;
            }
        }
        if !path.exists() {
            return Ok(Vec::new());
        }
        let json = std::fs::read_to_string(&path)?;''',
)

# HashMap::insert replaces before returning the old value. Check first so a
# defensive collision cannot discard the existing process handle.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''            let mut processes = self.processes.write().await;
            if processes.insert(id.to_string(), handle).is_some() {
                bail!("Runner '{id}' already has an active process");
            }
            runner.state = RunnerState::Online;''',
    '''            let mut processes = self.processes.write().await;
            if processes.contains_key(id) {
                bail!("Runner '{id}' already has an active process");
            }
            processes.insert(id.to_string(), handle);
            runner.state = RunnerState::Online;''',
)

# Once the child/container is alive and published, persistence failure must not
# return before the monitor owns it. Keep the in-memory lifecycle healthy and
# report the disk error; the next state transition will retry persistence.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''        self.emit_state_event(id, "online");
        self.save_to_disk().await?;

        // 5c. Spawn log reader tasks''',
    '''        self.emit_state_event(id, "online");
        if let Err(error) = self.save_to_disk().await {
            tracing::error!(
                runner = %id,
                error = %error,
                "Runner is online, but its state could not be persisted"
            );
        }

        // 5c. Spawn log reader tasks''',
)

print("Final post-merge lifecycle hardening applied")
