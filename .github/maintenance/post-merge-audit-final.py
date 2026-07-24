from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Preserve the existing, useful App <-> Service edit while preventing unsafe
# changes across the native/container boundary and while a process is active.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''        let normalized_labels = req.labels.map(types::normalize_labels).transpose()?;
        let display_name = match req.display_name {
            Some(value) => Some(types::normalize_display_name(value)?),
            None => None,
        };
        let start_in_progress = self.starting.read().await.contains(id);''',
    '''        let normalized_labels = req.labels.map(types::normalize_labels).transpose()?;
        let requested_mode = req.mode;
        let display_name = match req.display_name {
            Some(value) => Some(types::normalize_display_name(value)?),
            None => None,
        };
        let start_in_progress = self.starting.read().await.contains(id);''',
)

replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''            if let Some(requested_mode) = req.mode {
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
            if let Some(display_name) = display_name {''',
    '''            let stopped = matches!(
                runner.state,
                RunnerState::Creating | RunnerState::Offline | RunnerState::Error
            );
            if let Some(ref requested_mode) = requested_mode {
                if requested_mode != &runner.config.mode {
                    if start_in_progress || !stopped {
                        bail!("Runner mode can only be changed while the runner is stopped");
                    }
                    if *requested_mode == RunnerMode::Container
                        || runner.config.mode == RunnerMode::Container
                    {
                        bail!(
                            "Container execution mode cannot be changed after creation; create a new runner instead"
                        );
                    }
                }
            }
            if normalized_labels.is_some() && (start_in_progress || !stopped) {
                bail!("Runner labels can only be changed while the runner is stopped");
            }

            let previous = runner.clone();
            if let Some(labels) = normalized_labels {
                runner.config.labels = labels;
            }
            if let Some(requested_mode) = requested_mode {
                runner.config.mode = requested_mode;
            }
            if let Some(display_name) = display_name {''',
)

# Reject stale process handles before any download, deregistration or new child
# process can be created. This makes the defensive publication collision path
# unreachable during normal operation.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''        let runner = self
            .get(id)
            .await
            .ok_or_else(|| anyhow::anyhow!("Runner not found"))?;
        let config = &runner.config;

        // Check both .runner and .runner_migrated''',
    '''        let runner = self
            .get(id)
            .await
            .ok_or_else(|| anyhow::anyhow!("Runner not found"))?;
        if runner.state != RunnerState::Registering {
            bail!(
                "Runner '{id}' must be Registering before start, found {:?}",
                runner.state
            );
        }
        if self.has_active_process(id).await {
            bail!("Runner '{id}' already has an active process");
        }
        let config = &runner.config;

        // Check both .runner and .runner_migrated''',
)

# Restore the legacy native App -> Service update test and keep the new
# container-boundary safety regression in the focused audit test.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''    #[tokio::test]
    async fn test_update_rejects_mode_change() {
        let dir = tempfile::tempdir().unwrap();
        let config = Config::with_base_dir(dir.path().join(".homerun"));
        config.ensure_dirs().unwrap();
        let manager = RunnerManager::new(config);

        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id.clone();

        let result = manager
            .update(
                &id,
                crate::runner::types::UpdateRunnerRequest {
                    labels: None,
                    mode: Some(crate::runner::types::RunnerMode::Service),
                    display_name: None,
                },
            )
            .await;

        assert!(result.is_err());
        let unchanged = manager.get(&id).await.unwrap();
        assert_eq!(unchanged.config.mode, crate::runner::types::RunnerMode::App);
    }
''',
    '''    #[tokio::test]
    async fn test_update_native_mode_while_stopped() {
        let dir = tempfile::tempdir().unwrap();
        let config = Config::with_base_dir(dir.path().join(".homerun"));
        config.ensure_dirs().unwrap();
        let manager = RunnerManager::new(config);

        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id.clone();

        let updated = manager
            .update(
                &id,
                crate::runner::types::UpdateRunnerRequest {
                    labels: None,
                    mode: Some(crate::runner::types::RunnerMode::Service),
                    display_name: None,
                },
            )
            .await
            .unwrap();

        assert_eq!(updated.config.mode, crate::runner::types::RunnerMode::Service);
    }
''',
)

replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''        let label_result = manager
            .update(
                &id,
                types::UpdateRunnerRequest {
                    labels: Some(vec!["changed".to_string()]),
                    mode: None,
                    display_name: None,
                },
            )
            .await;
        assert!(label_result.is_err());''',
    '''        let label_result = manager
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

        let mode_result = manager
            .update(
                &id,
                types::UpdateRunnerRequest {
                    labels: None,
                    mode: Some(RunnerMode::Service),
                    display_name: None,
                },
            )
            .await;
        assert!(mode_result.is_err());''',
)

# The API must continue to support stopped native App -> Service updates.
replace_once(
    "crates/daemon/src/api/runners.rs",
    '''    #[tokio::test]
    async fn test_update_runner_rejects_mode_change() {
        let state = AppState::new_test_authenticated();
        let id = create_runner_and_get_id(&state).await;

        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .method("PATCH")
                    .uri(format!("/runners/{id}"))
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"mode":"service"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::CONFLICT);
    }
''',
    '''    #[tokio::test]
    async fn test_update_runner_native_mode_while_stopped() {
        let state = AppState::new_test_authenticated();
        let id = create_runner_and_get_id(&state).await;

        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .method("PATCH")
                    .uri(format!("/runners/{id}"))
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"mode":"service"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);

        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let updated: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(updated["config"]["mode"], "service");
    }
''',
)

# Clearing the parent preselection while the open dialog stays mounted must
# return to repository selection rather than retaining the previous repository.
replace_once(
    "apps/desktop/src/components/NewRunnerWizard.tsx",
    '''    if (!preselectedRepo) {
      setResolvedPreselectFor(null);
      return;
    }''',
    '''    if (!preselectedRepo) {
      if (resolvedPreselectFor !== null) {
        setSelectedRepo(null);
        setName("");
        setStep(0);
      }
      setResolvedPreselectFor(null);
      return;
    }''',
)

replace_once(
    "apps/desktop/src/components/NewRunnerWizard.test.tsx",
    '''  it("falls back to repository selection when a preselected repository is stale", async () => {''',
    '''  it("returns to repository selection when preselection is cleared", async () => {
    const { rerender, props } = await renderWizard({ preselectedRepo: "org/frontend" });
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();

    rerender(
      <AuthProvider>
        <NewRunnerWizard {...props} preselectedRepo={undefined} />
      </AuthProvider>,
    );
    expect(await screen.findByPlaceholderText("Search repositories...")).toBeInTheDocument();
  });

  it("falls back to repository selection when a preselected repository is stale", async () => {''',
)

print("Final semantic audit corrections applied")
