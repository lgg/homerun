from __future__ import annotations

import re
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one exact match in {path}, found {count}: {old[:80]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_count(path: str, old: str, new: str, expected: int) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"Expected {expected} exact matches in {path}, found {count}: {old[:80]!r}")
    file_path.write_text(text.replace(old, new), encoding="utf-8")


def regex_once(path: str, pattern: str, replacement: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"Expected one regex match in {path}, found {count}: {pattern[:100]!r}")
    file_path.write_text(updated, encoding="utf-8")


RUNNER = "crates/daemon/src/runner/mod.rs"

replace_once(
    RUNNER,
    """struct ProcessHandle {
    /// Signal the monitoring task to kill the child process.
    kill_signal: Arc<Notify>,
    /// Becomes `true` once the child process has fully exited.
    exited: watch::Receiver<bool>,
}

#[derive(Clone)]
pub struct RunnerManager {""",
    """struct ProcessHandle {
    /// Signal the monitoring task to kill the child process.
    kill_signal: Arc<Notify>,
    /// Becomes `true` once the child process has fully exited.
    exited: watch::Receiver<bool>,
}

#[derive(Default)]
struct LifecycleOperations {
    starting: HashSet<String>,
    deleting: HashSet<String>,
}

#[derive(Clone)]
pub struct RunnerManager {""",
)

replace_once(
    RUNNER,
    """    /// Runner IDs currently inside the registration/start pipeline. Deletion
    /// waits for this pipeline to finish before touching the work directory.
    starting: Arc<RwLock<HashSet<String>>>,
    /// Serialize persistence writes. Multiple async lifecycle tasks can request
    /// a save concurrently, and unsynchronized writes can truncate runners.json.
    persistence_lock: Arc<Mutex<()>>,""",
    """    /// Coordinate start and delete reservations under one lock. A deletion
    /// blocks new starts before waiting for an already-running start pipeline.
    lifecycle_operations: Arc<Mutex<LifecycleOperations>>,
    /// Serialize configuration PATCH operations through their persistence commit,
    /// so a failed write can never roll back a newer same-valued update.
    update_lock: Arc<Mutex<()>>,
    /// Serialize persistence writes. Multiple async lifecycle tasks can request
    /// a save concurrently, and unsynchronized writes can truncate runners.json.
    persistence_lock: Arc<Mutex<()>>,""",
)

replace_once(
    RUNNER,
    """            starting: Arc::new(RwLock::new(HashSet::new())),
            persistence_lock: Arc::new(Mutex::new(())),""",
    """            lifecycle_operations: Arc::new(Mutex::new(LifecycleOperations::default())),
            update_lock: Arc::new(Mutex::new(())),
            persistence_lock: Arc::new(Mutex::new(())),""",
)

replace_once(
    RUNNER,
    """            if runners.values().any(|existing| {
                existing
                    .config
                    .name
                    .eq_ignore_ascii_case(&runner.config.name)
            }) {
                drop(runners);
                let _ = std::fs::remove_dir_all(&runner.config.work_dir);
                bail!("A runner named '{}' already exists", runner.config.name);
            }""",
    """            if runners.values().any(|existing| {
                let same_name = existing
                    .config
                    .name
                    .eq_ignore_ascii_case(&runner.config.name);
                let same_repository = existing
                    .config
                    .repo_owner
                    .eq_ignore_ascii_case(owner)
                    && existing.config.repo_name.eq_ignore_ascii_case(repo);
                let docker_name_collision = existing.config.mode == RunnerMode::Container
                    && runner.config.mode == RunnerMode::Container;
                same_name && (same_repository || docker_name_collision)
            }) {
                drop(runners);
                let _ = std::fs::remove_dir_all(&runner.config.work_dir);
                bail!("A conflicting runner named '{}' already exists", runner.config.name);
            }""",
)

regex_once(
    RUNNER,
    r"    async fn wait_for_start_to_finish\(&self, id: &str\) -> Result<\(\)> \{.*?\n    \}\n\n    pub async fn delete",
    """    async fn begin_start_operation(&self, id: &str) -> Result<()> {
        let mut operations = self.lifecycle_operations.lock().await;
        if operations.deleting.contains(id) {
            bail!("Runner '{id}' is being deleted");
        }
        if !operations.starting.insert(id.to_string()) {
            bail!("Runner '{id}' already has a start operation in progress");
        }
        Ok(())
    }

    async fn finish_start_operation(&self, id: &str) {
        self.lifecycle_operations.lock().await.starting.remove(id);
    }

    async fn begin_delete_operation(&self, id: &str) -> Result<()> {
        if !self.runners.read().await.contains_key(id) {
            bail!("Runner not found");
        }
        let mut operations = self.lifecycle_operations.lock().await;
        if !operations.deleting.insert(id.to_string()) {
            bail!("Runner '{id}' already has a deletion in progress");
        }
        Ok(())
    }

    async fn finish_delete_operation(&self, id: &str) {
        self.lifecycle_operations.lock().await.deleting.remove(id);
    }

    async fn wait_for_start_to_finish(&self, id: &str) -> Result<()> {
        tokio::time::timeout(std::time::Duration::from_secs(60), async {
            loop {
                if !self.lifecycle_operations.lock().await.starting.contains(id) {
                    break;
                }
                tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            }
        })
        .await
        .map_err(|_| {
            anyhow::anyhow!("Timed out waiting for runner '{id}' start operation to finish")
        })?;
        Ok(())
    }

    pub async fn delete""",
)

regex_once(
    RUNNER,
    r"    pub async fn delete\(&self, id: &str\) -> Result<\(\)> \{(.*?)\n    \}\n\n    pub async fn update",
    r"""    pub async fn delete(&self, id: &str) -> Result<()> {
        self.begin_delete_operation(id).await?;
        let result = self.delete_reserved(id).await;
        self.finish_delete_operation(id).await;
        result
    }

    async fn delete_reserved(&self, id: &str) -> Result<()> {\1
    }

    pub async fn update""",
)

replace_once(
    RUNNER,
    """    pub async fn update(&self, id: &str, req: types::UpdateRunnerRequest) -> Result<RunnerInfo> {
        let normalized_labels = req.labels.map(types::normalize_labels).transpose()?;""",
    """    pub async fn update(&self, id: &str, req: types::UpdateRunnerRequest) -> Result<RunnerInfo> {
        let _update_guard = self.update_lock.lock().await;
        let normalized_labels = req.labels.map(types::normalize_labels).transpose()?;""",
)

replace_once(
    RUNNER,
    """        let start_in_progress = self.starting.read().await.contains(id);""",
    """        let start_in_progress = self
            .lifecycle_operations
            .lock()
            .await
            .starting
            .contains(id);""",
)

start_guard_old = """        {
            let mut starting = self.starting.write().await;
            if !starting.insert(id.to_string()) {
                bail!("Runner '{id}' already has a start operation in progress");
            }
        }
"""
replace_count(
    RUNNER,
    start_guard_old,
    """        self.begin_start_operation(id).await?;
""",
    2,
)
replace_count(
    RUNNER,
    """        self.starting.write().await.remove(id);
""",
    """        self.finish_start_operation(id).await;
""",
    2,
)

regex_once(
    RUNNER,
    r"    pub async fn full_delete\(&self, id: &str, auth_token: &str\) -> Result<\(\)> \{(.*?)\n        // Remove runner entry and work dir\n        self\.delete\(id\)\.await\?;\n        Ok\(\(\)\)\n    \}",
    r"""    pub async fn full_delete(&self, id: &str, auth_token: &str) -> Result<()> {
        self.begin_delete_operation(id).await?;
        let result = self.full_delete_reserved(id, auth_token).await;
        self.finish_delete_operation(id).await;
        result
    }

    async fn full_delete_reserved(&self, id: &str, auth_token: &str) -> Result<()> {\1
        // Remove runner entry and work dir while retaining the deletion reservation.
        self.delete_reserved(id).await?;
        Ok(())
    }""",
)

replace_once(
    RUNNER,
    """    #[tokio::test]
    async fn test_update_rejects_mode_change_and_active_label_change() {""",
    """    #[tokio::test]
    async fn test_same_native_runner_name_is_allowed_across_repositories() {
        let manager = create_test_manager();
        manager
            .create(
                "owner/one",
                Some("shared-name".to_string()),
                None,
                None,
                None,
                None,
            )
            .await
            .unwrap();
        manager
            .create(
                "owner/two",
                Some("shared-name".to_string()),
                None,
                None,
                None,
                None,
            )
            .await
            .unwrap();
        assert_eq!(manager.list().await.len(), 2);
    }

    #[tokio::test]
    async fn test_same_container_runner_name_is_rejected_across_repositories() {
        let manager = create_test_manager();
        let container = types::ContainerConfig {
            image: "ghcr.io/agallea/homerun-runner:ubuntu-24.04".to_string(),
            extra_env: vec![],
        };
        manager
            .create(
                "owner/one",
                Some("shared-name".to_string()),
                None,
                Some(RunnerMode::Container),
                None,
                Some(container.clone()),
            )
            .await
            .unwrap();
        let duplicate = manager
            .create(
                "owner/two",
                Some("shared-name".to_string()),
                None,
                Some(RunnerMode::Container),
                None,
                Some(container),
            )
            .await;
        assert!(duplicate.is_err());
        assert_eq!(manager.list().await.len(), 1);
    }

    #[tokio::test]
    async fn test_delete_reservation_blocks_new_start_operations() {
        let manager = create_test_manager();
        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id;

        manager.begin_delete_operation(&id).await.unwrap();
        assert!(manager.begin_start_operation(&id).await.is_err());
        manager.finish_delete_operation(&id).await;

        manager.begin_start_operation(&id).await.unwrap();
        manager.finish_start_operation(&id).await;
    }

    #[tokio::test]
    async fn test_update_rejects_mode_change_and_active_label_change() {""",
)

for workflow in [
    ".github/workflows/ci.yml",
    ".github/workflows/coverage-badge.yml",
    ".github/workflows/release-please.yml",
]:
    replace_once(
        workflow,
        """concurrency:
  group: homerun-ci-${{ github.repository }}
  cancel-in-progress: false""",
        """concurrency:
  group: homerun-ci-${{ github.repository }}
  cancel-in-progress: false
  queue: max""",
    )

replace_once(
    ".github/workflows/ci.yml",
    """  quality:
    name: Full quality gate
    runs-on: homerun-ci""",
    """  quality:
    # Never execute untrusted fork code on the persistent self-hosted machine.
    if: ${{ github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository }}
    name: Full quality gate
    runs-on: homerun-ci""",
)
