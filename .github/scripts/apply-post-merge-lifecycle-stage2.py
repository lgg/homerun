from __future__ import annotations

import re
from pathlib import Path

RUNNER = Path("crates/daemon/src/runner/mod.rs")
GROUPS = Path("crates/daemon/src/api/groups.rs")
SERVER = Path("crates/daemon/src/server.rs")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path}, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"expected one regex match in {path}, found {count}: {pattern[:120]!r}")
    path.write_text(updated, encoding="utf-8")


# Remove the old public helper whose contract allowed callers to publish
# Registering before obtaining a start reservation.
replace_once(
    RUNNER,
    '''    /// Start a runner that is already in the Registering state.
    /// Used by the start/restart API endpoints.
    pub async fn register_and_start_from_registering(
        &self,
        id: &str,
        auth_token: &str,
    ) -> Result<()> {
        self.begin_start_operation(id).await?;

        let result = async {
            self.set_desired_running(id, true).await?;
            self.emit_state_event(id, "registering");
            self.do_register_and_start(id, auth_token).await
        }
        .await;
        self.finish_start_operation(id).await;
        result
    }

''',
    '',
)

# Startup restoration must reserve before exposing Registering, just like the
# single-runner API path.
replace_once(
    SERVER,
    '''                tokio::spawn(async move {
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
                        tracing::error!("Failed to restore runner {}: {}", runner_id, e);
                        let _ = manager
                            .update_state_with_error(
                                &runner_id,
                                crate::runner::state::RunnerState::Error,
                                Some(format!("{e:#}")),
                            )
                            .await;
                        manager.schedule_recovery(runner_id);
                    }
                });''',
    '''                tokio::spawn(async move {
                    if let Err(error) = manager.begin_start_operation(&runner_id).await {
                        tracing::warn!(
                            runner = %runner_id,
                            error = %error,
                            "Startup restore deferred by another lifecycle operation"
                        );
                        manager.schedule_recovery(runner_id);
                        return;
                    }

                    if let Err(error) = manager.start_existing_reserved(&runner_id, &token).await {
                        tracing::error!(
                            runner = %runner_id,
                            error = %error,
                            "Failed to restore runner"
                        );
                        let _ = manager
                            .update_state_with_error(
                                &runner_id,
                                crate::runner::state::RunnerState::Error,
                                Some(format!("{error:#}")),
                            )
                            .await;
                        manager.schedule_recovery(runner_id.clone());
                    }
                    manager.finish_start_operation(&runner_id).await;
                });''',
)

# Group start reports success only after admission and retains the reservation
# through the complete background start.
regex_once(
    GROUPS,
    r'''    let mut results = Vec::new\(\);\n    for runner in &runners \{.*?\n    \}\n\n    Ok\(Json\(GroupActionResponse \{ group_id, results \}\)\)\n\}\n\npub async fn stop_group''',
    '''    let mut results = Vec::new();
    for runner in &runners {
        let id = runner.config.id.clone();
        if (runner.state == RunnerState::Offline || runner.state == RunnerState::Error)
            && !state.runner_manager.has_active_process(&id).await
        {
            match state.runner_manager.begin_start_operation(&id).await {
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
                Err(error) => results.push(GroupActionResult {
                    runner_id: id,
                    success: false,
                    error: Some(error.to_string()),
                }),
            }
        } else {
            results.push(GroupActionResult {
                runner_id: id,
                success: false,
                error: Some(format!(
                    "Runner is in {:?} state, cannot start",
                    runner.state
                )),
            });
        }
    }

    Ok(Json(GroupActionResponse { group_id, results }))
}

pub async fn stop_group''',
)

# Group restart now reserves each accepted runner synchronously and returns a
# truthful per-runner result instead of optimistically reporting every runner as
# accepted before the background task examines state.
regex_once(
    GROUPS,
    r'''pub async fn restart_group\(.*?\n\}\n\npub async fn scale_group''',
    '''pub async fn restart_group(
    State(state): State<AppState>,
    Path(group_id): Path<String>,
) -> Result<Json<GroupActionResponse>, (StatusCode, String)> {
    let runners = state.runner_manager.list_by_group(&group_id).await;
    if runners.is_empty() {
        return Err((
            StatusCode::NOT_FOUND,
            format!("No runners found for group '{group_id}'"),
        ));
    }

    let token = state.auth.token().await.ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            "No auth token available. Please authenticate first.".to_string(),
        )
    })?;

    let mut results = Vec::new();
    for runner in &runners {
        let id = runner.config.id.clone();
        if !matches!(
            runner.state,
            RunnerState::Online | RunnerState::Busy | RunnerState::Offline | RunnerState::Error
        ) {
            results.push(GroupActionResult {
                runner_id: id,
                success: false,
                error: Some(format!(
                    "Runner is in {:?} state, cannot restart",
                    runner.state
                )),
            });
            continue;
        }
        if matches!(runner.state, RunnerState::Offline | RunnerState::Error)
            && state.runner_manager.has_active_process(&id).await
        {
            results.push(GroupActionResult {
                runner_id: id,
                success: false,
                error: Some(
                    "Runner still has an active process and must be stopped before restarting"
                        .to_string(),
                ),
            });
            continue;
        }

        match state.runner_manager.begin_start_operation(&id).await {
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
    }

    Ok(Json(GroupActionResponse { group_id, results }))
}

pub async fn scale_group''',
)

# Regression coverage for group admission: a reserved stop must prevent both
# start and restart from reporting an accepted operation or changing state.
replace_once(
    GROUPS,
    '''    #[tokio::test]
    async fn test_group_action_404_for_nonexistent_group() {''',
    '''    #[tokio::test]
    async fn test_group_start_and_restart_report_lifecycle_conflicts() {
        let state = AppState::new_test_authenticated();
        let group_id = "reserved-group".to_string();
        let runner = state
            .runner_manager
            .create(
                "owner/repo",
                None,
                None,
                None,
                Some(group_id.clone()),
                None,
            )
            .await
            .unwrap();
        let id = runner.config.id;
        state
            .runner_manager
            .update_state(&id, RunnerState::Error)
            .await
            .unwrap();
        state.runner_manager.begin_stop_operation(&id).await.unwrap();

        for action in ["start", "restart"] {
            let app = create_router(state.clone());
            let response = app
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri(format!("/runners/groups/{group_id}/{action}"))
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::OK);
            let body = axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .unwrap();
            let response: serde_json::Value = serde_json::from_slice(&body).unwrap();
            assert_eq!(response["results"][0]["success"], false);
            assert!(response["results"][0]["error"]
                .as_str()
                .unwrap()
                .contains("stop operation"));
            assert_eq!(
                state.runner_manager.get(&id).await.unwrap().state,
                RunnerState::Error
            );
        }

        state.runner_manager.finish_stop_operation(&id).await;
    }

    #[tokio::test]
    async fn test_group_action_404_for_nonexistent_group() {''',
)

# The unsafe helper must have no remaining call sites or definition.
remaining = []
for rust_path in Path("crates").rglob("*.rs"):
    if "register_and_start_from_registering" in rust_path.read_text(encoding="utf-8"):
        remaining.append(str(rust_path))
if remaining:
    raise RuntimeError(
        "unsafe register_and_start_from_registering references remain: " + ", ".join(remaining)
    )
