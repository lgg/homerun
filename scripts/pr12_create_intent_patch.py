from pathlib import Path

# Runner manager: split create into wrappers + internal desired-intent implementation.
p = Path('crates/daemon/src/runner/mod.rs')
s = p.read_text()
old_sig = '''    pub async fn create(
        &self,
        repo_full_name: &str,
        name: Option<String>,
        labels: Option<Vec<String>>,
        mode: Option<RunnerMode>,
        group_id: Option<String>,
        container: Option<types::ContainerConfig>,
    ) -> Result<RunnerInfo> {
'''
new_sig = '''    pub async fn create(
        &self,
        repo_full_name: &str,
        name: Option<String>,
        labels: Option<Vec<String>>,
        mode: Option<RunnerMode>,
        group_id: Option<String>,
        container: Option<types::ContainerConfig>,
    ) -> Result<RunnerInfo> {
        self.create_with_intent(
            repo_full_name,
            name,
            labels,
            mode,
            group_id,
            container,
            false,
        )
        .await
    }

    pub(crate) async fn create_desired_running(
        &self,
        repo_full_name: &str,
        name: Option<String>,
        labels: Option<Vec<String>>,
        mode: Option<RunnerMode>,
        group_id: Option<String>,
        container: Option<types::ContainerConfig>,
    ) -> Result<RunnerInfo> {
        self.create_with_intent(
            repo_full_name,
            name,
            labels,
            mode,
            group_id,
            container,
            true,
        )
        .await
    }

    async fn create_with_intent(
        &self,
        repo_full_name: &str,
        name: Option<String>,
        labels: Option<Vec<String>>,
        mode: Option<RunnerMode>,
        group_id: Option<String>,
        container: Option<types::ContainerConfig>,
        desired: bool,
    ) -> Result<RunnerInfo> {
'''
assert s.count(old_sig) == 1
s = s.replace(old_sig, new_sig, 1)
old_insert = '''            runners.insert(id.clone(), runner.clone());
        }

        if let Err(error) = self.save_to_disk_locked().await {
            self.runners.write().await.remove(&id);
            let _ = std::fs::remove_dir_all(&runner.config.work_dir);
            return Err(error).context("persisting newly-created runner");
        }
'''
new_insert = '''            runners.insert(id.clone(), runner.clone());
        }
        if desired {
            self.desired_running.write().await.insert(id.clone());
        }

        if let Err(error) = self.save_to_disk_locked().await {
            self.runners.write().await.remove(&id);
            if desired {
                self.desired_running.write().await.remove(&id);
            }
            let _ = std::fs::remove_dir_all(&runner.config.work_dir);
            return Err(error).context("persisting newly-created runner");
        }
'''
assert s.count(old_insert) == 1
s = s.replace(old_insert, new_insert, 1)
# Batch and scale-up creations always intend to run.
s = s.replace('''            match self
                .create(
                    repo_full_name,
''', '''            match self
                .create_desired_running(
                    repo_full_name,
''', 1)
s = s.replace('''                match self
                    .create(
                        &repo_full_name,
''', '''                match self
                    .create_desired_running(
                        &repo_full_name,
''', 1)
# Broaden reserved start documentation to include Creating.
s = s.replace('''    /// Start an existing Offline/Error runner while the caller retains the
    /// start reservation. Manual API callers persist desired-running intent before
    /// spawning this work; startup restore and recovery already have that intent.
''', '''    /// Start a Creating/Offline/Error runner while the caller retains the start
    /// reservation. Manual API callers persist desired-running intent before spawning
    /// this work; startup restore and recovery already have that intent.
''', 1)
# Add atomic creation regression test before existing desired-running test.
marker = '''    #[tokio::test]
    async fn test_desired_running_is_persisted_independently_from_error_state() {
'''
assert s.count(marker) == 1
test = '''    #[tokio::test]
    async fn test_create_desired_running_persists_intent_atomically() {
        let manager = create_test_manager();
        let runner = manager
            .create_desired_running("owner/repo", None, None, None, None, None)
            .await
            .unwrap();

        assert!(manager.is_desired_running(&runner.config.id).await);
        let persisted: Vec<PersistedRunner> = serde_json::from_str(
            &std::fs::read_to_string(manager.config.runners_json_path()).unwrap(),
        )
        .unwrap();
        assert_eq!(persisted.len(), 1);
        assert_eq!(persisted[0].config.id, runner.config.id);
        assert!(persisted[0].was_running);
    }

'''
s = s.replace(marker, test + marker, 1)
p.write_text(s)

# Single create API: atomically persist desired intent, reserve synchronously, then spawn reserved work.
p = Path('crates/daemon/src/api/runners.rs')
s = p.read_text()
s = s.replace('''        .create(
            &req.repo_full_name,
''', '''        .create_desired_running(
            &req.repo_full_name,
''', 1)
old = '''    // Spawn background task to register and start the runner.
    let manager = state.runner_manager.clone();
    let runner_id = runner.config.id.clone();
    tokio::spawn(async move {
        if let Err(e) = manager.register_and_start(&runner_id, &token).await {
            tracing::error!("Failed to register and start runner {}: {}", runner_id, e);
            let _ = manager
                .update_state_with_error(
                    &runner_id,
                    crate::runner::state::RunnerState::Error,
                    Some(format!("{e:#}")),
                )
                .await;
            manager.schedule_recovery(runner_id);
        }
    });
'''
new = '''    // Persist the running intent as part of creation, then reserve the lifecycle
    // before returning 201 so a crash or immediate Delete cannot strand a Creating runner.
    let runner_id = runner.config.id.clone();
    if let Err(error) = state.runner_manager.begin_start_operation(&runner_id).await {
        let cleanup_error = state.runner_manager.delete(&runner_id).await.err();
        let detail = cleanup_error
            .map(|cleanup| format!("; cleanup also failed: {cleanup}"))
            .unwrap_or_default();
        return Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("Failed to admit runner start: {error}{detail}"),
        ));
    }

    let manager = state.runner_manager.clone();
    tokio::spawn(async move {
        if let Err(e) = manager.start_existing_reserved(&runner_id, &token).await {
            tracing::error!("Failed to register and start runner {}: {}", runner_id, e);
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
    });
'''
assert s.count(old) == 1
s = s.replace(old, new, 1)
p.write_text(s)

# Batch and scale API: reserve all newly-created runners before spawning any work.
p = Path('crates/daemon/src/api/groups.rs')
s = p.read_text()
old = '''    // Spawn background registration for each runner.
    for runner in &runners {
        let manager = state.runner_manager.clone();
        let runner_id = runner.config.id.clone();
        let token = token.clone();
        tokio::spawn(async move {
            if let Err(e) = manager.register_and_start(&runner_id, &token).await {
                tracing::error!("Failed to register runner {}: {}", runner_id, e);
                let _ = manager
                    .update_state_with_error(
                        &runner_id,
                        crate::runner::state::RunnerState::Error,
                        Some(format!("{e:#}")),
                    )
                    .await;
                manager.schedule_recovery(runner_id);
            }
        });
    }
'''
new = '''    // Creation persisted desired-running intent atomically. Reserve every start
    // before spawning any work so the batch response cannot publish unadmitted runners.
    let mut reserved = Vec::with_capacity(runners.len());
    for runner in &runners {
        let runner_id = runner.config.id.clone();
        if let Err(error) = state.runner_manager.begin_start_operation(&runner_id).await {
            for reserved_id in &reserved {
                state
                    .runner_manager
                    .finish_start_operation(reserved_id)
                    .await;
            }
            let mut cleanup_errors = Vec::new();
            for created in &runners {
                if let Err(cleanup) = state.runner_manager.delete(&created.config.id).await {
                    cleanup_errors.push(format!("{}: {cleanup}", created.config.id));
                }
            }
            let cleanup_detail = if cleanup_errors.is_empty() {
                String::new()
            } else {
                format!("; cleanup failures: {}", cleanup_errors.join(", "))
            };
            return Err((
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Failed to admit batch runner start: {error}{cleanup_detail}"),
            ));
        }
        reserved.push(runner_id);
    }

    for runner_id in reserved {
        let manager = state.runner_manager.clone();
        let token = token.clone();
        tokio::spawn(async move {
            if let Err(e) = manager.start_existing_reserved(&runner_id, &token).await {
                tracing::error!("Failed to register runner {}: {}", runner_id, e);
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
        });
    }
'''
assert s.count(old) == 1
s = s.replace(old, new, 1)
old2 = '''    let response = state
        .runner_manager
        .scale_group(&group_id, req.count)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    // Spawn registration for added runners. The precondition above guarantees
    // a token whenever additions are possible.
    for runner in &response.added {
        let manager = state.runner_manager.clone();
        let runner_id = runner.config.id.clone();
        let token = token
            .clone()
            .expect("scale-up token checked before mutation");
        tokio::spawn(async move {
            if let Err(e) = manager.register_and_start(&runner_id, &token).await {
                tracing::error!("Failed to register runner {}: {}", runner_id, e);
                let _ = manager
                    .update_state_with_error(
                        &runner_id,
                        crate::runner::state::RunnerState::Error,
                        Some(format!("{e:#}")),
                    )
                    .await;
                manager.schedule_recovery(runner_id);
            }
        });
    }

    Ok(Json(response))
'''
new2 = '''    let response = state
        .runner_manager
        .scale_group(&group_id, req.count)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    // Scale-up creation persisted intent atomically. Reserve every new runner
    // before spawning any of them so the returned count is fully admitted.
    let mut reserved = Vec::with_capacity(response.added.len());
    for runner in &response.added {
        let runner_id = runner.config.id.clone();
        if let Err(error) = state.runner_manager.begin_start_operation(&runner_id).await {
            for reserved_id in &reserved {
                state
                    .runner_manager
                    .finish_start_operation(reserved_id)
                    .await;
            }
            let mut cleanup_errors = Vec::new();
            for added in &response.added {
                if let Err(cleanup) = state.runner_manager.delete(&added.config.id).await {
                    cleanup_errors.push(format!("{}: {cleanup}", added.config.id));
                }
            }
            let cleanup_detail = if cleanup_errors.is_empty() {
                String::new()
            } else {
                format!("; cleanup failures: {}", cleanup_errors.join(", "))
            };
            return Err((
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Failed to admit scaled runner start: {error}{cleanup_detail}"),
            ));
        }
        reserved.push(runner_id);
    }

    for runner_id in reserved {
        let manager = state.runner_manager.clone();
        let token = token
            .clone()
            .expect("scale-up token checked before mutation");
        tokio::spawn(async move {
            if let Err(e) = manager.start_existing_reserved(&runner_id, &token).await {
                tracing::error!("Failed to register runner {}: {}", runner_id, e);
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
        });
    }

    Ok(Json(response))
'''
assert s.count(old2) == 1
s = s.replace(old2, new2, 1)
# Fix stale delete comment while here.
s = s.replace('''                // Local deletion still waits for registration and stops any
                // process safely; it simply cannot deregister from GitHub.
''', '''                // Local deletion is allowed only for runners that were never
                // configured; configured runners return a per-runner auth error.
''', 1)
p.write_text(s)

# Strengthen the native remove test and remove stale full-delete test commentary.
p = Path('crates/daemon/src/runner/process.rs')
s = p.read_text()
old = '''        // On Unix, spawn fails because the script doesn't exist.
        // On Windows, the command may run but exit with failure.
        // Both are acceptable — the function handles non-success exits.
        // Just verify it doesn't panic.
        let _ = result;
'''
new = '''        // On Unix, spawning the missing script fails. On Windows, cmd.exe may
        // start but must still return a non-success status. Both must surface.
        assert!(result.is_err());
'''
assert s.count(old) == 1
s = s.replace(old, new, 1)
p.write_text(s)

p = Path('crates/daemon/src/runner/mod.rs')
s = p.read_text()
old = '''        // full_delete with invalid token: GitHub deregistration will fail but runner
        // should still be removed from the local store.
        let result = manager.full_delete(&id, "invalid-token").await;
        // We expect success (the function ignores deregistration errors)
        assert!(
            result.is_ok(),
            "full_delete should succeed even with invalid token"
        );
'''
new = '''        // The runner was never configured, so full_delete must skip the remote
        // API entirely and remove it even when the supplied token is invalid.
        let result = manager.full_delete(&id, "invalid-token").await;
        assert!(result.is_ok(), "unconfigured full_delete should stay local");
'''
assert s.count(old) == 1
s = s.replace(old, new, 1)
p.write_text(s)
