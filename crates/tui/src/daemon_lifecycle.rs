use std::time::Duration;

#[cfg(unix)]
use std::path::{Path, PathBuf};
use std::process::Stdio;

use anyhow::{bail, Context, Result};

use crate::client::DaemonClient;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ShutdownErrorDisposition {
    ServiceManaged,
    AlreadyStopped,
    Fatal,
}

fn classify_shutdown_error(message: &str, daemon_healthy: bool) -> ShutdownErrorDisposition {
    if message.contains("launchd")
        || message.contains("Uninstall the service")
        || message.contains("auto-start service")
        || message.contains("system service")
    {
        ShutdownErrorDisposition::ServiceManaged
    } else if daemon_healthy {
        ShutdownErrorDisposition::Fatal
    } else {
        ShutdownErrorDisposition::AlreadyStopped
    }
}

#[cfg(unix)]
fn default_socket_path() -> PathBuf {
    dirs::home_dir()
        .expect("no home directory")
        .join(".homerun/daemon.sock")
}

#[cfg(windows)]
fn default_pipe_name() -> String {
    r"\\.\pipe\homerun-daemon".to_string()
}

#[cfg(unix)]
async fn is_daemon_running(socket: &Path) -> bool {
    if !socket.exists() {
        return false;
    }
    let client = DaemonClient::new(socket.to_path_buf());
    client.health().await.is_ok()
}

#[cfg(windows)]
async fn is_daemon_running() -> bool {
    let client = DaemonClient::new_pipe(default_pipe_name());
    client.health().await.is_ok()
}

pub async fn start_daemon() -> Result<()> {
    #[cfg(unix)]
    {
        let socket = default_socket_path();
        if is_daemon_running(&socket).await {
            bail!("Daemon is already running");
        }
        if socket.exists() {
            std::fs::remove_file(&socket)?;
        }
    }
    #[cfg(windows)]
    {
        if is_daemon_running().await {
            bail!("Daemon is already running");
        }
    }

    let binary = which::which("homerund")
        .context("homerund not found in PATH. Install it or add it to your PATH.")?;
    std::process::Command::new(&binary)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .context("Failed to spawn homerund")?;

    #[cfg(unix)]
    let client = DaemonClient::new(default_socket_path());
    #[cfg(windows)]
    let client = DaemonClient::new_pipe(default_pipe_name());

    let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
    loop {
        if client.health().await.is_ok() {
            return Ok(());
        }
        if tokio::time::Instant::now() >= deadline {
            bail!("Daemon failed to start within 5 seconds — check logs at ~/.homerun/logs/");
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
}

pub async fn stop_daemon() -> Result<()> {
    #[cfg(unix)]
    let socket = default_socket_path();

    #[cfg(unix)]
    if !socket.exists() {
        bail!("Daemon is not running (no socket file)");
    }

    #[cfg(windows)]
    if !is_daemon_running().await {
        bail!("Daemon is not running");
    }

    #[cfg(unix)]
    let client = DaemonClient::new(socket.clone());
    #[cfg(windows)]
    let client = DaemonClient::new_pipe(default_pipe_name());

    let active_runners = match client.shutdown().await {
        Ok(count) => count,
        Err(error) => {
            let message = error.to_string();
            let healthy = client.health().await.is_ok();
            match classify_shutdown_error(&message, healthy) {
                ShutdownErrorDisposition::ServiceManaged => {
                    #[cfg(target_os = "macos")]
                    bail!(
                        "Daemon is managed by launchd. Disable Launch at login first \
                         (Settings > Startup) or run: launchctl unload ~/Library/LaunchAgents/com.homerun.daemon.plist"
                    );
                    #[cfg(windows)]
                    bail!(
                        "Daemon is managed by Windows autostart. Disable Launch at login first \
                         (Settings > Startup)."
                    );
                    #[cfg(all(unix, not(target_os = "macos")))]
                    bail!(
                        "Daemon is managed by a system service. Disable that service before stopping it directly."
                    );
                }
                ShutdownErrorDisposition::AlreadyStopped => {
                    #[cfg(unix)]
                    if socket.exists() {
                        std::fs::remove_file(&socket)?;
                    }
                    return Ok(());
                }
                ShutdownErrorDisposition::Fatal => {
                    bail!("Failed to stop daemon: {message}");
                }
            }
        }
    };

    let timeout_secs = 5 + if active_runners > 0 { 15 } else { 0 };
    let deadline = tokio::time::Instant::now() + Duration::from_secs(timeout_secs);

    loop {
        #[cfg(unix)]
        if !socket.exists() {
            return Ok(());
        }
        #[cfg(windows)]
        if !is_daemon_running().await {
            return Ok(());
        }

        if tokio::time::Instant::now() >= deadline {
            let healthy = client.health().await.is_ok();
            if healthy {
                bail!("Daemon did not shut down in time and is still responding");
            }
            #[cfg(unix)]
            if socket.exists() {
                std::fs::remove_file(&socket)?;
            }
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
}

pub async fn restart_daemon() -> Result<()> {
    #[cfg(unix)]
    if is_daemon_running(&default_socket_path()).await {
        stop_daemon().await?;
    }
    #[cfg(windows)]
    if is_daemon_running().await {
        stop_daemon().await?;
    }
    tokio::time::sleep(Duration::from_millis(300)).await;
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
}
