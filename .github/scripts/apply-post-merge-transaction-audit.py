from __future__ import annotations

import re
from pathlib import Path

RUNNER = Path("crates/daemon/src/runner/mod.rs")
API = Path("crates/daemon/src/api/runners.rs")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path}, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_count(path: Path, old: str, new: str, expected: int) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(
            f"expected {expected} matches in {path}, found {count}: {old[:120]!r}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


def regex_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"expected one regex match in {path}, found {count}: {pattern[:120]!r}")
    path.write_text(updated, encoding="utf-8")


# A persistence write lock must cover the state mutation and its rollback, not
# only the filesystem write. Otherwise another save can publish an intermediate
# value that the originating operation later rolls back only in memory.
regex_once(
    RUNNER,
    r"    /// Save all runner configs to disk as JSON\.\n    pub async fn save_to_disk\(&self\) -> Result<\(\)> \{\n        let _persistence_guard = self\.persistence_lock\.lock\(\)\.await;\n(.*?)\n    \}\n\n    /// Load runner configs from disk\.",
    r"""    /// Save all runner configs to disk as JSON.
    pub async fn save_to_disk(&self) -> Result<()> {
        let _persistence_guard = self.persistence_lock.lock().await;
        self.save_to_disk_locked().await
    }

    /// Persist a snapshot while the caller holds `persistence_lock`.
    async fn save_to_disk_locked(&self) -> Result<()> {
\1
    }

    /// Load runner configs from disk.""",
)

replace_once(
    RUNNER,
    """struct LifecycleOperations {
    starting: HashSet<String>,
    updating: HashSet<String>,
    deleting: HashSet<String>,
}""",
    """struct LifecycleOperations {
    starting: HashSet<String>,
    stopping: HashSet<String>,
    updating: HashSet<String>,
    deleting: HashSet<String>,
}""",
)

replace_once(
    RUNNER,
    """        {
            let mut runners = self.runners.write().await;
            if runners.values().any(|existing| {
                let same_name = existing
                    .config
                    .name
                    .eq_ignore_ascii_case(&runner.config.name);
                let same_repository = existing.config.repo_owner.eq_ignore_ascii_case(owner)
                    && existing.config.repo_name.eq_ignore_ascii_case(repo);
                let docker_name_collision = existing.config.mode == RunnerMode::Container
                    && runner.config.mode == RunnerMode::Container;
                same_name && (same_repository || docker_name_collision)
            }) {
                drop(runners);
                let _ = std::fs::remove_dir_all(&runner.config.work_dir);
                bail!(
                    "A conflicting runner named '{}' already exists",
                    runner.config.name
                );
            }
            runners.insert(id.clone(), runner.clone());
        }

        if let Err(error) = self.save_to_disk().await {
            self.runners.write().await.remove(&id);
            let _ = std::fs::remove_dir_all(&runner.config.work_dir);
            return Err(error).context("persisting newly-created runner");
        }
        Ok(runner)""",
    """        let _persistence_guard = self.persistence_lock.lock().await;
        {
            let mut runners = self.runners.write().await;
            if runners.values().any(|existing| {
                let same_name = existing
                    .config
                    .name
                    .eq_ignore_ascii_case(&runner.config.name);
                let same_repository = existing.config.repo_owner.eq_ignore_ascii_case(owner)
                    && existing.config.repo_name.eq_ignore_ascii_case(repo);
                let docker_name_collision = existing.config.mode == RunnerMode::Container
                    && runner.config.mode == RunnerMode::Container;
                same_name && (same_repository || docker_name_collision)
            }) {
                drop(runners);
                let _ = std::fs::remove_dir_all(&runner.config.work_dir);
                bail!(
                    "A conflicting runner named '{}' already exists",
                    runner.config.name
                );
            }
            runners.insert(id.clone(), runner.clone());
        }

        if let Err(error) = self.save_to_disk_locked().await {
            self.runners.write().await.remove(&id);
            let _ = std::fs::remove_dir_all(&runner.config.work_dir);
            return Err(error).context("persisting newly-created runner");
        }
        Ok(runner)""",
)

# Scaling down already enters a serialized delete flow; stopping first creates a
# gap in which another start can race between stop and deletion.
replace_once(
    RUNNER,
    """                let delete_result = if let Some(token) = token {
                    self.full_delete(&runner.config.id, &token).await
                } else if runner.state == RunnerState::Online
                    || self.has_active_process(&runner.config.id).await
                {
                    match self.stop_process(&runner.config.id).await {
                        Ok(()) => self.delete(&runner.config.id).await,
                        Err(error) => Err(error),
                    }
                } else {
                    self.delete(&runner.config.id).await
                };""",
    """                let delete_result = if let Some(token) = token {
                    self.full_delete(&runner.config.id, &token).await
                } else {
                    self.delete(&runner.config.id).await
                };""",
)

# Replace the operation arbitration as one unit so every mutation has a clear
# admission rule and deletion waits for both start/update and explicit stop.
regex_once(
    RUNNER,
    r"    async fn begin_start_operation\(&self, id: &str\) -> Result<\(\)> \{.*?    async fn wait_for_mutations_to_finish\(&self, id: &str\) -> Result<\(\)> \{.*?        Ok\(\(\)\)\n    \}\n",
    """    pub(crate) async fn begin_start_operation(&self, id: &str) -> Result<()> {
        let mut operations = self.lifecycle_operations.lock().await;
        if operations.deleting.contains(id) {
            bail!("Runner '{id}' is being deleted");
        }
        if operations.stopping.contains(id) {
            bail!("Runner '{id}' has a stop operation in progress");
        }
        if operations.updating.contains(id) {
            bail!("Runner '{id}' is being updated");
        }
        if !self.runners.read().await.contains_key(id) {
            bail!("Runner not found");
        }
        if !operations.starting.insert(id.to_string()) {
            bail!("Runner '{id}' already has a start operation in progress");
        }
        Ok(())
    }

    pub(crate) async fn finish_start_operation(&self, id: &str) {
        self.lifecycle_operations.lock().await.starting.remove(id);
    }

    pub(crate) async fn begin_stop_operation(&self, id: &str) -> Result<()> {
        let mut operations = self.lifecycle_operations.lock().await;
        if operations.deleting.contains(id) {
            bail!("Runner '{id}' is being deleted");
        }
        if operations.starting.contains(id) {
            bail!("Runner '{id}' has a start operation in progress");
        }
        if operations.updating.contains(id) {
            bail!("Runner '{id}' is being updated");
        }
        if !self.runners.read().await.contains_key(id) {
            bail!("Runner not found");
        }
        if !operations.stopping.insert(id.to_string()) {
            bail!("Runner '{id}' already has a stop operation in progress");
        }
        Ok(())
    }

    pub(crate) async fn finish_stop_operation(&self, id: &str) {
        self.lifecycle_operations.lock().await.stopping.remove(id);
    }

    async fn begin_update_operation(&self, id: &str) -> Result<()> {
        let mut operations = self.lifecycle_operations.lock().await;
        if operations.deleting.contains(id) {
            bail!("Runner '{id}' is being deleted");
        }
        if operations.starting.contains(id) {
            bail!("Runner '{id}' has a start operation in progress");
        }
        if operations.stopping.contains(id) {
            bail!("Runner '{id}' has a stop operation in progress");
        }
        if !self.runners.read().await.contains_key(id) {
            bail!("Runner not found");
        }
        if !operations.updating.insert(id.to_string()) {
            bail!("Runner '{id}' already has an update in progress");
        }
        Ok(())
    }

    async fn finish_update_operation(&self, id: &str) {
        self.lifecycle_operations.lock().await.updating.remove(id);
    }

    async fn begin_delete_operation(&self, id: &str) -> Result<()> {
        let mut operations = self.lifecycle_operations.lock().await;
        if !self.runners.read().await.contains_key(id) {
            bail!("Runner not found");
        }
        if !operations.deleting.insert(id.to_string()) {
            bail!("Runner '{id}' already has a deletion in progress");
        }
        Ok(())
    }

    async fn finish_delete_operation(&self, id: &str) {
        self.lifecycle_operations.lock().await.deleting.remove(id);
    }

    async fn wait_for_mutations_to_finish(&self, id: &str) -> Result<()> {
        tokio::time::timeout(std::time::Duration::from_secs(60), async {
            loop {
                let operations = self.lifecycle_operations.lock().await;
                if !operations.starting.contains(id)
                    && !operations.stopping.contains(id)
                    && !operations.updating.contains(id)
                {
                    break;
                }
                drop(operations);
                tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            }
        })
        .await
        .map_err(|_| {
            anyhow::anyhow!("Timed out waiting for runner '{id}' lifecycle operation to finish")
        })?;
        Ok(())
    }
""",
)

replace_once(
    RUNNER,
    """        if self.has_active_process(id).await {
            self.stop_process(id).await?;
        }

        let removed = self.runners.write().await.remove(id);
        if let Err(error) = self.save_to_disk().await {
            if let Some(runner) = removed {
                self.runners.write().await.insert(id.to_string(), runner);
            }
            return Err(error).context("persisting runner deletion");
        }

        // Destructive cleanup happens only after the durable state no longer""",
    """        if self.has_active_process(id).await {
            self.stop_process_internal(id, true).await?;
        }

        let _persistence_guard = self.persistence_lock.lock().await;
        let removed = self.runners.write().await.remove(id);
        if let Err(error) = self.save_to_disk_locked().await {
            if let Some(runner) = removed {
                self.runners.write().await.insert(id.to_string(), runner);
            }
            return Err(error).context("persisting runner deletion");
        }
        drop(_persistence_guard);

        // Destructive cleanup happens only after the durable state no longer""",
)

replace_once(
    RUNNER,
    """        let display_name = match req.display_name {
            Some(value) => Some(types::normalize_display_name(value)?),
            None => None,
        };
        let previous = {""",
    """        let display_name = match req.display_name {
            Some(value) => Some(types::normalize_display_name(value)?),
            None => None,
        };
        let _persistence_guard = self.persistence_lock.lock().await;
        let previous = {""",
)
replace_once(
    RUNNER,
    """        if let Err(error) = self.save_to_disk().await {
            let mut runners = self.runners.write().await;
            if let Some(runner) = runners.get_mut(id) {""",
    """        if let Err(error) = self.save_to_disk_locked().await {
            let mut runners = self.runners.write().await;
            if let Some(runner) = runners.get_mut(id) {""",
)
replace_once(
    RUNNER,
    """            return Err(error).context("persisting runner update");
        }
        self.get(id)""",
    """            return Err(error).context("persisting runner update");
        }
        drop(_persistence_guard);
        self.get(id)""",
)

# Existing start/restart and recovery callers can now acquire the reservation
# before changing state, then invoke this helper while retaining that reservation.
replace_once(
    RUNNER,
    """    /// Common register-and-start flow (assumes already in Registering state):
    /// Downloads runner binary if needed, removes stale configuration via""",
    """    /// Start an existing Offline/Error runner while the caller retains the
    /// start reservation. State cannot be exposed as Registering before admission.
    pub(crate) async fn start_existing_reserved(
        &self,
        id: &str,
        auth_token: &str,
    ) -> Result<()> {
        self.set_desired_running(id, true).await?;
        self.update_state(id, RunnerState::Registering).await?;
        self.emit_state_event(id, "registering");
        self.do_register_and_start(id, auth_token).await
    }

    /// Common register-and-start flow (assumes already in Registering state):
    /// Downloads runner binary if needed, removes stale configuration via""",
)

# Intent mutation and persistence are one serial transaction.
replace_once(
    RUNNER,
    """    async fn set_desired_running(&self, runner_id: &str, desired: bool) -> Result<()> {
        if desired && !self.runners.read().await.contains_key(runner_id) {""",
    """    pub(crate) async fn set_desired_running(
        &self,
        runner_id: &str,
        desired: bool,
    ) -> Result<()> {
        let _persistence_guard = self.persistence_lock.lock().await;
        if desired && !self.runners.read().await.contains_key(runner_id) {""",
)
replace_once(
    RUNNER,
    """        if let Err(error) = self.save_to_disk().await {
            let mut desired_running = self.desired_running.write().await;""",
    """        if let Err(error) = self.save_to_disk_locked().await {
            let mut desired_running = self.desired_running.write().await;""",
)

# Recovery reserves the start before publishing Registering, rather than
# transitioning first and attempting admission afterwards.
replace_once(
    RUNNER,
    """                if let Err(error) = manager
                    .update_state(&runner_id, RunnerState::Registering)
                    .await
                {
                    tracing::warn!(runner = %runner_id, error = %error, "Recovery state transition failed");
                    break;
                }
                manager.emit_state_event(&runner_id, "registering");

                match manager
                    .register_and_start_from_registering(&runner_id, &token)
                    .await
                {
                    Ok(()) => {
                        tracing::info!(
                            runner = %runner_id,
                            attempt = attempt + 1,
                            "Runner recovered after an unexpected exit"
                        );
                        break;
                    }
                    Err(error) => {
                        attempt += 1;
                        let next_delay = BACKOFF_SECS[attempt.min(BACKOFF_SECS.len() - 1)];
                        tracing::error!(
                            runner = %runner_id,
                            attempt,
                            error = %error,
                            "Automatic runner recovery failed"
                        );
                        let _ = manager
                            .update_state_with_error(
                                &runner_id,
                                RunnerState::Error,
                                Some(format!(
                                    "Automatic recovery attempt {attempt} failed: {error:#}. Retrying in {next_delay}s"
                                )),
                            )
                            .await;
                        manager.emit_state_event(&runner_id, "error");
                    }
                }""",
    """                if let Err(error) = manager.begin_start_operation(&runner_id).await {
                    tracing::debug!(
                        runner = %runner_id,
                        error = %error,
                        "Recovery deferred by another lifecycle operation"
                    );
                    continue;
                }

                match manager.start_existing_reserved(&runner_id, &token).await {
                    Ok(()) => {
                        manager.finish_start_operation(&runner_id).await;
                        tracing::info!(
                            runner = %runner_id,
                            attempt = attempt + 1,
                            "Runner recovered after an unexpected exit"
                        );
                        break;
                    }
                    Err(error) => {
                        attempt += 1;
                        let next_delay = BACKOFF_SECS[attempt.min(BACKOFF_SECS.len() - 1)];
                        tracing::error!(
                            runner = %runner_id,
                            attempt,
                            error = %error,
                            "Automatic runner recovery failed"
                        );
                        let _ = manager
                            .update_state_with_error(
                                &runner_id,
                                RunnerState::Error,
                                Some(format!(
                                    "Automatic recovery attempt {attempt} failed: {error:#}. Retrying in {next_delay}s"
                                )),
                            )
                            .await;
                        manager.emit_state_event(&runner_id, "error");
                        manager.finish_start_operation(&runner_id).await;
                    }
                }""",
)

# Stop transition also participates in the persistence transaction.
replace_once(
    RUNNER,
    """    async fn begin_stop(&self, id: &str, clear_desired: bool) -> Result<()> {
        // Match save_to_disk's lock ordering so a persistence snapshot cannot
        // deadlock with a lifecycle transition.
        let (previous_state, was_desired) = {""",
    """    async fn begin_stop(&self, id: &str, clear_desired: bool) -> Result<()> {
        // Hold the persistence transaction across mutation, durable write, and
        // rollback so no other save can publish an intermediate stop state.
        let _persistence_guard = self.persistence_lock.lock().await;
        let (previous_state, was_desired) = {""",
)
replace_once(
    RUNNER,
    """        if let Err(error) = self.save_to_disk().await {
            let mut desired_running = self.desired_running.write().await;""",
    """        if let Err(error) = self.save_to_disk_locked().await {
            let mut desired_running = self.desired_running.write().await;""",
)
replace_once(
    RUNNER,
    """        self.emit_state_event(id, "stopping");
        Ok(())
    }

    /// Stop a running runner process because the user explicitly requested it.
    pub async fn stop_process(&self, id: &str) -> Result<()> {
        self.stop_process_internal(id, true).await
    }

    /// Stop a process while retaining the user's desired-running intent. This is
    /// used by restart and daemon shutdown flows so a later launch can restore it.
    pub async fn stop_process_preserving_intent(&self, id: &str) -> Result<()> {
        self.stop_process_internal(id, false).await
    }""",
    """        drop(_persistence_guard);
        self.emit_state_event(id, "stopping");
        Ok(())
    }

    /// Stop a running runner process because the user explicitly requested it.
    pub async fn stop_process(&self, id: &str) -> Result<()> {
        self.begin_stop_operation(id).await?;
        let result = self.stop_process_internal(id, true).await;
        self.finish_stop_operation(id).await;
        result
    }

    /// Stop a process while retaining the user's desired-running intent. This is
    /// used by daemon shutdown flows so a later launch can restore it.
    pub async fn stop_process_preserving_intent(&self, id: &str) -> Result<()> {
        self.begin_stop_operation(id).await?;
        let result = self.stop_process_internal(id, false).await;
        self.finish_stop_operation(id).await;
        result
    }""",
)
replace_once(
    RUNNER,
    """    async fn stop_process_internal(&self, id: &str, clear_desired: bool) -> Result<()> {""",
    """    pub(crate) async fn stop_process_internal(
        &self,
        id: &str,
        clear_desired: bool,
    ) -> Result<()> {""",
)

# Full deletion already owns the deletion reservation and must not attempt to
# acquire a nested stop reservation that correctly rejects deleting runners.
replace_once(
    RUNNER,
    """            self.stop_process(id)
                .await
                .context("Failed to stop runner before deletion")?;""",
    """            self.stop_process_internal(id, true)
                .await
                .context("Failed to stop runner before deletion")?;""",
)

# Add deterministic regression tests: while persistence_lock is held, a durable
# operation must not publish its new in-memory state, and delete must wait for an
# already-admitted explicit stop.
replace_once(
    RUNNER,
    """    #[tokio::test]
    async fn test_update_reservation_blocks_start_and_delete_waits_for_update() {""",
    """    #[tokio::test]
    async fn test_persisted_mutations_wait_for_their_persistence_transaction() {
        let manager = create_test_manager();
        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id;

        let persistence_guard = manager.persistence_lock.lock().await;
        let update = {
            let manager = manager.clone();
            let id = id.clone();
            tokio::spawn(async move {
                manager
                    .update(
                        &id,
                        types::UpdateRunnerRequest {
                            labels: None,
                            mode: None,
                            display_name: Some(Some("durable alias".to_string())),
                        },
                    )
                    .await
            })
        };
        tokio::time::sleep(std::time::Duration::from_millis(25)).await;
        assert_eq!(manager.get(&id).await.unwrap().config.display_name, None);
        drop(persistence_guard);
        update.await.unwrap().unwrap();
        assert_eq!(
            manager.get(&id).await.unwrap().config.display_name.as_deref(),
            Some("durable alias")
        );

        manager.set_desired_running(&id, true).await.unwrap();
        let persistence_guard = manager.persistence_lock.lock().await;
        let clear_intent = {
            let manager = manager.clone();
            let id = id.clone();
            tokio::spawn(async move { manager.set_desired_running(&id, false).await })
        };
        tokio::time::sleep(std::time::Duration::from_millis(25)).await;
        assert!(manager.is_desired_running(&id).await);
        drop(persistence_guard);
        clear_intent.await.unwrap().unwrap();
        assert!(!manager.is_desired_running(&id).await);

        manager
            .update_state(&id, RunnerState::Registering)
            .await
            .unwrap();
        manager.update_state(&id, RunnerState::Online).await.unwrap();
        manager.set_desired_running(&id, true).await.unwrap();
        let persistence_guard = manager.persistence_lock.lock().await;
        let stopping = {
            let manager = manager.clone();
            let id = id.clone();
            tokio::spawn(async move { manager.begin_stop(&id, true).await })
        };
        tokio::time::sleep(std::time::Duration::from_millis(25)).await;
        let current = manager.get(&id).await.unwrap();
        assert_eq!(current.state, RunnerState::Online);
        assert!(manager.is_desired_running(&id).await);
        drop(persistence_guard);
        stopping.await.unwrap().unwrap();
        assert_eq!(manager.get(&id).await.unwrap().state, RunnerState::Stopping);
        assert!(!manager.is_desired_running(&id).await);
    }

    #[tokio::test]
    async fn test_delete_waits_for_an_admitted_stop_operation() {
        let manager = create_test_manager();
        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id;

        manager.begin_stop_operation(&id).await.unwrap();
        assert!(manager.begin_start_operation(&id).await.is_err());
        assert!(manager
            .update(
                &id,
                types::UpdateRunnerRequest {
                    labels: None,
                    mode: None,
                    display_name: Some(Some("blocked".to_string())),
                },
            )
            .await
            .is_err());
        manager.begin_delete_operation(&id).await.unwrap();

        let waiter = {
            let manager = manager.clone();
            let id = id.clone();
            tokio::spawn(async move { manager.wait_for_mutations_to_finish(&id).await })
        };
        tokio::task::yield_now().await;
        assert!(!waiter.is_finished());
        manager.finish_stop_operation(&id).await;
        waiter.await.unwrap().unwrap();
        manager.finish_delete_operation(&id).await;
    }

    #[tokio::test]
    async fn test_update_reservation_blocks_start_and_delete_waits_for_update() {""",
)

# API start/restart reserve synchronously before exposing Registering or stopping.
replace_once(
    API,
    """    let manager = state.runner_manager.clone();
    let runner_id = id.clone();
    tokio::spawn(async move {
        // Offline/Error -> Registering is a valid transition
        if let Err(e) = manager
            .update_state(&runner_id, crate::runner::state::RunnerState::Registering)
            .await
        {
            tracing::error!("Failed to transition runner {}: {}", runner_id, e);
            return;
        }
        if let Err(e) = manager
            .register_and_start_from_registering(&runner_id, &token)
            .await
        {
            tracing::error!("Failed to start runner {}: {}", runner_id, e);
            let _ = manager
                .update_state_with_error(
                    &runner_id,
                    crate::runner::state::RunnerState::Error,
                    Some(format!("{e:#}")),
                )
                .await;
            manager.schedule_recovery(runner_id);
        }
    });""",
    """    state
        .runner_manager
        .begin_start_operation(&id)
        .await
        .map_err(|e| (StatusCode::CONFLICT, e.to_string()))?;

    let manager = state.runner_manager.clone();
    let runner_id = id.clone();
    tokio::spawn(async move {
        if let Err(e) = manager.start_existing_reserved(&runner_id, &token).await {
            tracing::error!("Failed to start runner {}: {}", runner_id, e);
            let _ = manager
                .update_state_with_error(
                    &runner_id,
                    crate::runner::state::RunnerState::Error,
                    Some(format!("{e:#}")),
                )
                .await;
            manager.schedule_recovery(runner_id.clone());
        }
        manager.finish_start_operation(&runner_id).await;
    });""",
)

replace_once(
    API,
    """    // Stop if running, but retain desired-running intent throughout restart.
    if runner.state == crate::runner::state::RunnerState::Online
        || runner.state == crate::runner::state::RunnerState::Busy
        || state.runner_manager.has_active_process(&id).await
    {
        if let Err(error) = state
            .runner_manager
            .stop_process_preserving_intent(&id)
            .await
        {
            state.runner_manager.schedule_recovery(id.clone());
            return Err((
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Failed to stop runner for restart: {error}"),
            ));
        }
    }

    // Now start — the old process is fully stopped.

    let manager = state.runner_manager.clone();
    let runner_id = id.clone();
    tokio::spawn(async move {
        if let Err(e) = manager
            .update_state(&runner_id, crate::runner::state::RunnerState::Registering)
            .await
        {
            tracing::error!("Failed to transition runner {}: {}", runner_id, e);
            manager.schedule_recovery(runner_id);
            return;
        }
        if let Err(e) = manager
            .register_and_start_from_registering(&runner_id, &token)
            .await
        {
            tracing::error!("Failed to restart runner {}: {}", runner_id, e);
            let _ = manager
                .update_state_with_error(
                    &runner_id,
                    crate::runner::state::RunnerState::Error,
                    Some(format!("{e:#}")),
                )
                .await;
            manager.schedule_recovery(runner_id);
        }
    });""",
    """    // Reserve the full stop -> start sequence before mutating state. This
    // makes concurrent Start/Restart/Delete/PATCH requests deterministic.
    state
        .runner_manager
        .begin_start_operation(&id)
        .await
        .map_err(|e| (StatusCode::CONFLICT, e.to_string()))?;

    if let Err(error) = state.runner_manager.set_desired_running(&id, true).await {
        state.runner_manager.finish_start_operation(&id).await;
        return Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("Failed to persist restart intent: {error}"),
        ));
    }

    // Stop if running, retaining the already-persisted restart intent.
    if runner.state == crate::runner::state::RunnerState::Online
        || runner.state == crate::runner::state::RunnerState::Busy
        || state.runner_manager.has_active_process(&id).await
    {
        if let Err(error) = state.runner_manager.stop_process_internal(&id, false).await {
            state.runner_manager.finish_start_operation(&id).await;
            state.runner_manager.schedule_recovery(id.clone());
            return Err((
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Failed to stop runner for restart: {error}"),
            ));
        }
    }

    // Now start — the old process is fully stopped and deletion remains blocked.
    let manager = state.runner_manager.clone();
    let runner_id = id.clone();
    tokio::spawn(async move {
        if let Err(e) = manager.start_existing_reserved(&runner_id, &token).await {
            tracing::error!("Failed to restart runner {}: {}", runner_id, e);
            let _ = manager
                .update_state_with_error(
                    &runner_id,
                    crate::runner::state::RunnerState::Error,
                    Some(format!("{e:#}")),
                )
                .await;
            manager.schedule_recovery(runner_id.clone());
        }
        manager.finish_start_operation(&runner_id).await;
    });""",
)

# Lifecycle conflicts are resource-state conflicts, not malformed requests.
replace_once(
    API,
    """        } else if message.contains("cannot be changed")
            || message.contains("only be changed while the runner is stopped")
        {""",
    """        } else if message.contains("cannot be changed")
            || message.contains("only be changed while the runner is stopped")
            || message.contains("operation in progress")
            || message.contains("being deleted")
            || message.contains("being updated")
        {""",
)

replace_once(
    API,
    """    #[tokio::test]
    async fn test_restart_rejects_transitional_state_before_authentication() {""",
    """    #[tokio::test]
    async fn test_start_reserves_before_publishing_registering_state() {
        let state = AppState::new_test_authenticated();
        let runner = state
            .runner_manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id;
        state
            .runner_manager
            .update_state(&id, crate::runner::state::RunnerState::Error)
            .await
            .unwrap();
        state.runner_manager.begin_stop_operation(&id).await.unwrap();

        let app = create_router(state.clone());
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!("/runners/{id}/start"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::CONFLICT);
        assert_eq!(
            state.runner_manager.get(&id).await.unwrap().state,
            crate::runner::state::RunnerState::Error
        );
        state.runner_manager.finish_stop_operation(&id).await;
    }

    #[tokio::test]
    async fn test_restart_rejects_transitional_state_before_authentication() {""",
)
