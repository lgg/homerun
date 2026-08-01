from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    content = target.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "apps/desktop/src-tauri/src/commands.rs",
    '''        .show()
        .map_err(|e| format!("Failed to send notification: {e}"))

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shutdown_errors_only_count_as_stopped_when_health_is_gone() {
        assert_eq!(
            classify_shutdown_error("connection refused", false),
            ShutdownErrorDisposition::AlreadyStopped
        );
        assert_eq!(
            classify_shutdown_error("temporary transport failure", true),
            ShutdownErrorDisposition::Fatal
        );
        assert_eq!(
            classify_shutdown_error("Uninstall the service first", true),
            ShutdownErrorDisposition::ServiceManaged
        );
    }
}

}''',
    '''        .show()
        .map_err(|e| format!("Failed to send notification: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shutdown_errors_only_count_as_stopped_when_health_is_gone() {
        assert_eq!(
            classify_shutdown_error("connection refused", false),
            ShutdownErrorDisposition::AlreadyStopped
        );
        assert_eq!(
            classify_shutdown_error("temporary transport failure", true),
            ShutdownErrorDisposition::Fatal
        );
        assert_eq!(
            classify_shutdown_error("Uninstall the service first", true),
            ShutdownErrorDisposition::ServiceManaged
        );
    }
}''',
)

replace_once(
    "crates/tui/src/daemon_lifecycle.rs",
    '''    tokio::time::sleep(Duration::from_millis(300)).await;
    start_daemon().await

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shutdown_error_classification_is_fail_closed() {
        assert_eq!(
            classify_shutdown_error("connection refused", false),
            ShutdownErrorDisposition::AlreadyStopped
        );
        assert_eq!(
            classify_shutdown_error("transport reset", true),
            ShutdownErrorDisposition::Fatal
        );
        assert_eq!(
            classify_shutdown_error("Daemon is installed as a system service", true),
            ShutdownErrorDisposition::ServiceManaged
        );
    }
}

}''',
    '''    tokio::time::sleep(Duration::from_millis(300)).await;
    start_daemon().await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shutdown_error_classification_is_fail_closed() {
        assert_eq!(
            classify_shutdown_error("connection refused", false),
            ShutdownErrorDisposition::AlreadyStopped
        );
        assert_eq!(
            classify_shutdown_error("transport reset", true),
            ShutdownErrorDisposition::Fatal
        );
        assert_eq!(
            classify_shutdown_error("Daemon is installed as a system service", true),
            ShutdownErrorDisposition::ServiceManaged
        );
    }
}''',
)

print("regression placement fixed")
# synchronize trigger
