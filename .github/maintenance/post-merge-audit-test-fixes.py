from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "crates/daemon/src/api/runners.rs",
    '''    #[tokio::test]
    async fn test_update_runner_mode() {
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
)

replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''    #[tokio::test]
    async fn test_update_mode() {
        let dir = tempfile::tempdir().unwrap();
        let config = Config::with_base_dir(dir.path().join(".homerun"));
        config.ensure_dirs().unwrap();
        let manager = RunnerManager::new(config);

        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id.clone();

        // Default mode is App; update to Service
        manager
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

        let updated = manager.get(&id).await.unwrap();
        assert_eq!(
            updated.config.mode,
            crate::runner::types::RunnerMode::Service
        );
    }
''',
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
)

print("Legacy mode-mutation tests updated for immutable runner modes")
