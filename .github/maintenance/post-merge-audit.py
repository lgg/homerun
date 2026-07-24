from __future__ import annotations

import re
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one literal match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_replace_once(path: str, pattern: str, replacement: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one regex match, found {count}")
    file_path.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# Runner request normalization and immutable runtime mode.
# ---------------------------------------------------------------------------
replace_once(
    "crates/daemon/src/runner/types.rs",
    "use serde::{Deserialize, Serialize};\n\nconst MAX_DISPLAY_NAME_CHARS: usize = 100;",
    "use serde::{Deserialize, Serialize};\nuse std::collections::HashSet;\n\nconst MAX_DISPLAY_NAME_CHARS: usize = 100;\nconst MAX_LABEL_CHARS: usize = 100;",
)

replace_once(
    "crates/daemon/src/runner/types.rs",
    "pub(crate) fn normalize_display_name(value: Option<String>) -> anyhow::Result<Option<String>> {",
    r'''pub(crate) fn normalize_labels(labels: Vec<String>) -> anyhow::Result<Vec<String>> {
    let mut normalized = Vec::new();
    let mut seen = HashSet::new();

    for label in labels {
        let trimmed = label.trim();
        if trimmed.is_empty() {
            continue;
        }
        if trimmed.chars().count() > MAX_LABEL_CHARS {
            anyhow::bail!("Runner labels must be at most {MAX_LABEL_CHARS} characters");
        }
        if trimmed.contains(',') || trimmed.chars().any(char::is_control) {
            anyhow::bail!("Runner labels cannot contain commas or control characters");
        }

        let key = trimmed.to_lowercase();
        if seen.insert(key) {
            normalized.push(trimmed.to_string());
        }
    }

    Ok(normalized)
}

pub(crate) fn normalize_display_name(value: Option<String>) -> anyhow::Result<Option<String>> {''',
)

replace_once(
    "crates/daemon/src/runner/types.rs",
    "    #[test]\n    fn test_update_request_distinguishes_omitted_clear_and_set() {",
    r'''    #[test]
    fn test_normalize_labels_trims_deduplicates_and_rejects_unsafe_values() {
        let labels = normalize_labels(vec![
            " rust ".to_string(),
            "RUST".to_string(),
            "docker".to_string(),
            "".to_string(),
        ])
        .unwrap();
        assert_eq!(labels, vec!["rust", "docker"]);
        assert!(normalize_labels(vec!["bad,label".to_string()]).is_err());
        assert!(normalize_labels(vec!["bad\nlabel".to_string()]).is_err());
    }

    #[test]
    fn test_update_request_distinguishes_omitted_clear_and_set() {''',
)

# ---------------------------------------------------------------------------
# Runner manager: atomic persistence, process publication and lifecycle intent.
# ---------------------------------------------------------------------------
replace_once(
    "crates/daemon/src/runner/mod.rs",
    "use std::collections::{HashMap, HashSet, VecDeque};\nuse std::pin::Pin;",
    "use std::collections::{HashMap, HashSet, VecDeque};\nuse std::io::Write;\nuse std::pin::Pin;",
)

regex_replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''    /// Save all runner configs to disk as JSON\.\n    pub async fn save_to_disk\(&self\) -> Result<\(\)> \{.*?\n    \}\n\n    /// Load runner configs from disk\.''',
    r'''    /// Save all runner configs to disk as JSON.
    pub async fn save_to_disk(&self) -> Result<()> {
        let _persistence_guard = self.persistence_lock.lock().await;
        let persisted = {
            let desired_running = self.desired_running.read().await;
            let runners = self.runners.read().await;
            runners
                .values()
                .map(|runner| PersistedRunner {
                    config: runner.config.clone(),
                    was_running: desired_running.contains(&runner.config.id),
                })
                .collect::<Vec<_>>()
        };
        let json = serde_json::to_string_pretty(&persisted)?;
        let path = self.config.runners_json_path();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        // Never truncate the live state file in place. A daemon crash or power
        // loss during a direct write otherwise leaves invalid JSON and prevents
        // every runner from loading on the next launch.
        let temp_path = path.with_extension(format!("json.tmp-{}", std::process::id()));
        let write_result = (|| -> Result<()> {
            let mut file = std::fs::OpenOptions::new()
                .create(true)
                .truncate(true)
                .write(true)
                .open(&temp_path)
                .with_context(|| format!("creating temporary runner state {}", temp_path.display()))?;
            file.write_all(json.as_bytes())
                .context("writing temporary runner state")?;
            file.sync_all().context("syncing temporary runner state")?;
            Ok(())
        })();
        if let Err(error) = write_result {
            let _ = std::fs::remove_file(&temp_path);
            return Err(error);
        }

        #[cfg(not(windows))]
        std::fs::rename(&temp_path, &path)
            .with_context(|| format!("replacing runner state {}", path.display()))?;

        #[cfg(windows)]
        {
            // Windows rename does not replace an existing destination. Keep a
            // recoverable backup while swapping the fully-synced temporary file.
            let backup_path = path.with_extension("json.bak");
            let _ = std::fs::remove_file(&backup_path);
            if path.exists() {
                std::fs::rename(&path, &backup_path)
                    .context("backing up previous runner state")?;
            }
            if let Err(error) = std::fs::rename(&temp_path, &path) {
                if backup_path.exists() {
                    let _ = std::fs::rename(&backup_path, &path);
                }
                return Err(error).context("installing new runner state");
            }
            let _ = std::fs::remove_file(&backup_path);
        }

        Ok(())
    }

    /// Load runner configs from disk.''',
)

regex_replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''    /// Spawn a background task that monitors an orphaned runner process by PID\..*?\n    \}\n\n    /// Tail the latest Runner_\*\.log in the _diag directory''',
    r'''    /// Spawn a background task that monitors an orphaned runner process by PID.
    /// The process handle is installed before this method returns, so API calls
    /// can never observe an Online reattached runner without being able to stop it.
    pub async fn monitor_orphaned_process(&self, runner_id: &str, pid: u32) {
        let manager = self.clone();
        let rid = runner_id.to_string();
        let kill_signal = Arc::new(Notify::new());
        let (exit_tx, exit_rx) = watch::channel(false);
        let handle = ProcessHandle {
            kill_signal: kill_signal.clone(),
            exited: exit_rx,
        };
        let work_dir = self
            .runners
            .read()
            .await
            .get(&rid)
            .map(|runner| runner.config.work_dir.clone());

        self.processes.write().await.insert(rid.clone(), handle);

        tokio::spawn(async move {
            if let Some(ref work_dir) = work_dir {
                let diag_dir = work_dir.join("_diag");
                let watcher_manager = manager.clone();
                let watcher_id = rid.clone();
                let step_watcher = manager.step_watcher.clone();
                let watcher_work_dir = work_dir.clone();
                tokio::spawn(async move {
                    Self::tail_diag_logs(
                        watcher_manager,
                        &watcher_id,
                        &diag_dir,
                        &watcher_work_dir,
                        step_watcher,
                    )
                    .await;
                });
            }

            loop {
                tokio::select! {
                    _ = kill_signal.notified() => {
                        #[cfg(unix)]
                        {
                            let pgid = pid as i32;
                            unsafe { libc::kill(-pgid, libc::SIGTERM); }
                            tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                            unsafe { libc::kill(-pgid, libc::SIGKILL); }
                        }
                        #[cfg(windows)]
                        {
                            let _ = std::process::Command::new("taskkill")
                                .args(["/T", "/F", "/PID", &pid.to_string()])
                                .stdout(std::process::Stdio::null())
                                .stderr(std::process::Stdio::null())
                                .status();
                        }
                        break;
                    }
                    _ = tokio::time::sleep(std::time::Duration::from_secs(2)) => {
                        #[cfg(unix)]
                        let alive = {
                            let result = unsafe { libc::kill(pid as i32, 0) };
                            result == 0
                                || std::io::Error::last_os_error().raw_os_error()
                                    == Some(libc::EPERM)
                        };
                        #[cfg(windows)]
                        let alive = {
                            use sysinfo::{Pid, ProcessesToUpdate, ProcessRefreshKind, System};
                            let mut sys = System::new();
                            sys.refresh_processes_specifics(
                                ProcessesToUpdate::Some(&[Pid::from_u32(pid)]),
                                true,
                                ProcessRefreshKind::nothing(),
                            );
                            sys.process(Pid::from_u32(pid)).is_some()
                        };
                        if !alive {
                            break;
                        }
                    }
                }
            }

            let (exists, unexpected) = {
                let mut runners = manager.runners.write().await;
                if let Some(runner) = runners.get_mut(&rid) {
                    let unexpected =
                        runner.state == RunnerState::Online || runner.state == RunnerState::Busy;
                    if runner.state != RunnerState::Deleting {
                        runner.state = RunnerState::Offline;
                        runner.pid = None;
                        runner.container_id = None;
                        runner.started_at = None;
                        runner.current_job = None;
                        runner.job_context = None;
                        runner.job_started_at = None;
                        runner.error_message = unexpected.then(|| {
                            "Runner process exited unexpectedly; recovery is scheduled".to_string()
                        });
                    }
                    (true, unexpected)
                } else {
                    (false, false)
                }
            };
            manager.processes.write().await.remove(&rid);
            if exists {
                manager.emit_state_event(&rid, "offline");
            }
            if unexpected && manager.is_desired_running(&rid).await {
                manager.schedule_recovery(rid.clone());
            } else {
                let _ = manager.save_to_disk().await;
            }
            let _ = exit_tx.send(true);
        });
    }

    /// Tail the latest Runner_*.log in the _diag directory''',
)

# Remove the non-atomic duplicate-name check. The check is moved into the same
# write-locked critical section as insertion below.
regex_replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''\n        // Runner names must be globally unique\..*?\n        \}\n\n        let work_dir =''',
    "\n        let work_dir =",
)

replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''        let resolved_labels = match labels {
            Some(user_labels) if !user_labels.is_empty() => user_labels,
            _ => platform_defaults,
        };''',
    r'''        let resolved_labels = match labels {
            Some(user_labels) => {
                let normalized = types::normalize_labels(user_labels)?;
                if normalized.is_empty() {
                    platform_defaults
                } else {
                    normalized
                }
            }
            None => platform_defaults,
        };''',
)

replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''        self.runners.write().await.insert(id, runner.clone());
        self.save_to_disk().await?;
        Ok(runner)''',
    r'''        {
            let mut runners = self.runners.write().await;
            if runners
                .values()
                .any(|existing| existing.config.name.eq_ignore_ascii_case(&runner.config.name))
            {
                drop(runners);
                let _ = std::fs::remove_dir_all(&runner.config.work_dir);
                bail!("A runner named '{}' already exists", runner.config.name);
            }
            runners.insert(id.clone(), runner.clone());
        }

        if let Err(error) = self.save_to_disk().await {
            self.runners.write().await.remove(&id);
            let _ = std::fs::remove_dir_all(&runner.config.work_dir);
            return Err(error).context("persisting newly-created runner");
        }
        Ok(runner)''',
)

regex_replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''    pub async fn update\(&self, id: &str, req: types::UpdateRunnerRequest\) -> Result<RunnerInfo> \{.*?\n    \}\n\n    pub async fn update_state''',
    r'''    pub async fn update(&self, id: &str, req: types::UpdateRunnerRequest) -> Result<RunnerInfo> {
        let normalized_labels = req
            .labels
            .map(types::normalize_labels)
            .transpose()?;
        let display_name = match req.display_name {
            Some(value) => Some(types::normalize_display_name(value)?),
            None => None,
        };
        let start_in_progress = self.starting.read().await.contains(id);

        let previous = {
            let mut runners = self.runners.write().await;
            let runner = runners
                .get_mut(id)
                .ok_or_else(|| anyhow::anyhow!("Runner not found"))?;

            if let Some(requested_mode) = req.mode {
                if requested_mode != runner.config.mode {
                    bail!(
                        "Runner mode cannot be changed after creation; create a new runner instead"
                    );
                }
            }
            if normalized_labels.is_some()
                && (start_in_progress
                    || !matches!(
                        runner.state,
                        RunnerState::Creating | RunnerState::Offline | RunnerState::Error
                    ))
            {
                bail!("Runner labels can only be changed while the runner is stopped");
            }

            let previous = runner.clone();
            if let Some(labels) = normalized_labels {
                runner.config.labels = labels;
            }
            if let Some(display_name) = display_name {
                runner.config.display_name = display_name;
            }
            previous
        };

        if let Err(error) = self.save_to_disk().await {
            self.runners
                .write()
                .await
                .insert(id.to_string(), previous);
            return Err(error).context("persisting runner update");
        }
        self.get(id)
            .await
            .ok_or_else(|| anyhow::anyhow!("Runner not found"))
    }

    pub async fn update_state''',
)

# Publish the process handle and Online state together. This removes the window
# in which API callers observed Online but has_active_process() returned false.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''        // 6. Store PID/container id, update state to Online, record start time
        let started_at = chrono::Utc::now();
        {
            let mut runners = self.runners.write().await;
            if let Some(r) = runners.get_mut(id) {
                r.state = RunnerState::Online;
                r.pid = pid;
                r.container_id = container_id;
                r.started_at = Some(started_at);
                r.current_job = None;
                r.job_context = None;
                r.job_started_at = None;
                r.error_message = None;
            }
        }
        self.emit_state_event(id, "online");
        let _ = self.save_to_disk().await;

        // 5c. Spawn log reader tasks''',
    r'''        // Publish the process handle and Online state in one critical section.
        // Stop/restart/delete can now safely act as soon as Online is observable.
        let kill_signal = Arc::new(Notify::new());
        let (exit_tx, exit_rx) = watch::channel(false);
        let handle = ProcessHandle {
            kill_signal: kill_signal.clone(),
            exited: exit_rx,
        };
        let started_at = chrono::Utc::now();
        {
            let mut runners = self.runners.write().await;
            let runner = runners
                .get_mut(id)
                .ok_or_else(|| anyhow::anyhow!("Runner not found while publishing process"))?;
            if runner.state != RunnerState::Registering {
                bail!(
                    "Runner '{id}' changed to {:?} before its process could be published",
                    runner.state
                );
            }
            let mut processes = self.processes.write().await;
            if processes.insert(id.to_string(), handle).is_some() {
                bail!("Runner '{id}' already has an active process");
            }
            runner.state = RunnerState::Online;
            runner.pid = pid;
            runner.container_id = container_id;
            runner.started_at = Some(started_at);
            runner.current_job = None;
            runner.job_context = None;
            runner.job_started_at = None;
            runner.error_message = None;
        }
        self.emit_state_event(id, "online");
        self.save_to_disk().await?;

        // 5c. Spawn log reader tasks''',
)

replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''        // Store process handle with kill signal + exit watch
        let kill_signal = Arc::new(Notify::new());
        let (exit_tx, exit_rx) = watch::channel(false);

        let handle = ProcessHandle {
            kill_signal: kill_signal.clone(),
            exited: exit_rx,
        };
        self.processes.write().await.insert(id.to_string(), handle);

        // 7. Spawn background monitor task — owns `running` exclusively''',
    r'''        // Spawn background monitor task — owns `running` exclusively.''',
)

regex_replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''    async fn set_desired_running\(&self, runner_id: &str, desired: bool\) -> Result<\(\)> \{.*?\n    \}\n\n    async fn is_desired_running''',
    r'''    async fn set_desired_running(&self, runner_id: &str, desired: bool) -> Result<()> {
        if desired && !self.runners.read().await.contains_key(runner_id) {
            bail!("Runner not found");
        }

        let previous = {
            let mut desired_running = self.desired_running.write().await;
            let previous = desired_running.contains(runner_id);
            if desired {
                desired_running.insert(runner_id.to_string());
            } else {
                desired_running.remove(runner_id);
            }
            previous
        };
        if let Err(error) = self.save_to_disk().await {
            let mut desired_running = self.desired_running.write().await;
            if previous {
                desired_running.insert(runner_id.to_string());
            } else {
                desired_running.remove(runner_id);
            }
            return Err(error).context("persisting desired runner state");
        }
        Ok(())
    }

    async fn is_desired_running''',
)

replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''    /// Stop a running runner process because the user explicitly requested it.
    pub async fn stop_process(&self, id: &str) -> Result<()> {''',
    r'''    async fn begin_stop(&self, id: &str, clear_desired: bool) -> Result<()> {
        // Match save_to_disk's lock ordering so a persistence snapshot cannot
        // deadlock with a lifecycle transition.
        let (previous_state, was_desired) = {
            let mut desired_running = self.desired_running.write().await;
            let mut runners = self.runners.write().await;
            let runner = runners
                .get_mut(id)
                .ok_or_else(|| anyhow::anyhow!("Runner not found"))?;
            if !runner.state.can_transition_to(&RunnerState::Stopping) {
                bail!(
                    "Invalid state transition: {:?} -> {:?}",
                    runner.state,
                    RunnerState::Stopping
                );
            }
            let previous_state = runner.state.clone();
            let was_desired = desired_running.contains(id);
            runner.state = RunnerState::Stopping;
            runner.error_message = None;
            if clear_desired {
                desired_running.remove(id);
            }
            (previous_state, was_desired)
        };

        if let Err(error) = self.save_to_disk().await {
            let mut desired_running = self.desired_running.write().await;
            let mut runners = self.runners.write().await;
            if let Some(runner) = runners.get_mut(id) {
                if runner.state == RunnerState::Stopping {
                    runner.state = previous_state;
                }
            }
            if was_desired {
                desired_running.insert(id.to_string());
            } else {
                desired_running.remove(id);
            }
            return Err(error).context("persisting stop transition");
        }

        self.emit_state_event(id, "stopping");
        Ok(())
    }

    /// Stop a running runner process because the user explicitly requested it.
    pub async fn stop_process(&self, id: &str) -> Result<()> {''',
)

replace_once(
    "crates/daemon/src/runner/mod.rs",
    r'''    async fn stop_process_internal(&self, id: &str, clear_desired: bool) -> Result<()> {
        if clear_desired {
            // Clear user intent before signalling the process, so an exit observed
            // by the monitor cannot race into automatic recovery.
            self.set_desired_running(id, false).await?;
        }

        // Transition to Stopping
        self.update_state(id, RunnerState::Stopping).await?;
        self.emit_state_event(id, "stopping");

        let handle = self.processes.read().await.get(id).cloned();''',
    r'''    async fn stop_process_internal(&self, id: &str, clear_desired: bool) -> Result<()> {
        // State and desired-running intent are changed atomically. A rejected
        // transition must never silently disable future recovery.
        self.begin_stop(id, clear_desired).await?;

        let handle = self.processes.read().await.get(id).cloned();''',
)

# Add focused regression tests before the existing state-transition test.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    "    #[tokio::test]\n    async fn test_runner_state_transitions() {",
    r'''    #[tokio::test]
    async fn test_concurrent_explicit_name_creation_is_unique() {
        let manager = create_test_manager();
        let first_manager = manager.clone();
        let second_manager = manager.clone();
        let first = tokio::spawn(async move {
            first_manager
                .create(
                    "owner/repo",
                    Some("shared-name".to_string()),
                    None,
                    None,
                    None,
                    None,
                )
                .await
        });
        let second = tokio::spawn(async move {
            second_manager
                .create(
                    "owner/repo",
                    Some("shared-name".to_string()),
                    None,
                    None,
                    None,
                    None,
                )
                .await
        });
        let results = [first.await.unwrap(), second.await.unwrap()];
        assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
        assert_eq!(manager.list().await.len(), 1);
    }

    #[tokio::test]
    async fn test_update_rejects_mode_change_and_active_label_change() {
        let manager = create_test_manager();
        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id.clone();

        let mode_result = manager
            .update(
                &id,
                types::UpdateRunnerRequest {
                    labels: None,
                    mode: Some(RunnerMode::Container),
                    display_name: None,
                },
            )
            .await;
        assert!(mode_result.is_err());
        assert_eq!(manager.get(&id).await.unwrap().config.mode, RunnerMode::App);

        manager
            .update_state(&id, RunnerState::Registering)
            .await
            .unwrap();
        manager.update_state(&id, RunnerState::Online).await.unwrap();
        let label_result = manager
            .update(
                &id,
                types::UpdateRunnerRequest {
                    labels: Some(vec!["changed".to_string()]),
                    mode: None,
                    display_name: None,
                },
            )
            .await;
        assert!(label_result.is_err());
    }

    #[tokio::test]
    async fn test_failed_stop_transition_preserves_desired_running_intent() {
        let manager = create_test_manager();
        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id.clone();
        manager.set_desired_running(&id, true).await.unwrap();

        let result = manager.stop_process(&id).await;
        assert!(result.is_err());
        assert!(manager.is_desired_running(&id).await);
        assert_eq!(manager.get(&id).await.unwrap().state, RunnerState::Creating);
    }

    #[tokio::test]
    async fn test_runner_state_transitions() {''',
)

# ---------------------------------------------------------------------------
# API validation for transitional restart states and clearer update errors.
# ---------------------------------------------------------------------------
replace_once(
    "crates/daemon/src/api/runners.rs",
    r'''    let updated = state.runner_manager.update(&id, req).await.map_err(|e| {
        let message = e.to_string();
        if message == "Runner not found" {
            (StatusCode::NOT_FOUND, message)
        } else {
            (StatusCode::INTERNAL_SERVER_ERROR, message)
        }
    })?;''',
    r'''    let updated = state.runner_manager.update(&id, req).await.map_err(|e| {
        let message = e.to_string();
        if message == "Runner not found" {
            (StatusCode::NOT_FOUND, message)
        } else if message.contains("cannot be changed")
            || message.contains("only be changed while the runner is stopped")
        {
            (StatusCode::CONFLICT, message)
        } else if message.contains("persisting runner update") {
            (StatusCode::INTERNAL_SERVER_ERROR, message)
        } else {
            (StatusCode::BAD_REQUEST, message)
        }
    })?;''',
)

replace_once(
    "crates/daemon/src/api/runners.rs",
    r'''    // Authenticate before stopping. A failed restart must not take a healthy
    // runner offline merely because credentials are unavailable.
    let token = state.auth.token().await.ok_or_else(|| {''',
    r'''    if !matches!(
        runner.state,
        crate::runner::state::RunnerState::Online
            | crate::runner::state::RunnerState::Busy
            | crate::runner::state::RunnerState::Offline
            | crate::runner::state::RunnerState::Error
    ) {
        return Err((
            StatusCode::CONFLICT,
            format!("Runner is in {:?} state, cannot restart", runner.state),
        ));
    }
    if matches!(
        runner.state,
        crate::runner::state::RunnerState::Offline | crate::runner::state::RunnerState::Error
    ) && state.runner_manager.has_active_process(&id).await
    {
        return Err((
            StatusCode::CONFLICT,
            "Runner still has an active process and must be stopped before restarting".to_string(),
        ));
    }

    // Authenticate before stopping. A failed restart must not take a healthy
    // runner offline merely because credentials are unavailable.
    let token = state.auth.token().await.ok_or_else(|| {''',
)

replace_once(
    "crates/daemon/src/api/runners.rs",
    "    #[tokio::test]\n    async fn test_create_and_list_runners() {",
    r'''    #[tokio::test]
    async fn test_restart_rejects_transitional_state_before_authentication() {
        let state = AppState::new_test();
        let runner = state
            .runner_manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!("/runners/{}/restart", runner.config.id))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::CONFLICT);
    }

    #[tokio::test]
    async fn test_create_and_list_runners() {''',
)

# Reattached process handles must be present before startup continues.
replace_once(
    "crates/daemon/src/server.rs",
    r'''                    state
                        .runner_manager
                        .monitor_orphaned_process(&runner.config.id, pid);''',
    r'''                    state
                        .runner_manager
                        .monitor_orphaned_process(&runner.config.id, pid)
                        .await;''',
)

# ---------------------------------------------------------------------------
# Wizard preselection can change while the dialog remains mounted.
# ---------------------------------------------------------------------------
replace_once(
    "apps/desktop/src/components/NewRunnerWizard.tsx",
    "  const [resolvedPreselect, setResolvedPreselect] = useState(false);",
    "  const [resolvedPreselectFor, setResolvedPreselectFor] = useState<string | null>(null);",
)

replace_once(
    "apps/desktop/src/components/NewRunnerWizard.tsx",
    r'''  useEffect(() => {
    if (!preselectedRepo || resolvedPreselect || reposLoading) return;

    const found = repos.find((r) => r.full_name === preselectedRepo) ?? null;
    setSelectedRepo(found);
    setResolvedPreselect(true);

    if (found) {
      setName(generateName(found.name));
      setStep(1);
    } else {
      // A stale/deleted repository must not leave the wizard stuck on an
      // unresolvable "Loading repository..." screen.
      setStep(0);
    }
  }, [preselectedRepo, repos, reposLoading, resolvedPreselect]);''',
    r'''  useEffect(() => {
    if (!preselectedRepo) {
      setResolvedPreselectFor(null);
      return;
    }
    if (resolvedPreselectFor === preselectedRepo || reposLoading) return;

    const found = repos.find((r) => r.full_name === preselectedRepo) ?? null;
    setSelectedRepo(found);
    setResolvedPreselectFor(preselectedRepo);

    if (found) {
      setName(generateName(found.name));
      setStep(1);
    } else {
      // A stale/deleted repository must not leave the wizard stuck on an
      // unresolvable "Loading repository..." screen.
      setStep(0);
    }
  }, [preselectedRepo, repos, reposLoading, resolvedPreselectFor]);''',
)

replace_once(
    "apps/desktop/src/components/NewRunnerWizard.test.tsx",
    "  it(\"falls back to repository selection when a preselected repository is stale\", async () => {",
    r'''  it("resolves a changed preselected repository while remaining mounted", async () => {
    const { rerender, props } = await renderWizard({ preselectedRepo: "org/frontend" });
    const firstName = (await screen.findByLabelText("Name")) as HTMLInputElement;
    await waitFor(() => expect(firstName.value).toMatch(/^frontend-runner-/));

    rerender(
      <AuthProvider>
        <NewRunnerWizard {...props} preselectedRepo="org/backend" />
      </AuthProvider>,
    );
    const changedName = (await screen.findByLabelText("Name")) as HTMLInputElement;
    await waitFor(() => expect(changedName.value).toMatch(/^backend-runner-/));
  });

  it("falls back to repository selection when a preselected repository is stale", async () => {''',
)

print("Post-merge audit transformations applied successfully")
