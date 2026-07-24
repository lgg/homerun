from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match in {path}, found {text.count(old)}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


# GitHub exposes a distinct remove-token endpoint for config.sh remove.
replace_once(
    "crates/daemon/src/github/mod.rs",
    '''    pub async fn get_runner_registration_token(
        &self,
        owner: &str,
        repo: &str,
    ) -> Result<RunnerRegistration> {
        #[derive(Deserialize)]
        struct RegistrationTokenResponse {
            token: String,
            expires_at: String,
        }

        let route = format!("/repos/{owner}/{repo}/actions/runners/registration-token");
        let response: RegistrationTokenResponse = self.octocrab.post(route, None::<&()>).await?;

        Ok(RunnerRegistration {
            token: response.token,
            expires_at: response.expires_at,
        })
    }
''',
    '''    pub async fn get_runner_registration_token(
        &self,
        owner: &str,
        repo: &str,
    ) -> Result<RunnerRegistration> {
        #[derive(Deserialize)]
        struct RegistrationTokenResponse {
            token: String,
            expires_at: String,
        }

        let route = format!("/repos/{owner}/{repo}/actions/runners/registration-token");
        let response: RegistrationTokenResponse = self.octocrab.post(route, None::<&()>).await?;

        Ok(RunnerRegistration {
            token: response.token,
            expires_at: response.expires_at,
        })
    }

    pub async fn get_runner_remove_token(
        &self,
        owner: &str,
        repo: &str,
    ) -> Result<RunnerRegistration> {
        #[derive(Deserialize)]
        struct RemoveTokenResponse {
            token: String,
            expires_at: String,
        }

        let route = format!("/repos/{owner}/{repo}/actions/runners/remove-token");
        let response: RemoveTokenResponse = self.octocrab.post(route, None::<&()>).await?;

        Ok(RunnerRegistration {
            token: response.token,
            expires_at: response.expires_at,
        })
    }
''',
)

# Do not hide config-script failures, and consume stdout/stderr to avoid pipe deadlocks.
replace_once(
    "crates/daemon/src/runner/process.rs",
    '''pub async fn remove_runner(runner_dir: &Path, token: &str) -> Result<()> {
    let script = config_script();
    let status = Command::new(runner_dir.join(&script))
        .args(["remove", "--token", token])
        .current_dir(runner_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .status()
        .await?;

    if !status.success() {
        tracing::warn!(
            "{} remove failed — runner may need manual cleanup on GitHub",
            script
        );
    }
    Ok(())
}
''',
    '''pub async fn remove_runner(runner_dir: &Path, token: &str) -> Result<()> {
    let script = config_script();
    let output = Command::new(runner_dir.join(&script))
        .args(["remove", "--token", token])
        .current_dir(runner_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        let detail = if !stderr.trim().is_empty() {
            stderr.trim()
        } else {
            stdout.trim()
        };
        anyhow::bail!(
            "{} remove failed (exit {}): {}",
            script,
            output.status.code().unwrap_or(-1),
            detail
        );
    }
    Ok(())
}
''',
)

replace_once(
    "crates/daemon/src/runner/docker.rs",
    '''/// Runs `config.sh remove --token <token>` in a short-lived helper
/// container against the same bind-mounted `work_dir`, then removes the
/// helper container. Best-effort: mirrors `process::remove_runner`'s
/// behavior of logging (not failing) when GitHub-side removal doesn't
/// succeed — the runner may need manual cleanup on GitHub.
pub async fn deregister(docker: &Docker, work_dir: &Path, image: &str, token: &str) -> Result<()> {
    // config.sh remove hits the same root guard as config.sh/run.sh.
    let env = vec![
        "RUNNER_ALLOW_RUNASROOT=1".to_string(),
        format!("HOMERUN_RUNNER_TOKEN={token}"),
    ];
    let cmd = vec![
        "cd /workspace && exec ./config.sh remove --token \"$HOMERUN_RUNNER_TOKEN\"".to_string(),
    ];
    let name = format!("homerun-deregister-{}", uuid::Uuid::new_v4());

    match create_and_start(docker, work_dir, image, &name, env, cmd).await {
        Ok(container_id) => {
            let _ = wait_container(docker, &container_id).await;
            remove_container(docker, &container_id).await
        }
        Err(e) => {
            tracing::warn!(
                "Failed to start deregistration container — runner may need manual cleanup on GitHub: {e}"
            );
            Ok(())
        }
    }
}
''',
    '''/// Runs `config.sh remove --token <token>` in a short-lived helper
/// container against the same bind-mounted `work_dir`, then removes the
/// helper container. A non-zero config-script exit is returned to the caller:
/// local deletion must not silently leave a stale runner registered on GitHub.
pub async fn deregister(docker: &Docker, work_dir: &Path, image: &str, token: &str) -> Result<()> {
    // config.sh remove hits the same root guard as config.sh/run.sh.
    let env = vec![
        "RUNNER_ALLOW_RUNASROOT=1".to_string(),
        format!("HOMERUN_RUNNER_TOKEN={token}"),
    ];
    let cmd = vec![
        "cd /workspace && exec ./config.sh remove --token \"$HOMERUN_RUNNER_TOKEN\"".to_string(),
    ];
    let name = format!("homerun-deregister-{}", uuid::Uuid::new_v4());

    let container_id = create_and_start(docker, work_dir, image, &name, env, cmd)
        .await
        .context("Failed to start runner deregistration container")?;
    let wait_result = wait_container(docker, &container_id).await;
    let cleanup_result = remove_container(docker, &container_id).await;
    let exit_code = wait_result.context("Failed while waiting for runner deregistration")?;
    cleanup_result?;
    if exit_code != 0 {
        anyhow::bail!("Runner deregistration container exited with code {exit_code}");
    }
    Ok(())
}
''',
)

# Existing configurations must be removed with a remove token, never a registration token.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''        // If already configured, deregister before re-configuring.
        // The config script refuses to configure an already-configured runner, so we
        // must remove the old configuration first.
        if already_configured {
            if let (Some(dc), Some(cc)) = (&docker_client, container_cfg) {
                let _ = docker::deregister(dc, &config.work_dir, &cc.image, &reg.token).await;
            } else {
                let _ = remove_runner(&config.work_dir, &reg.token).await;
            }
            clean_runner_config(&config.work_dir);
        }
''',
    '''        // If already configured, deregister before re-configuring. GitHub
        // requires a dedicated remove token; a registration token is not valid for
        // `config.sh remove` and previously left stale runners behind silently.
        if already_configured {
            let removal = gh
                .get_runner_remove_token(&config.repo_owner, &config.repo_name)
                .await
                .context("Failed to get runner removal token")?;
            if let (Some(dc), Some(cc)) = (&docker_client, container_cfg) {
                docker::deregister(dc, &config.work_dir, &cc.image, &removal.token)
                    .await
                    .context("Failed to deregister existing container runner")?;
            } else {
                remove_runner(&config.work_dir, &removal.token)
                    .await
                    .context("Failed to deregister existing runner")?;
            }
            clean_runner_config(&config.work_dir);
        }
''',
)

# Desired-running intent is admitted synchronously by API callers or already exists
# for startup restore/recovery. This helper must not introduce a hidden durable failure
# after an endpoint has already reported success.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''    /// Start an existing Offline/Error runner while the caller retains the
    /// start reservation. State cannot be exposed as Registering before admission.
    pub(crate) async fn start_existing_reserved(&self, id: &str, auth_token: &str) -> Result<()> {
        self.set_desired_running(id, true).await?;
        self.update_state(id, RunnerState::Registering).await?;
        self.emit_state_event(id, "registering");
        self.do_register_and_start(id, auth_token).await
    }
''',
    '''    /// Start an existing Offline/Error runner while the caller retains the
    /// start reservation. Manual API callers persist desired-running intent before
    /// spawning this work; startup restore and recovery already have that intent.
    pub(crate) async fn start_existing_reserved(&self, id: &str, auth_token: &str) -> Result<()> {
        self.update_state(id, RunnerState::Registering).await?;
        self.emit_state_event(id, "registering");
        self.do_register_and_start(id, auth_token).await
    }
''',
)

# Deletion reservation blocks new mutations, so wait before changing user intent.
# This makes timeout/failure side-effect free and avoids persisting terminal Deleting.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''    async fn delete_reserved(&self, id: &str) -> Result<()> {
        // Cancel future recovery first, then wait until any in-flight registration
        // no longer uses the work directory. If it managed to start a process,
        // stop that process before removing files.
        self.set_desired_running(id, false).await?;
        self.wait_for_mutations_to_finish(id).await?;
        // An already-running start operation may have restored the intent after
        // deletion first cleared it. Clear it again after the reservation drains.
        self.set_desired_running(id, false).await?;
        if self.has_active_process(id).await {
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

        // Destructive cleanup happens only after the durable state no longer
        // references this runner. A failed write therefore cannot resurrect a
        // deleted runner with a missing work directory on the next launch.
        self.processes.write().await.remove(id);
        self.delete_job_history(id).await;
        if let Some(runner) = removed {
            let _ = std::fs::remove_dir_all(&runner.config.work_dir);
        }
        Ok(())
    }
''',
    '''    async fn prepare_delete_reserved(&self, id: &str) -> Result<()> {
        // The deletion reservation already blocks new Start/Stop/PATCH operations.
        // Wait before changing desired-running so a timeout leaves the runner and
        // its recovery intent exactly as they were before the delete request.
        self.wait_for_mutations_to_finish(id).await?;
        self.set_desired_running(id, false).await?;

        let runner = self
            .get(id)
            .await
            .ok_or_else(|| anyhow::anyhow!("Runner not found"))?;
        if runner.state == RunnerState::Online
            || runner.state == RunnerState::Busy
            || self.has_active_process(id).await
        {
            self.stop_process_internal(id, true).await?;
        }
        Ok(())
    }

    async fn remove_reserved(&self, id: &str) -> Result<()> {
        let _persistence_guard = self.persistence_lock.lock().await;
        let removed = self.runners.write().await.remove(id);
        if let Err(error) = self.save_to_disk_locked().await {
            if let Some(runner) = removed {
                self.runners.write().await.insert(id.to_string(), runner);
            }
            return Err(error).context("persisting runner deletion");
        }
        drop(_persistence_guard);

        // Destructive cleanup happens only after the durable state no longer
        // references this runner. A failed write therefore cannot resurrect a
        // deleted runner with a missing work directory on the next launch.
        self.processes.write().await.remove(id);
        self.delete_job_history(id).await;
        if let Some(runner) = removed {
            let _ = std::fs::remove_dir_all(&runner.config.work_dir);
        }
        Ok(())
    }

    async fn delete_reserved(&self, id: &str) -> Result<()> {
        self.prepare_delete_reserved(id).await?;
        self.emit_state_event(id, "deleting");
        self.remove_reserved(id).await
    }
''',
)

replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''    async fn full_delete_reserved(&self, id: &str, auth_token: &str) -> Result<()> {
        self.get(id)
            .await
            .ok_or_else(|| anyhow::anyhow!("Runner not found"))?;

        // Cancel recovery and serialize against an in-flight registration. The
        // registration may finish while deletion is waiting; refresh state and
        // stop any process it created before deregistration/removal.
        self.set_desired_running(id, false).await?;
        self.wait_for_mutations_to_finish(id).await?;
        let runner = self
            .get(id)
            .await
            .ok_or_else(|| anyhow::anyhow!("Runner not found"))?;
        if runner.state == RunnerState::Online
            || runner.state == RunnerState::Busy
            || self.has_active_process(id).await
        {
            self.stop_process_internal(id, true)
                .await
                .context("Failed to stop runner before deletion")?;
        }

        // Try to transition to Deleting
        {
            let mut runners = self.runners.write().await;
            if let Some(r) = runners.get_mut(id) {
                // Force the state for deletion
                r.state = RunnerState::Deleting;
            }
        }
        self.emit_state_event(id, "deleting");

        // Deregister from GitHub
        let config = &runner.config;
        if let Ok(gh) = GitHubClient::new(Some(auth_token.to_string())) {
            if let Ok(reg) = gh
                .get_runner_registration_token(&config.repo_owner, &config.repo_name)
                .await
            {
                if let Some(cc) = config.container.as_ref() {
                    if let Ok(dc) = docker::connect() {
                        let _ =
                            docker::deregister(&dc, &config.work_dir, &cc.image, &reg.token).await;
                    }
                } else {
                    let _ = remove_runner(&config.work_dir, &reg.token).await;
                }
            }
        }

        // Remove runner entry and work dir while retaining the deletion reservation.
        self.delete_reserved(id).await?;
        Ok(())
    }
''',
    '''    async fn full_delete_reserved(&self, id: &str, auth_token: &str) -> Result<()> {
        self.prepare_delete_reserved(id).await?;
        let runner = self
            .get(id)
            .await
            .ok_or_else(|| anyhow::anyhow!("Runner not found"))?;
        self.emit_state_event(id, "deleting");

        // Only configured runners have a GitHub registration to remove. Use the
        // dedicated removal token and propagate failures so local deletion never
        // silently leaves a stale remote runner behind.
        let config = &runner.config;
        let configured = config.work_dir.join(".runner").exists()
            || config.work_dir.join(".runner_migrated").exists();
        if configured {
            let gh = GitHubClient::new(Some(auth_token.to_string()))?;
            let removal = gh
                .get_runner_remove_token(&config.repo_owner, &config.repo_name)
                .await
                .context("Failed to get runner removal token")?;
            if let Some(cc) = config.container.as_ref() {
                let dc = docker::connect()?;
                docker::deregister(&dc, &config.work_dir, &cc.image, &removal.token)
                    .await
                    .context("Failed to deregister container runner")?;
            } else {
                remove_runner(&config.work_dir, &removal.token)
                    .await
                    .context("Failed to deregister runner")?;
            }
        }

        self.remove_reserved(id).await
    }
''',
)

# Manual single start must persist intent before the background task is reported accepted.
replace_once(
    "crates/daemon/src/api/runners.rs",
    '''    state
        .runner_manager
        .begin_start_operation(&id)
        .await
        .map_err(|e| (StatusCode::CONFLICT, e.to_string()))?;

    let manager = state.runner_manager.clone();
''',
    '''    state
        .runner_manager
        .begin_start_operation(&id)
        .await
        .map_err(|e| (StatusCode::CONFLICT, e.to_string()))?;
    if let Err(error) = state.runner_manager.set_desired_running(&id, true).await {
        state.runner_manager.finish_start_operation(&id).await;
        return Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("Failed to persist start intent: {error}"),
        ));
    }

    let manager = state.runner_manager.clone();
''',
)

# Group Start/Restart report durable admission failures synchronously and never
# mark a still-running process Error merely because persistence failed.
replace_once(
    "crates/daemon/src/api/groups.rs",
    '''                Ok(()) => {
                    let manager = state.runner_manager.clone();
                    let runner_id = id.clone();
                    let token = token.clone();
                    tokio::spawn(async move {
                        if let Err(error) =
                            manager.start_existing_reserved(&runner_id, &token).await
                        {
                            tracing::error!(
                                runner = %runner_id,
                                error = %error,
                                "Failed to start grouped runner"
                            );
                            let _ = manager
                                .update_state_with_error(
                                    &runner_id,
                                    RunnerState::Error,
                                    Some(format!("{error:#}")),
                                )
                                .await;
                            manager.schedule_recovery(runner_id.clone());
                        }
                        manager.finish_start_operation(&runner_id).await;
                    });
                    results.push(GroupActionResult {
                        runner_id: id,
                        success: true,
                        error: None,
                    });
                }
''',
    '''                Ok(()) => match state.runner_manager.set_desired_running(&id, true).await {
                    Ok(()) => {
                        let manager = state.runner_manager.clone();
                        let runner_id = id.clone();
                        let token = token.clone();
                        tokio::spawn(async move {
                            if let Err(error) =
                                manager.start_existing_reserved(&runner_id, &token).await
                            {
                                tracing::error!(
                                    runner = %runner_id,
                                    error = %error,
                                    "Failed to start grouped runner"
                                );
                                let _ = manager
                                    .update_state_with_error(
                                        &runner_id,
                                        RunnerState::Error,
                                        Some(format!("{error:#}")),
                                    )
                                    .await;
                                manager.schedule_recovery(runner_id.clone());
                            }
                            manager.finish_start_operation(&runner_id).await;
                        });
                        results.push(GroupActionResult {
                            runner_id: id,
                            success: true,
                            error: None,
                        });
                    }
                    Err(error) => {
                        state.runner_manager.finish_start_operation(&id).await;
                        results.push(GroupActionResult {
                            runner_id: id,
                            success: false,
                            error: Some(format!("Failed to persist start intent: {error}")),
                        });
                    }
                },
''',
)

replace_once(
    "crates/daemon/src/api/groups.rs",
    '''        match state.runner_manager.begin_start_operation(&id).await {
            Ok(()) => {
                let manager = state.runner_manager.clone();
                let runner_id = id.clone();
                let token = token.clone();
                tokio::spawn(async move {
                    let result = async {
                        manager.set_desired_running(&runner_id, true).await?;
                        let current = manager
                            .get(&runner_id)
                            .await
                            .ok_or_else(|| anyhow::anyhow!("Runner not found"))?;
                        if current.state == RunnerState::Online
                            || current.state == RunnerState::Busy
                            || manager.has_active_process(&runner_id).await
                        {
                            manager.stop_process_internal(&runner_id, false).await?;
                        }
                        manager.start_existing_reserved(&runner_id, &token).await
                    }
                    .await;

                    if let Err(error) = result {
                        tracing::error!(
                            runner = %runner_id,
                            error = %error,
                            "Failed to restart grouped runner"
                        );
                        let _ = manager
                            .update_state_with_error(
                                &runner_id,
                                RunnerState::Error,
                                Some(format!("{error:#}")),
                            )
                            .await;
                        manager.schedule_recovery(runner_id.clone());
                    }
                    manager.finish_start_operation(&runner_id).await;
                });
                results.push(GroupActionResult {
                    runner_id: id,
                    success: true,
                    error: None,
                });
            }
            Err(error) => results.push(GroupActionResult {
                runner_id: id,
                success: false,
                error: Some(error.to_string()),
            }),
        }
''',
    '''        match state.runner_manager.begin_start_operation(&id).await {
            Ok(()) => match state.runner_manager.set_desired_running(&id, true).await {
                Ok(()) => {
                    let manager = state.runner_manager.clone();
                    let runner_id = id.clone();
                    let token = token.clone();
                    tokio::spawn(async move {
                        let result = async {
                            let current = manager
                                .get(&runner_id)
                                .await
                                .ok_or_else(|| anyhow::anyhow!("Runner not found"))?;
                            if current.state == RunnerState::Online
                                || current.state == RunnerState::Busy
                                || manager.has_active_process(&runner_id).await
                            {
                                manager.stop_process_internal(&runner_id, false).await?;
                            }
                            manager.start_existing_reserved(&runner_id, &token).await
                        }
                        .await;

                        if let Err(error) = result {
                            tracing::error!(
                                runner = %runner_id,
                                error = %error,
                                "Failed to restart grouped runner"
                            );
                            let _ = manager
                                .update_state_with_error(
                                    &runner_id,
                                    RunnerState::Error,
                                    Some(format!("{error:#}")),
                                )
                                .await;
                            manager.schedule_recovery(runner_id.clone());
                        }
                        manager.finish_start_operation(&runner_id).await;
                    });
                    results.push(GroupActionResult {
                        runner_id: id,
                        success: true,
                        error: None,
                    });
                }
                Err(error) => {
                    state.runner_manager.finish_start_operation(&id).await;
                    results.push(GroupActionResult {
                        runner_id: id,
                        success: false,
                        error: Some(format!("Failed to persist restart intent: {error}")),
                    });
                }
            },
            Err(error) => results.push(GroupActionResult {
                runner_id: id,
                success: false,
                error: Some(error.to_string()),
            }),
        }
''',
)

# Strengthen regression coverage for side-effect-free waiting and unconfigured deletion.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''        // Simulate the already-admitted start restoring desired-running after
        // deletion's first clear, then allow that start operation to drain.
        manager.set_desired_running(&id, true).await.unwrap();
        manager.finish_start_operation(&id).await;
''',
    '''        // The deletion reservation blocks recovery/start admission, so Delete
        // must not mutate user intent while waiting for the admitted start to drain.
        assert!(manager.is_desired_running(&id).await);
        manager.finish_start_operation(&id).await;
''',
)

replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''    #[tokio::test]
    async fn test_update_rejects_mode_change_and_active_label_change() {
''',
    '''    #[tokio::test]
    async fn test_full_delete_skips_remote_api_for_unconfigured_runner() {
        let manager = create_test_manager();
        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id;

        manager.full_delete(&id, "not-a-real-token").await.unwrap();

        assert!(manager.get(&id).await.is_none());
        assert!(!manager.is_desired_running(&id).await);
    }

    #[tokio::test]
    async fn test_update_rejects_mode_change_and_active_label_change() {
''',
)

print("delete-integrity audit patch applied")
