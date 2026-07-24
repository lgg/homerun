from __future__ import annotations

import re
from pathlib import Path

PATH = Path("crates/daemon/src/runner/mod.rs")


def replace_once(old: str, new: str) -> None:
    text = PATH.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one exact match, found {count}: {old[:100]!r}")
    PATH.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_count(old: str, new: str, expected: int) -> None:
    text = PATH.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"expected {expected} exact matches, found {count}: {old[:100]!r}")
    PATH.write_text(text.replace(old, new), encoding="utf-8")


def regex_once(pattern: str, replacement: str) -> None:
    text = PATH.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"expected one regex match, found {count}: {pattern[:100]!r}")
    PATH.write_text(updated, encoding="utf-8")


replace_once(
    """struct LifecycleOperations {
    starting: HashSet<String>,
    deleting: HashSet<String>,
}""",
    """struct LifecycleOperations {
    starting: HashSet<String>,
    updating: HashSet<String>,
    deleting: HashSet<String>,
}""",
)

replace_once(
    """        if operations.deleting.contains(id) {
            bail!("Runner '{id}' is being deleted");
        }
        if !operations.starting.insert(id.to_string()) {""",
    """        if operations.deleting.contains(id) {
            bail!("Runner '{id}' is being deleted");
        }
        if operations.updating.contains(id) {
            bail!("Runner '{id}' is being updated");
        }
        if !operations.starting.insert(id.to_string()) {""",
)

replace_once(
    """    async fn begin_delete_operation(&self, id: &str) -> Result<()> {
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

    async fn wait_for_start_to_finish(&self, id: &str) -> Result<()> {""",
    """    async fn begin_update_operation(&self, id: &str) -> Result<()> {
        let mut operations = self.lifecycle_operations.lock().await;
        if operations.deleting.contains(id) {
            bail!("Runner '{id}' is being deleted");
        }
        if operations.starting.contains(id) {
            bail!("Runner '{id}' has a start operation in progress");
        }
        if !self.runners.read().await.contains_key(id) {
            bail!("Runner not found");
        }
        if !operations.updating.insert(id.to_string()) {
            bail!("Runner '{id}' already has an update in progress");
        }
        Ok(())
    }

    async fn finish_update_operation(&self, id: &str) {
        self.lifecycle_operations.lock().await.updating.remove(id);
    }

    async fn begin_delete_operation(&self, id: &str) -> Result<()> {
        let mut operations = self.lifecycle_operations.lock().await;
        if !self.runners.read().await.contains_key(id) {
            bail!("Runner not found");
        }
        if !operations.deleting.insert(id.to_string()) {
            bail!("Runner '{id}' already has a deletion in progress");
        }
        Ok(())
    }

    async fn finish_delete_operation(&self, id: &str) {
        self.lifecycle_operations.lock().await.deleting.remove(id);
    }

    async fn wait_for_mutations_to_finish(&self, id: &str) -> Result<()> {""",
)

replace_once(
    """                if !self.lifecycle_operations.lock().await.starting.contains(id) {
                    break;
                }""",
    """                let operations = self.lifecycle_operations.lock().await;
                if !operations.starting.contains(id) && !operations.updating.contains(id) {
                    break;
                }
                drop(operations);""",
)

replace_once(
    """            anyhow::anyhow!("Timed out waiting for runner '{id}' start operation to finish")""",
    """            anyhow::anyhow!(
                "Timed out waiting for runner '{id}' lifecycle operation to finish"
            )""",
)

replace_count(
    """self.wait_for_start_to_finish(id).await?;""",
    """self.wait_for_mutations_to_finish(id).await?;""",
    2,
)

replace_once(
    """        self.set_desired_running(id, false).await?;
        self.wait_for_mutations_to_finish(id).await?;
        if self.has_active_process(id).await {""",
    """        self.set_desired_running(id, false).await?;
        self.wait_for_mutations_to_finish(id).await?;
        // An already-running start operation may have restored the intent after
        // deletion first cleared it. Clear it again after the reservation drains.
        self.set_desired_running(id, false).await?;
        if self.has_active_process(id).await {""",
)

regex_once(
    r"    pub async fn update\(&self, id: &str, req: types::UpdateRunnerRequest\) -> Result<RunnerInfo> \{\n        let _update_guard = self.update_lock.lock\(\).await;(.*?)\n    \}\n\n    pub async fn update_state",
    r"""    pub async fn update(&self, id: &str, req: types::UpdateRunnerRequest) -> Result<RunnerInfo> {
        let _update_guard = self.update_lock.lock().await;
        self.begin_update_operation(id).await?;
        let result = self.update_reserved(id, req).await;
        self.finish_update_operation(id).await;
        result
    }

    async fn update_reserved(
        &self,
        id: &str,
        req: types::UpdateRunnerRequest,
    ) -> Result<RunnerInfo> {
\1
    }

    pub async fn update_state""",
)

replace_once(
    """        let start_in_progress = self.lifecycle_operations.lock().await.starting.contains(id);

""",
    "",
)
replace_count(
    """if start_in_progress || !stopped""",
    """if !stopped""",
    1,
)
replace_count(
    """if normalized_labels.is_some() && (start_in_progress || !stopped)""",
    """if normalized_labels.is_some() && !stopped""",
    1,
)

replace_once(
    """    #[tokio::test]
    async fn test_update_rejects_mode_change_and_active_label_change() {""",
    """    #[tokio::test]
    async fn test_update_reservation_blocks_start_and_delete_waits_for_update() {
        let manager = create_test_manager();
        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id;

        manager.begin_update_operation(&id).await.unwrap();
        assert!(manager.begin_start_operation(&id).await.is_err());
        manager.begin_delete_operation(&id).await.unwrap();

        let waiter = {
            let manager = manager.clone();
            let id = id.clone();
            tokio::spawn(async move { manager.wait_for_mutations_to_finish(&id).await })
        };
        tokio::task::yield_now().await;
        assert!(!waiter.is_finished());

        manager.finish_update_operation(&id).await;
        waiter.await.unwrap().unwrap();
        manager.finish_delete_operation(&id).await;
    }

    #[tokio::test]
    async fn test_delete_clears_intent_restored_by_inflight_start() {
        let manager = create_test_manager();
        let runner = manager
            .create("owner/repo", None, None, None, None, None)
            .await
            .unwrap();
        let id = runner.config.id;
        manager.begin_start_operation(&id).await.unwrap();
        manager.set_desired_running(&id, true).await.unwrap();

        let deletion = {
            let manager = manager.clone();
            let id = id.clone();
            tokio::spawn(async move { manager.delete(&id).await })
        };
        loop {
            if manager
                .lifecycle_operations
                .lock()
                .await
                .deleting
                .contains(&id)
            {
                break;
            }
            tokio::task::yield_now().await;
        }

        // Simulate the already-admitted start restoring desired-running after
        // deletion's first clear, then allow that start operation to drain.
        manager.set_desired_running(&id, true).await.unwrap();
        manager.finish_start_operation(&id).await;
        deletion.await.unwrap().unwrap();

        assert!(!manager.is_desired_running(&id).await);
        assert!(manager.get(&id).await.is_none());
    }

    #[tokio::test]
    async fn test_update_rejects_mode_change_and_active_label_change() {""",
)
