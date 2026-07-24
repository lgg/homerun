from pathlib import Path

path = Path("crates/daemon/src/runner/mod.rs")
text = path.read_text(encoding="utf-8")
old = '''    #[tokio::test]
    async fn test_create_rejects_duplicate_name_case_insensitive_across_repos() {
        let manager = create_test_manager();
        // Same name (different case), different repo — still rejected: runner
        // names must be globally unique (Docker container name is repo-agnostic).
        manager
            .create(
                "owner/repo-a",
                Some("My-Runner".to_string()),
                None,
                None,
                None,
                None,
            )
            .await
            .unwrap();
        let err = manager
            .create(
                "owner/repo-b",
                Some("my-runner".to_string()),
                None,
                None,
                None,
                None,
            )
            .await
            .unwrap_err();
        assert!(
            err.to_string().contains("already exists"),
            "unexpected error: {err}"
        );
    }
'''
new = '''    #[tokio::test]
    async fn test_create_rejects_duplicate_name_case_insensitive_within_repo() {
        let manager = create_test_manager();
        // GitHub runner names are scoped to a repository. Case-insensitive
        // duplicates must still be rejected inside that repository.
        manager
            .create(
                "owner/repo",
                Some("My-Runner".to_string()),
                None,
                None,
                None,
                None,
            )
            .await
            .unwrap();
        let err = manager
            .create(
                "owner/repo",
                Some("my-runner".to_string()),
                None,
                None,
                None,
                None,
            )
            .await
            .unwrap_err();
        assert!(
            err.to_string().contains("already exists"),
            "unexpected error: {err}"
        );
    }
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"expected one stale duplicate-name test, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
