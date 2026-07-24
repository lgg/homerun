use axum::{
    extract::{Path, State},
    http::StatusCode,
    Json,
};

use crate::runner::state::RunnerState;
use crate::runner::types::{
    BatchCreateResponse, CreateBatchRequest, GroupActionResponse, GroupActionResult,
    ScaleGroupRequest, ScaleGroupResponse,
};
use crate::server::AppState;

pub async fn create_batch(
    State(state): State<AppState>,
    Json(req): Json<CreateBatchRequest>,
) -> Result<(StatusCode, Json<BatchCreateResponse>), (StatusCode, String)> {
    if req.count < 2 || req.count > 10 {
        return Err((
            StatusCode::BAD_REQUEST,
            "count must be between 2 and 10".to_string(),
        ));
    }

    crate::runner::RunnerManager::validate_create_request(
        &req.repo_full_name,
        None,
        req.mode.as_ref(),
        req.container.as_ref(),
    )
    .map_err(|error| (StatusCode::BAD_REQUEST, error.to_string()))?;

    // Authenticate before persisting any Creating runners. Otherwise a 401
    // leaves an orphaned batch that can never start.
    let token = state.auth.token().await.ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            "No auth token available. Please authenticate first.".to_string(),
        )
    })?;

    let (group_id, runners, errors) = state
        .runner_manager
        .create_batch(
            &req.repo_full_name,
            req.count,
            req.labels,
            req.mode,
            req.container,
        )
        .await
        .map_err(|e| (StatusCode::BAD_REQUEST, e.to_string()))?;

    // Spawn background registration for each runner.
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

    let status = if errors.is_empty() {
        StatusCode::CREATED
    } else {
        StatusCode::MULTI_STATUS
    };

    Ok((
        status,
        Json(BatchCreateResponse {
            group_id,
            runners,
            errors,
        }),
    ))
}

pub async fn start_group(
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

pub async fn stop_group(
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

    let mut results = Vec::new();
    for runner in &runners {
        let id = runner.config.id.clone();
        if runner.state == RunnerState::Online
            || runner.state == RunnerState::Busy
            || state.runner_manager.has_active_process(&id).await
        {
            match state.runner_manager.stop_process(&id).await {
                Ok(_) => results.push(GroupActionResult {
                    runner_id: id,
                    success: true,
                    error: None,
                }),
                Err(e) => results.push(GroupActionResult {
                    runner_id: id,
                    success: false,
                    error: Some(format!("Failed to stop runner: {e}")),
                }),
            }
        } else {
            results.push(GroupActionResult {
                runner_id: id,
                success: false,
                error: Some(format!(
                    "Runner is in {:?} state, cannot stop",
                    runner.state
                )),
            });
        }
    }

    Ok(Json(GroupActionResponse { group_id, results }))
}

pub async fn restart_group(
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

pub async fn scale_group(
    State(state): State<AppState>,
    Path(group_id): Path<String>,
    Json(req): Json<ScaleGroupRequest>,
) -> Result<Json<ScaleGroupResponse>, (StatusCode, String)> {
    if req.count < 1 || req.count > 10 {
        return Err((
            StatusCode::BAD_REQUEST,
            "count must be between 1 and 10".to_string(),
        ));
    }

    let existing = state.runner_manager.list_by_group(&group_id).await;
    if existing.is_empty() {
        return Err((
            StatusCode::NOT_FOUND,
            format!("No runners found for group '{group_id}'"),
        ));
    }

    // Scaling up requires authentication and must fail before creating local
    // runner records. Scaling down can still proceed locally while logged out.
    let token = if req.count as usize > existing.len() {
        Some(state.auth.token().await.ok_or_else(|| {
            (
                StatusCode::UNAUTHORIZED,
                "No auth token available. Please authenticate first.".to_string(),
            )
        })?)
    } else {
        state.auth.token().await
    };

    let response = state
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
}

pub async fn delete_group(
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

    let token = state.auth.token().await;
    let mut results = Vec::new();
    let mut deletions = Vec::new();

    for runner in &runners {
        let id = runner.config.id.clone();

        if matches!(
            runner.state,
            RunnerState::Busy | RunnerState::Stopping | RunnerState::Deleting
        ) {
            results.push(GroupActionResult {
                runner_id: id,
                success: false,
                error: Some(format!(
                    "Runner is in {:?} state; wait for the current lifecycle operation before deleting",
                    runner.state
                )),
            });
            continue;
        }

        let manager = state.runner_manager.clone();
        let runner_id = id.clone();
        let token = token.clone();
        deletions.push(async move {
            let result = if let Some(ref token) = token {
                manager.full_delete(&runner_id, token).await
            } else {
                // Local deletion still waits for registration and stops any
                // process safely; it simply cannot deregister from GitHub.
                manager.delete(&runner_id).await
            };
            (runner_id, result)
        });
    }

    for (runner_id, result) in futures::future::join_all(deletions).await {
        match result {
            Ok(()) => results.push(GroupActionResult {
                runner_id,
                success: true,
                error: None,
            }),
            Err(error) => {
                tracing::error!("Failed to delete runner {}: {}", runner_id, error);
                results.push(GroupActionResult {
                    runner_id,
                    success: false,
                    error: Some(format!("Failed to delete runner: {error}")),
                });
            }
        }
    }

    Ok(Json(GroupActionResponse { group_id, results }))
}

#[cfg(test)]
mod tests {
    use crate::server::{create_router, AppState};
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use tower::ServiceExt;

    #[tokio::test]
    async fn test_batch_create_returns_group_id_and_runners() {
        let state = AppState::new_test_authenticated();
        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners/batch")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"repo_full_name":"owner/myrepo","count":3}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::CREATED);

        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let resp: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert!(resp["group_id"].is_string());
        assert_eq!(resp["runners"].as_array().unwrap().len(), 3);
        assert_eq!(resp["errors"].as_array().unwrap().len(), 0);

        let gid = resp["group_id"].as_str().unwrap();
        for runner in resp["runners"].as_array().unwrap() {
            assert_eq!(runner["config"]["group_id"].as_str().unwrap(), gid);
        }
    }

    #[tokio::test]
    async fn test_batch_create_auto_names_with_counter() {
        let state = AppState::new_test_authenticated();
        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners/batch")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"repo_full_name":"owner/myrepo","count":2}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let resp: serde_json::Value = serde_json::from_slice(&body).unwrap();
        let names: Vec<&str> = resp["runners"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r["config"]["name"].as_str().unwrap())
            .collect();
        assert_eq!(names, vec!["myrepo-runner-1", "myrepo-runner-2"]);
    }

    #[tokio::test]
    async fn test_batch_create_rejects_count_below_2() {
        let state = AppState::new_test();
        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners/batch")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"repo_full_name":"owner/repo","count":1}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn test_batch_create_rejects_count_above_10() {
        let state = AppState::new_test();
        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners/batch")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"repo_full_name":"owner/repo","count":11}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn test_group_start_returns_results() {
        let state = AppState::new_test_authenticated();
        // Create a batch
        let app = create_router(state.clone());
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners/batch")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"repo_full_name":"owner/repo","count":2}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let batch: serde_json::Value = serde_json::from_slice(&body).unwrap();
        let group_id = batch["group_id"].as_str().unwrap();

        // Start the group (runners are in Creating state, can't start)
        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!("/runners/groups/{group_id}/start"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let resp: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(resp["group_id"].as_str().unwrap(), group_id);
        assert_eq!(resp["results"].as_array().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn test_group_start_and_restart_report_lifecycle_conflicts() {
        let state = AppState::new_test_authenticated();
        let group_id = "reserved-group".to_string();
        let runner = state
            .runner_manager
            .create("owner/repo", None, None, None, Some(group_id.clone()), None)
            .await
            .unwrap();
        let id = runner.config.id;
        state
            .runner_manager
            .update_state(&id, RunnerState::Error)
            .await
            .unwrap();
        state
            .runner_manager
            .begin_stop_operation(&id)
            .await
            .unwrap();

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
    async fn test_group_action_404_for_nonexistent_group() {
        let state = AppState::new_test();
        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners/groups/nonexistent-group/start")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn test_scale_up_adds_runners() {
        let state = AppState::new_test_authenticated();
        let app = create_router(state.clone());
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners/batch")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"repo_full_name":"owner/repo","count":2}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let batch: serde_json::Value = serde_json::from_slice(&body).unwrap();
        let group_id = batch["group_id"].as_str().unwrap();

        let app = create_router(state.clone());
        let response = app
            .oneshot(
                Request::builder()
                    .method("PATCH")
                    .uri(format!("/runners/groups/{group_id}"))
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"count":4}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let resp: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(resp["previous_count"].as_u64().unwrap(), 2);
        assert_eq!(resp["actual_count"].as_u64().unwrap(), 4);
        assert_eq!(resp["added"].as_array().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn test_scale_down_removes_runners() {
        let state = AppState::new_test_authenticated();
        let app = create_router(state.clone());
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners/batch")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"repo_full_name":"owner/repo","count":3}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let batch: serde_json::Value = serde_json::from_slice(&body).unwrap();
        let group_id = batch["group_id"].as_str().unwrap();

        let app = create_router(state.clone());
        let response = app
            .oneshot(
                Request::builder()
                    .method("PATCH")
                    .uri(format!("/runners/groups/{group_id}"))
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"count":1}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let resp: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(resp["previous_count"].as_u64().unwrap(), 3);
        assert_eq!(resp["actual_count"].as_u64().unwrap(), 1);
        assert_eq!(resp["removed"].as_array().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn test_list_runners_filter_by_group_id() {
        let state = AppState::new_test_authenticated();
        // Create a batch
        let app = create_router(state.clone());
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners/batch")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"repo_full_name":"owner/repo","count":2}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let batch: serde_json::Value = serde_json::from_slice(&body).unwrap();
        let group_id = batch["group_id"].as_str().unwrap();

        // Create a solo runner
        let app = create_router(state.clone());
        app.oneshot(
            Request::builder()
                .method("POST")
                .uri("/runners")
                .header("content-type", "application/json")
                .body(Body::from(
                    r#"{"repo_full_name":"owner/repo","name":"solo-runner"}"#,
                ))
                .unwrap(),
        )
        .await
        .unwrap();

        // Filter by group_id
        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .uri(format!("/runners?group_id={group_id}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let runners: Vec<serde_json::Value> = serde_json::from_slice(&body).unwrap();
        assert_eq!(runners.len(), 2);
    }

    #[tokio::test]
    async fn test_group_delete_removes_runners() {
        let state = AppState::new_test_authenticated();
        // Create batch
        let app = create_router(state.clone());
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners/batch")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"repo_full_name":"owner/repo","count":2}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let batch: serde_json::Value = serde_json::from_slice(&body).unwrap();
        let group_id = batch["group_id"].as_str().unwrap();

        // Delete the group
        let app = create_router(state.clone());
        let response = app
            .oneshot(
                Request::builder()
                    .method("DELETE")
                    .uri(format!("/runners/groups/{group_id}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);

        // Wait for background deletion tasks to complete
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;

        // Verify runners are gone
        let app = create_router(state);
        let response = app
            .oneshot(
                Request::builder()
                    .uri("/runners")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let runners: Vec<serde_json::Value> = serde_json::from_slice(&body).unwrap();
        assert_eq!(runners.len(), 0);
    }
}
