from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Persist removal before deleting files/history. If persistence fails, restore the
# in-memory entry without resurrecting stale state from disk on the next launch.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''        let mut runners = self.runners.write().await;
        if let Some(runner) = runners.remove(id) {
            let _ = std::fs::remove_dir_all(&runner.config.work_dir);
        }
        drop(runners);
        // Also remove any tracked process handle
        self.processes.write().await.remove(id);
        self.delete_job_history(id).await;
        self.save_to_disk().await?;
        Ok(())''',
    '''        let removed = self.runners.write().await.remove(id);
        if let Err(error) = self.save_to_disk().await {
            if let Some(runner) = removed {
                self.runners.write().await.insert(id.to_string(), runner);
            }
            return Err(error).context("persisting runner deletion");
        }

        // Destructive cleanup happens only after the durable state no longer
        // references this runner. A failed write therefore cannot resurrect a
        // deleted runner with a missing work directory on the next launch.
        self.processes.write().await.remove(id);
        self.delete_job_history(id).await;
        if let Some(runner) = removed {
            let _ = std::fs::remove_dir_all(&runner.config.work_dir);
        }
        Ok(())''',
)

# Roll back only the configuration fields changed by this request. Restoring the
# whole RunnerInfo could overwrite a concurrent state/PID/job transition.
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''            let previous = runner.clone();
            if let Some(labels) = normalized_labels {
                runner.config.labels = labels;
            }
            if let Some(requested_mode) = requested_mode {
                runner.config.mode = requested_mode;
            }
            if let Some(display_name) = display_name {
                runner.config.display_name = display_name;
            }
            previous
        };

        if let Err(error) = self.save_to_disk().await {
            self.runners.write().await.insert(id.to_string(), previous);
            return Err(error).context("persisting runner update");
        }''',
    '''            let previous_config = (
                runner.config.labels.clone(),
                runner.config.mode.clone(),
                runner.config.display_name.clone(),
            );
            if let Some(labels) = normalized_labels {
                runner.config.labels = labels;
            }
            if let Some(requested_mode) = requested_mode {
                runner.config.mode = requested_mode;
            }
            if let Some(display_name) = display_name {
                runner.config.display_name = display_name;
            }
            let applied_config = (
                runner.config.labels.clone(),
                runner.config.mode.clone(),
                runner.config.display_name.clone(),
            );
            (previous_config, applied_config)
        };

        if let Err(error) = self.save_to_disk().await {
            let mut runners = self.runners.write().await;
            if let Some(runner) = runners.get_mut(id) {
                let current_config = (
                    runner.config.labels.clone(),
                    runner.config.mode.clone(),
                    runner.config.display_name.clone(),
                );
                // Do not undo a newer concurrent configuration update. Runtime
                // fields are intentionally never replaced during rollback.
                if current_config == previous.1 {
                    runner.config.labels = previous.0.0;
                    runner.config.mode = previous.0.1;
                    runner.config.display_name = previous.0.2;
                }
            }
            return Err(error).context("persisting runner update");
        }''',
)

print("Deletion durability and config-only rollback hardening applied")
