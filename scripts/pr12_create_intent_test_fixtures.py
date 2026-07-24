from pathlib import Path

p = Path('crates/daemon/src/api/runners.rs')
s = p.read_text()

old = '''    #[tokio::test]
    async fn test_update_runner() {
        let state = AppState::new_test_authenticated();

        // Create
        let app = create_router(state.clone());
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/runners")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{\"repo_full_name\":\"aGallea/gifted\"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let runner: serde_json::Value = serde_json::from_slice(&body).unwrap();
        let id = runner["config"]["id"].as_str().unwrap();

        // Update labels
        let app = create_router(state);
'''
new = '''    #[tokio::test]
    async fn test_update_runner() {
        let state = AppState::new_test_authenticated();
        let runner = state
            .runner_manager
            .create("aGallea/gifted", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id;

        // Update labels on a stopped fixture. POST /runners intentionally starts
        // asynchronously and is covered by dedicated create/start tests.
        let app = create_router(state);
'''
assert s.count(old) == 1
s = s.replace(old, new, 1)

old = '''        let state = AppState::new_test_authenticated();
        let id = create_runner_and_get_id(&state).await;

        // Manually transition to Offline so restart is valid.
'''
new = '''        let state = AppState::new_test_authenticated();
        let runner = state
            .runner_manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id;

        // Manually transition a stopped fixture to Offline so restart is valid.
'''
# Exactly one occurrence belongs to the restart test in the nearby block.
restart_start = s.index('    async fn test_restart_runner_in_offline_state_spawns_ok()')
restart_end = s.index('\n    #[tokio::test]', restart_start)
block = s[restart_start:restart_end]
assert block.count(old) == 1
block = block.replace(old, new, 1)
s = s[:restart_start] + block + s[restart_end:]

old = '''    async fn test_update_runner_native_mode_while_stopped() {
        let state = AppState::new_test_authenticated();
        let id = create_runner_and_get_id(&state).await;

        let app = create_router(state);
'''
new = '''    async fn test_update_runner_native_mode_while_stopped() {
        let state = AppState::new_test_authenticated();
        let runner = state
            .runner_manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id;

        let app = create_router(state);
'''
assert s.count(old) == 1
s = s.replace(old, new, 1)

p.write_text(s)
