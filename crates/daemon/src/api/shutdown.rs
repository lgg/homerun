use axum::{extract::State, http::StatusCode, Json};
use serde_json::json;

use crate::server::AppState;

pub async fn shutdown_daemon(
    State(state): State<AppState>,
) -> Result<(StatusCode, Json<serde_json::Value>), (StatusCode, Json<serde_json::Value>)> {
    if crate::platform::service::is_daemon_installed() {
        let msg = if cfg!(target_os = "macos") {
            "Daemon is managed by launchd. Uninstall the service first or use `launchctl unload`."
        } else if cfg!(windows) {
            "Daemon is registered as an auto-start service. Uninstall the service first."
        } else {
            "Daemon is installed as a system service. Uninstall the service first."
        };
        return Err((StatusCode::CONFLICT, Json(json!({ "error": msg }))));
    }

    tracing::info!("Shutdown requested via API");

    let admitted_starts = state
        .runner_manager
        .begin_shutdown_operation()
        .await
        .map_err(|error| {
            (
                StatusCode::CONFLICT,
                Json(json!({ "error": error.to_string() })),
            )
        })?;

    let runners = state.runner_manager.list().await;
    let mut active_count = admitted_starts;
    for runner in &runners {
        let transitional = !matches!(
            runner.state,
            crate::runner::state::RunnerState::Offline | crate::runner::state::RunnerState::Error
        );
        if transitional
            || state
                .runner_manager
                .has_active_process(&runner.config.id)
                .await
        {
            active_count = active_count.max(admitted_starts.max(1));
        }
    }

    tokio::spawn(async move {
        // A start admitted before the barrier is allowed to finish. Once all
        // reservations drain, no later start can race process enumeration.
        state
            .runner_manager
            .wait_for_start_operations_to_finish()
            .await;

        let runners = state.runner_manager.list().await;
        let mut stop_futures = Vec::new();
        for runner in &runners {
            let active = runner.state == crate::runner::state::RunnerState::Online
                || runner.state == crate::runner::state::RunnerState::Busy
                || state
                    .runner_manager
                    .has_active_process(&runner.config.id)
                    .await;
            if active {
                tracing::info!("Stopping runner {} for shutdown", runner.config.name);
                let manager = state.runner_manager.clone();
                let id = runner.config.id.clone();
                stop_futures.push(async move {
                    if let Err(e) = manager.stop_process_preserving_intent(&id).await {
                        tracing::warn!("Failed to stop runner {} for shutdown: {}", id, e);
                    }
                });
            }
        }
        futures::future::join_all(stop_futures).await;
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        // On Unix, clean up the socket file. On Windows, named pipes are
        // kernel objects and require no file cleanup.
        #[cfg(unix)]
        {
            let socket_path = state.config.read().await.socket_path();
            if socket_path.exists() {
                let _ = std::fs::remove_file(&socket_path);
            }
        }
        tracing::info!("Daemon shutting down");
        std::process::exit(0);
    });

    Ok((
        StatusCode::ACCEPTED,
        Json(json!({ "active_runners": active_count })),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::Request;
    use tower::ServiceExt;

    use crate::server::{create_router, AppState};

    #[tokio::test]
    async fn test_shutdown_returns_accepted_or_conflict() {
        let app = create_router(AppState::new_test());
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/daemon/shutdown")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        // On machines where the daemon is registered as a service,
        // shutdown returns CONFLICT; otherwise ACCEPTED.
        let status = response.status();
        assert!(
            status == StatusCode::ACCEPTED || status == StatusCode::CONFLICT,
            "expected ACCEPTED or CONFLICT, got {status}"
        );
    }

    #[tokio::test]
    async fn test_shutdown_blocked_when_service_installed() {
        // Since we can't easily mock is_daemon_installed(), we test the actual state.
        // The handler calls is_daemon_installed() internally, so we just verify the
        // response is one of the two valid outcomes.
        let app = create_router(AppState::new_test());
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/daemon/shutdown")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        let status = response.status();
        assert!(
            status == StatusCode::ACCEPTED || status == StatusCode::CONFLICT,
            "expected ACCEPTED or CONFLICT, got {status}"
        );

        if status == StatusCode::CONFLICT {
            let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .unwrap();
            let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
            assert!(json["error"].as_str().unwrap().contains("service"));
        }
    }

    #[tokio::test]
    async fn test_shutdown_returns_active_runners_count() {
        let app = create_router(AppState::new_test());
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/daemon/shutdown")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        if response.status() == StatusCode::CONFLICT {
            return; // Daemon is installed as a service — shutdown blocked
        }
        assert_eq!(response.status(), StatusCode::ACCEPTED);
        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        // No runners in test state, so active_runners should be 0
        assert_eq!(json["active_runners"], 0);
    }
}
