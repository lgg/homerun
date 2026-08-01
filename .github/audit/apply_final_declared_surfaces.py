from __future__ import annotations

import re
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one exact match, found {count}")
    write(path, content.replace(old, new, 1))


def regex_once(path: str, pattern: str, replacement: str, flags: int = 0) -> None:
    content = read(path)
    updated, count = re.subn(pattern, replacement, content, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{path}: expected one regex match, found {count}: {pattern}")
    write(path, updated)


def insert_before_last_brace(path: str, addition: str) -> None:
    content = read(path)
    index = content.rfind("\n}")
    if index < 0:
        raise RuntimeError(f"{path}: final module brace not found")
    write(path, content[:index] + "\n" + addition.rstrip() + "\n" + content[index:])


# ---------------------------------------------------------------------------
# Settings: keep queued persistence alive after the screen unmounts.
# ---------------------------------------------------------------------------
replace_once(
    "apps/desktop/src/pages/Settings.tsx",
    '''        while (\n          settingsMountedRef.current &&\n          desiredPreferencesRef.current !== persistedPreferencesRef.current\n        ) {''',
    '''        while (desiredPreferencesRef.current !== persistedPreferencesRef.current) {''',
)
replace_once(
    "apps/desktop/src/pages/Settings.tsx",
    '''          if (version === preferenceVersionRef.current) {\n            desiredPreferencesRef.current = saved;\n            setPreferences(saved);\n            setWorkspaceInput(saved.workspace_path ?? "");\n          }''',
    '''          if (version === preferenceVersionRef.current) {\n            desiredPreferencesRef.current = saved;\n            if (settingsMountedRef.current) {\n              setPreferences(saved);\n              setWorkspaceInput(saved.workspace_path ?? "");\n            }\n          }''',
)
replace_once(
    "apps/desktop/src/pages/Settings.test.tsx",
    '''\n  it("opens About links through the Tauri shell bridge", async () => {''',
    '''\n  it("persists the newest queued snapshot after the screen unmounts", async () => {\n    let resolveFirst: ((value: Preferences) => void) | undefined;\n    mocks.updatePreferences\n      .mockReturnValueOnce(\n        new Promise((resolve) => {\n          resolveFirst = resolve;\n        }),\n      )\n      .mockImplementation(async (value: Preferences) => value);\n\n    const view = render(<Settings />);\n    const restore = await screen.findByRole("switch", { name: "Restore runners on launch" });\n    const completions = screen.getByRole("switch", { name: "Job completions" });\n    await waitFor(() => expect(restore).not.toBeDisabled());\n\n    act(() => {\n      restore.click();\n      completions.click();\n    });\n    expect(mocks.updatePreferences).toHaveBeenCalledTimes(1);\n    view.unmount();\n\n    await act(async () => {\n      resolveFirst?.({ ...preferences, start_runners_on_launch: true });\n      await Promise.resolve();\n    });\n\n    await waitFor(() => expect(mocks.updatePreferences).toHaveBeenCalledTimes(2));\n    expect(mocks.updatePreferences).toHaveBeenLastCalledWith(\n      expect.objectContaining({\n        start_runners_on_launch: true,\n        notify_job_completions: false,\n      }),\n    );\n  });\n\n  it("opens About links through the Tauri shell bridge", async () => {''',
)

# ---------------------------------------------------------------------------
# Main layout: unknown is not online; serialize health checks.
# ---------------------------------------------------------------------------
replace_once(
    "apps/desktop/src/components/Layout.tsx",
    'import { useState, useEffect, useRef, useCallback } from "react";',
    'import { useState, useEffect, useCallback } from "react";',
)
replace_once(
    "apps/desktop/src/components/Layout.tsx",
    '''  const [daemonConnected, setDaemonConnected] = useState(true);\n  const [dotCount, setDotCount] = useState(0);\n  const [starting, setStarting] = useState(false);\n  const wasDisconnectedRef = useRef(false);''',
    '''  const [daemonConnected, setDaemonConnected] = useState<boolean | null>(null);\n  const [dotCount, setDotCount] = useState(0);\n  const [starting, setStarting] = useState(false);''',
)
replace_once(
    "apps/desktop/src/components/Layout.tsx",
    '  useTrayIcon(runnersHook.runners, daemonConnected);',
    '  useTrayIcon(runnersHook.runners, daemonConnected === true);',
)
replace_once(
    "apps/desktop/src/components/Layout.tsx",
    '''  useEffect(() => {\n    if (!daemonConnected) {\n      const timer = setInterval(() => setDotCount((n) => (n + 1) % 4), 1500);\n      return () => clearInterval(timer);\n    }\n    setDotCount(0);\n  }, [daemonConnected]);''',
    '''  useEffect(() => {\n    if (daemonConnected === false) {\n      const timer = setInterval(() => setDotCount((n) => (n + 1) % 4), 1500);\n      return () => clearInterval(timer);\n    }\n    setDotCount(0);\n  }, [daemonConnected]);''',
)
regex_once(
    "apps/desktop/src/components/Layout.tsx",
    r'''  useEffect\(\(\) => \{\n    let cancelled = false;\n    async function check\(\) \{.*?\n    check\(\);\n    const timer = setInterval\(check, 10000\);\n    return \(\) => \{\n      cancelled = true;\n      clearInterval\(timer\);\n    \};\n  \}, \[\]\);''',
    '''  useEffect(() => {\n    let cancelled = false;\n    let timer: number | undefined;\n\n    async function check() {\n      try {\n        const ok = await api.healthCheck();\n        if (!cancelled) setDaemonConnected(ok);\n      } catch {\n        if (!cancelled) setDaemonConnected(false);\n      } finally {\n        if (!cancelled) {\n          timer = window.setTimeout(() => void check(), 10000);\n        }\n      }\n    }\n\n    void check();\n    return () => {\n      cancelled = true;\n      if (timer !== undefined) window.clearTimeout(timer);\n    };\n  }, []);''',
    re.S,
)
replace_once(
    "apps/desktop/src/components/Layout.tsx",
    '        {!daemonConnected && (',
    '        {daemonConnected === false && (',
)
write(
    "apps/desktop/src/components/Layout.test.tsx",
    '''import { render, waitFor } from "@testing-library/react";\nimport { MemoryRouter, Route, Routes } from "react-router";\nimport { beforeEach, describe, expect, it, vi } from "vitest";\nimport { Layout } from "./Layout";\n\nconst mocks = vi.hoisted(() => ({\n  healthCheck: vi.fn(),\n  getPreferences: vi.fn(),\n  useTrayIcon: vi.fn(),\n  useNotifications: vi.fn(),\n  unlisten: vi.fn(),\n}));\n\nvi.mock("../api/commands", () => ({\n  api: {\n    healthCheck: mocks.healthCheck,\n    getPreferences: mocks.getPreferences,\n    startDaemon: vi.fn(),\n  },\n}));\nvi.mock("../hooks/useRunners", () => ({\n  useRunners: () => ({ runners: [] }),\n}));\nvi.mock("../hooks/useTrayIcon", () => ({ useTrayIcon: mocks.useTrayIcon }));\nvi.mock("../hooks/useNotifications", () => ({ useNotifications: mocks.useNotifications }));\nvi.mock("@tauri-apps/api/event", () => ({\n  listen: vi.fn().mockResolvedValue(mocks.unlisten),\n}));\nvi.mock("./Sidebar", () => ({ Sidebar: () => <aside /> }));\n\ndescribe("Layout", () => {\n  beforeEach(() => {\n    vi.clearAllMocks();\n    mocks.getPreferences.mockResolvedValue(null);\n  });\n\n  it("does not report the daemon online before the first health check completes", async () => {\n    let resolveHealth: ((value: boolean) => void) | undefined;\n    mocks.healthCheck.mockReturnValue(\n      new Promise((resolve) => {\n        resolveHealth = resolve;\n      }),\n    );\n\n    const view = render(\n      <MemoryRouter>\n        <Routes>\n          <Route element={<Layout />}>\n            <Route index element={<div>content</div>} />\n          </Route>\n        </Routes>\n      </MemoryRouter>,\n    );\n\n    expect(mocks.useTrayIcon).toHaveBeenLastCalledWith([], false);\n    expect(view.queryByText("Unable to connect to the HomeRun daemon.")).not.toBeInTheDocument();\n\n    resolveHealth?.(true);\n    await waitFor(() => expect(mocks.useTrayIcon).toHaveBeenLastCalledWith([], true));\n  });\n});\n''',
)

# ---------------------------------------------------------------------------
# Desktop daemon lifecycle: never hide a live daemon by deleting its IPC path.
# ---------------------------------------------------------------------------
replace_once(
    "apps/desktop/src-tauri/src/commands.rs",
    '''use crate::AppState;\n\n#[tauri::command]''',
    '''use crate::AppState;\n\n#[derive(Debug, Clone, Copy, PartialEq, Eq)]\nenum ShutdownErrorDisposition {\n    ServiceManaged,\n    AlreadyStopped,\n    Fatal,\n}\n\nfn classify_shutdown_error(message: &str, daemon_healthy: bool) -> ShutdownErrorDisposition {\n    if message.contains("launchd")\n        || message.contains("Uninstall the service")\n        || message.contains("auto-start service")\n        || message.contains("system service")\n    {\n        ShutdownErrorDisposition::ServiceManaged\n    } else if daemon_healthy {\n        ShutdownErrorDisposition::Fatal\n    } else {\n        ShutdownErrorDisposition::AlreadyStopped\n    }\n}\n\nasync fn daemon_is_healthy(client: &crate::client::DaemonClient) -> bool {\n    matches!(\n        tokio::time::timeout(std::time::Duration::from_secs(2), client.health()).await,\n        Ok(Ok(_))\n    )\n}\n\nfn remove_stale_socket(client: &crate::client::DaemonClient) {\n    #[cfg(unix)]\n    {\n        let _ = std::fs::remove_file(client.socket_path());\n    }\n    #[cfg(windows)]\n    {\n        let _ = client;\n    }\n}\n\n#[tauri::command]''',
)
regex_once(
    "apps/desktop/src-tauri/src/commands.rs",
    r'''async fn do_stop_daemon\(client: crate::client::DaemonClient\) -> Result<bool, String> \{.*?\n\}\n\n#\[tauri::command\]\npub async fn stop_daemon''',
    '''async fn do_stop_daemon(client: crate::client::DaemonClient) -> Result<bool, String> {\n    let active_runners = match client.shutdown().await {\n        Ok(count) => count,\n        Err(error) => {\n            let message = error.to_string();\n            let healthy = daemon_is_healthy(&client).await;\n            match classify_shutdown_error(&message, healthy) {\n                ShutdownErrorDisposition::ServiceManaged => {\n                    let retry_client = client.clone_connection();\n                    retry_client.uninstall_service().await.map_err(|error| {\n                        format!("Failed to uninstall daemon startup service: {error}")\n                    })?;\n                    match retry_client.shutdown().await {\n                        Ok(count) => count,\n                        Err(retry_error) => {\n                            let retry_message = retry_error.to_string();\n                            let retry_healthy = daemon_is_healthy(&retry_client).await;\n                            if classify_shutdown_error(&retry_message, retry_healthy)\n                                == ShutdownErrorDisposition::AlreadyStopped\n                            {\n                                remove_stale_socket(&retry_client);\n                                return Ok(true);\n                            }\n                            return Err(format!(\n                                "Failed to stop daemon after uninstalling startup service: {retry_message}"\n                            ));\n                        }\n                    }\n                }\n                ShutdownErrorDisposition::AlreadyStopped => {\n                    remove_stale_socket(&client);\n                    return Ok(true);\n                }\n                ShutdownErrorDisposition::Fatal => {\n                    return Err(format!("Failed to stop daemon: {message}"));\n                }\n            }\n        }\n    };\n\n    let timeout_secs: u64 = 5 + if active_runners > 0 { 15 } else { 0 };\n    let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(timeout_secs);\n    loop {\n        if !client.socket_exists() {\n            return Ok(true);\n        }\n        if tokio::time::Instant::now() >= deadline {\n            if daemon_is_healthy(&client).await {\n                return Err("Daemon did not shut down in time and is still responding".to_string());\n            }\n            remove_stale_socket(&client);\n            return Ok(true);\n        }\n        tokio::time::sleep(std::time::Duration::from_millis(200)).await;\n    }\n}\n\n#[tauri::command]\npub async fn stop_daemon''',
    re.S,
)
replace_once(
    "apps/desktop/src-tauri/src/commands.rs",
    '''    let client = state.client.lock().await.clone_connection();\n    let _ = do_stop_daemon(client).await;\n    tokio::time::sleep(std::time::Duration::from_millis(300)).await;''',
    '''    let client = state.client.lock().await.clone_connection();\n    do_stop_daemon(client).await?;\n    tokio::time::sleep(std::time::Duration::from_millis(300)).await;''',
)
insert_before_last_brace(
    "apps/desktop/src-tauri/src/commands.rs",
    '''\n#[cfg(test)]\nmod tests {\n    use super::*;\n\n    #[test]\n    fn shutdown_errors_only_count_as_stopped_when_health_is_gone() {\n        assert_eq!(\n            classify_shutdown_error("connection refused", false),\n            ShutdownErrorDisposition::AlreadyStopped\n        );\n        assert_eq!(\n            classify_shutdown_error("temporary transport failure", true),\n            ShutdownErrorDisposition::Fatal\n        );\n        assert_eq!(\n            classify_shutdown_error("Uninstall the service first", true),\n            ShutdownErrorDisposition::ServiceManaged\n        );\n    }\n}\n''',
)

# ---------------------------------------------------------------------------
# CLI daemon lifecycle: same fail-closed behavior and restart propagation.
# ---------------------------------------------------------------------------
replace_once(
    "crates/tui/src/daemon_lifecycle.rs",
    '''use crate::client::DaemonClient;\n\n#[cfg(unix)]''',
    '''use crate::client::DaemonClient;\n\n#[derive(Debug, Clone, Copy, PartialEq, Eq)]\nenum ShutdownErrorDisposition {\n    ServiceManaged,\n    AlreadyStopped,\n    Fatal,\n}\n\nfn classify_shutdown_error(message: &str, daemon_healthy: bool) -> ShutdownErrorDisposition {\n    if message.contains("launchd")\n        || message.contains("Uninstall the service")\n        || message.contains("auto-start service")\n        || message.contains("system service")\n    {\n        ShutdownErrorDisposition::ServiceManaged\n    } else if daemon_healthy {\n        ShutdownErrorDisposition::Fatal\n    } else {\n        ShutdownErrorDisposition::AlreadyStopped\n    }\n}\n\n#[cfg(unix)]''',
)
regex_once(
    "crates/tui/src/daemon_lifecycle.rs",
    r'''pub async fn stop_daemon\(\) -> Result<\(\)> \{.*?\n\}\n\npub async fn restart_daemon\(\) -> Result<\(\)> \{.*?\n\}''',
    '''pub async fn stop_daemon() -> Result<()> {\n    #[cfg(unix)]\n    let socket = default_socket_path();\n\n    #[cfg(unix)]\n    if !socket.exists() {\n        bail!("Daemon is not running (no socket file)");\n    }\n\n    #[cfg(windows)]\n    if !is_daemon_running().await {\n        bail!("Daemon is not running");\n    }\n\n    #[cfg(unix)]\n    let client = DaemonClient::new(socket.clone());\n    #[cfg(windows)]\n    let client = DaemonClient::new_pipe(default_pipe_name());\n\n    let active_runners = match client.shutdown().await {\n        Ok(count) => count,\n        Err(error) => {\n            let message = error.to_string();\n            let healthy = client.health().await.is_ok();\n            match classify_shutdown_error(&message, healthy) {\n                ShutdownErrorDisposition::ServiceManaged => {\n                    #[cfg(target_os = "macos")]\n                    bail!(\n                        "Daemon is managed by launchd. Disable Launch at login first \\\n                         (Settings > Startup) or run: launchctl unload ~/Library/LaunchAgents/com.homerun.daemon.plist"\n                    );\n                    #[cfg(windows)]\n                    bail!(\n                        "Daemon is managed by Windows autostart. Disable Launch at login first \\\n                         (Settings > Startup)."\n                    );\n                    #[cfg(all(unix, not(target_os = "macos")))]\n                    bail!(\n                        "Daemon is managed by a system service. Disable that service before stopping it directly."\n                    );\n                }\n                ShutdownErrorDisposition::AlreadyStopped => {\n                    #[cfg(unix)]\n                    if socket.exists() {\n                        std::fs::remove_file(&socket)?;\n                    }\n                    return Ok(());\n                }\n                ShutdownErrorDisposition::Fatal => {\n                    bail!("Failed to stop daemon: {message}");\n                }\n            }\n        }\n    };\n\n    let timeout_secs = 5 + if active_runners > 0 { 15 } else { 0 };\n    let deadline = tokio::time::Instant::now() + Duration::from_secs(timeout_secs);\n\n    loop {\n        #[cfg(unix)]\n        if !socket.exists() {\n            return Ok(());\n        }\n        #[cfg(windows)]\n        if !is_daemon_running().await {\n            return Ok(());\n        }\n\n        if tokio::time::Instant::now() >= deadline {\n            let healthy = client.health().await.is_ok();\n            if healthy {\n                bail!("Daemon did not shut down in time and is still responding");\n            }\n            #[cfg(unix)]\n            if socket.exists() {\n                std::fs::remove_file(&socket)?;\n            }\n            return Ok(());\n        }\n        tokio::time::sleep(Duration::from_millis(200)).await;\n    }\n}\n\npub async fn restart_daemon() -> Result<()> {\n    #[cfg(unix)]\n    if is_daemon_running(&default_socket_path()).await {\n        stop_daemon().await?;\n    }\n    #[cfg(windows)]\n    if is_daemon_running().await {\n        stop_daemon().await?;\n    }\n    tokio::time::sleep(Duration::from_millis(300)).await;\n    start_daemon().await\n}''',
    re.S,
)
insert_before_last_brace(
    "crates/tui/src/daemon_lifecycle.rs",
    '''\n#[cfg(test)]\nmod tests {\n    use super::*;\n\n    #[test]\n    fn shutdown_error_classification_is_fail_closed() {\n        assert_eq!(\n            classify_shutdown_error("connection refused", false),\n            ShutdownErrorDisposition::AlreadyStopped\n        );\n        assert_eq!(\n            classify_shutdown_error("transport reset", true),\n            ShutdownErrorDisposition::Fatal\n        );\n        assert_eq!(\n            classify_shutdown_error("Daemon is installed as a system service", true),\n            ShutdownErrorDisposition::ServiceManaged\n        );\n    }\n}\n''',
)

# ---------------------------------------------------------------------------
# Job context: compare-and-set after the asynchronous GitHub lookup.
# ---------------------------------------------------------------------------
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''const RECENT_LOGS_MAX: usize = 500;\n\n/// Handle for communicating with a runner's monitoring task.''',
    '''const RECENT_LOGS_MAX: usize = 500;\n\nfn should_apply_job_context(\n    state: &RunnerState,\n    current_job: Option<&str>,\n    has_context: bool,\n    expected_job: &str,\n) -> bool {\n    *state == RunnerState::Busy && current_job == Some(expected_job) && !has_context\n}\n\n/// Handle for communicating with a runner's monitoring task.''',
)
replace_once(
    "crates/daemon/src/runner/mod.rs",
    '''                        Ok(Some(ctx)) => {\n                            tracing::info!(\n                                runner = %runner_id,\n                                branch = %ctx.branch,\n                                pr = ?ctx.pr_number,\n                                "Job context fetched"\n                            );\n                            let mut map = runners.write().await;\n                            if let Some(r) = map.get_mut(&runner_id) {\n                                r.job_context = Some(ctx);\n                            }\n                        }''',
    '''                        Ok(Some(ctx)) => {\n                            let mut map = runners.write().await;\n                            if let Some(runner) = map.get_mut(&runner_id) {\n                                if should_apply_job_context(\n                                    &runner.state,\n                                    runner.current_job.as_deref(),\n                                    runner.job_context.is_some(),\n                                    &job_name,\n                                ) {\n                                    tracing::info!(\n                                        runner = %runner_id,\n                                        branch = %ctx.branch,\n                                        pr = ?ctx.pr_number,\n                                        "Job context fetched"\n                                    );\n                                    runner.job_context = Some(ctx);\n                                } else {\n                                    tracing::debug!(\n                                        runner = %runner_id,\n                                        expected_job = %job_name,\n                                        current_job = ?runner.current_job,\n                                        state = ?runner.state,\n                                        "Discarding stale job context result"\n                                    );\n                                }\n                            }\n                        }''',
)
insert_before_last_brace(
    "crates/daemon/src/runner/mod.rs",
    '''\n    #[test]\n    fn test_job_context_compare_and_set_rejects_stale_results() {\n        assert!(should_apply_job_context(\n            &RunnerState::Busy,\n            Some("build"),\n            false,\n            "build"\n        ));\n        assert!(!should_apply_job_context(\n            &RunnerState::Online,\n            None,\n            false,\n            "build"\n        ));\n        assert!(!should_apply_job_context(\n            &RunnerState::Busy,\n            Some("test"),\n            false,\n            "build"\n        ));\n        assert!(!should_apply_job_context(\n            &RunnerState::Busy,\n            Some("build"),\n            true,\n            "build"\n        ));\n    }\n''',
)

# ---------------------------------------------------------------------------
# Local discovery: only real git repositories; robust GitHub remote parsing.
# ---------------------------------------------------------------------------
replace_once(
    "crates/daemon/src/scanner/mod.rs",
    '''                if let Some(repo_root) = path.parent() {\n                    process_workflows_dir(repo_root, &workflows_dir, labels, found).await;\n                }''',
    '''                if let Some(repo_root) = path.parent() {\n                    if repo_root.join(".git").exists() {\n                        process_workflows_dir(repo_root, &workflows_dir, labels, found).await;\n                    }\n                }''',
)
replace_once(
    "crates/daemon/src/scanner/mod.rs",
    '''                if let Some(repo_root) = path.parent() {\n                    let full_name = git_remote_full_name(repo_root).await.unwrap_or_else(|| {\n                        repo_root\n                            .file_name()\n                            .map(|n| n.to_string_lossy().to_string())\n                            .unwrap_or_default()\n                    });\n                    repos.push((full_name, repo_root.to_path_buf(), workflows_dir));\n                }''',
    '''                if let Some(repo_root) = path.parent() {\n                    if !repo_root.join(".git").exists() {\n                        continue;\n                    }\n                    let full_name = git_remote_full_name(repo_root).await.unwrap_or_else(|| {\n                        repo_root\n                            .file_name()\n                            .map(|n| n.to_string_lossy().to_string())\n                            .unwrap_or_default()\n                    });\n                    repos.push((full_name, repo_root.to_path_buf(), workflows_dir));\n                }''',
)
regex_once(
    "crates/daemon/src/scanner/mod.rs",
    r'''fn parse_github_full_name\(url: &str\) -> Option<String> \{.*?\n\}\n\n// ---------------------------------------------------------------------------\n// Remote scan''',
    '''fn parse_github_full_name(url: &str) -> Option<String> {\n    let candidate = if let Some(rest) = url.strip_prefix("git@github.com:") {\n        rest\n    } else if let Some(rest) = url\n        .strip_prefix("https://github.com/")\n        .or_else(|| url.strip_prefix("http://github.com/"))\n        .or_else(|| url.strip_prefix("ssh://git@github.com/"))\n    {\n        rest\n    } else {\n        return None;\n    };\n\n    let candidate = candidate.trim().trim_end_matches('/').trim_end_matches(".git");\n    let mut parts = candidate.split('/');\n    let owner = parts.next()?;\n    let repo = parts.next()?;\n    if owner.is_empty() || repo.is_empty() || parts.next().is_some() {\n        return None;\n    }\n    Some(format!("{owner}/{repo}"))\n}\n\n// ---------------------------------------------------------------------------\n// Remote scan''',
    re.S,
)
insert_before_last_brace(
    "crates/daemon/src/scanner/mod.rs",
    '''\n    #[tokio::test]\n    async fn test_local_scan_ignores_non_git_workflow_trees() {\n        let tmp = TempDir::new().unwrap();\n        let fake_repo = tmp.path().join("generated-copy");\n        write_workflow(\n            &fake_repo,\n            "ci.yml",\n            "jobs:\\n  build:\\n    runs-on: self-hosted\\n",\n        );\n\n        let results = scan_local(tmp.path(), &["self-hosted".to_string()])\n            .await\n            .unwrap();\n        assert!(results.is_empty());\n    }\n\n    #[test]\n    fn test_parse_github_url_requires_exact_owner_and_repo() {\n        assert_eq!(\n            parse_github_full_name("ssh://git@github.com/owner/repo.git"),\n            Some("owner/repo".to_string())\n        );\n        assert_eq!(parse_github_full_name("https://github.com/owner"), None);\n        assert_eq!(\n            parse_github_full_name("https://github.com/owner/repo/extra"),\n            None\n        );\n        assert_eq!(parse_github_full_name("https://gitlab.com/owner/repo"), None);\n    }\n''',
)

# ---------------------------------------------------------------------------
# Repositories: discovered local folders without owner/repo cannot launch.
# ---------------------------------------------------------------------------
write(
    "apps/desktop/src/utils/repository.ts",
    '''export function isLaunchableRepository(fullName: string): boolean {\n  const parts = fullName.split("/");\n  return parts.length === 2 && parts.every((part) => part.trim().length > 0);\n}\n''',
)
write(
    "apps/desktop/src/utils/repository.test.ts",
    '''import { describe, expect, it } from "vitest";\nimport { isLaunchableRepository } from "./repository";\n\ndescribe("isLaunchableRepository", () => {\n  it("requires an exact non-empty owner/repository pair", () => {\n    expect(isLaunchableRepository("owner/repo")).toBe(true);\n    expect(isLaunchableRepository("repo")).toBe(false);\n    expect(isLaunchableRepository("owner/")).toBe(false);\n    expect(isLaunchableRepository("/repo")).toBe(false);\n    expect(isLaunchableRepository("owner/repo/extra")).toBe(false);\n  });\n});\n''',
)
replace_once(
    "apps/desktop/src/pages/Repositories.tsx",
    '''import { api } from "../api/commands";''',
    '''import { api } from "../api/commands";\nimport { isLaunchableRepository } from "../utils/repository";''',
)
replace_once(
    "apps/desktop/src/pages/Repositories.tsx",
    '''            const discovered = discoveryMap.get(repo.full_name);\n            const isDimmed = hasScanned && showEnriched && !discovered;''',
    '''            const discovered = discoveryMap.get(repo.full_name);\n            const isDimmed = hasScanned && showEnriched && !discovered;\n            const canCreateRunner = isLaunchableRepository(repo.full_name);''',
)
replace_once(
    "apps/desktop/src/pages/Repositories.tsx",
    '''                  <button\n                    className="btn btn-primary"\n                    style={{ fontSize: 12, padding: "4px 12px" }}\n                    onClick={() => {\n                      if (auth.authenticated) {\n                        setWizardRepo(repo);\n                        setWizardOpen(true);\n                      } else {\n                        navigate("/settings");\n                      }\n                    }}\n                  >\n                    {auth.authenticated ? "+ Add Runner" : "Sign in to add"}\n                  </button>''',
    '''                  <button\n                    className="btn btn-primary"\n                    style={{ fontSize: 12, padding: "4px 12px" }}\n                    disabled={auth.authenticated && !canCreateRunner}\n                    title={\n                      auth.authenticated && !canCreateRunner\n                        ? "Add a GitHub remote in owner/repository form before creating a runner"\n                        : undefined\n                    }\n                    onClick={() => {\n                      if (auth.authenticated && canCreateRunner) {\n                        setWizardRepo(repo);\n                        setWizardOpen(true);\n                      } else if (!auth.authenticated) {\n                        navigate("/settings");\n                      }\n                    }}\n                  >\n                    {auth.authenticated\n                      ? canCreateRunner\n                        ? "+ Add Runner"\n                        : "GitHub remote required"\n                      : "Sign in to add"}\n                  </button>''',
)

# ---------------------------------------------------------------------------
# Reduce webview shell privileges and make service-mode wording truthful.
# ---------------------------------------------------------------------------
replace_once(
    "apps/desktop/src-tauri/capabilities/default.json",
    '''    "shell:allow-open",\n    "shell:allow-execute",\n    "shell:allow-spawn",''',
    '''    "shell:allow-open",''',
)
replace_once(
    "apps/desktop/src/components/NewRunnerWizard.tsx",
    '''                      ? "Runs as a background service. Survives restarts."''',
    '''                      ? "Runs persistently and can be reattached after daemon crashes."''',
)

# ---------------------------------------------------------------------------
# CLI status: every configured runner is represented in the summary.
# ---------------------------------------------------------------------------
replace_once(
    "crates/tui/src/cli.rs",
    '''pub async fn cmd_status(client: &DaemonClient, verbose: bool) -> Result<()> {''',
    '''#[derive(Debug, Default, PartialEq, Eq)]\nstruct RunnerStateCounts {\n    online: usize,\n    busy: usize,\n    offline: usize,\n    error: usize,\n    transitioning: usize,\n}\n\nfn count_runner_states<'a>(states: impl IntoIterator<Item = &'a str>) -> RunnerStateCounts {\n    let mut counts = RunnerStateCounts::default();\n    for state in states {\n        match state {\n            "online" => counts.online += 1,\n            "busy" => counts.busy += 1,\n            "offline" => counts.offline += 1,\n            "error" => counts.error += 1,\n            _ => counts.transitioning += 1,\n        }\n    }\n    counts\n}\n\npub async fn cmd_status(client: &DaemonClient, verbose: bool) -> Result<()> {''',
)
replace_once(
    "crates/tui/src/cli.rs",
    '''    let online = runners.iter().filter(|r| r.state == "online").count();\n    let busy = runners.iter().filter(|r| r.state == "busy").count();\n    let offline = runners.iter().filter(|r| r.state == "offline").count();''',
    '''    let counts = count_runner_states(runners.iter().map(|runner| runner.state.as_str()));''',
)
replace_once(
    "crates/tui/src/cli.rs",
    '''    println!(\n        "  Runners: {total} total ({} online, {} busy, {} offline)",\n        colored(&online.to_string(), "32"),\n        colored(&busy.to_string(), "33"),\n        colored(&offline.to_string(), "90"),\n    );''',
    '''    println!(\n        "  Runners: {total} total ({} online, {} busy, {} offline, {} error, {} transitioning)",\n        colored(&counts.online.to_string(), "32"),\n        colored(&counts.busy.to_string(), "33"),\n        colored(&counts.offline.to_string(), "90"),\n        colored(&counts.error.to_string(), "31"),\n        colored(&counts.transitioning.to_string(), "36"),\n    );''',
)
insert_before_last_brace(
    "crates/tui/src/cli.rs",
    '''\n    #[test]\n    fn test_count_runner_states_includes_errors_and_transitions() {\n        let counts = count_runner_states([\n            "online",\n            "busy",\n            "offline",\n            "error",\n            "registering",\n            "stopping",\n        ]);\n        assert_eq!(\n            counts,\n            RunnerStateCounts {\n                online: 1,\n                busy: 1,\n                offline: 1,\n                error: 1,\n                transitioning: 2,\n            }\n        );\n    }\n''',
)

# ---------------------------------------------------------------------------
# Metadata/docs: match the actual repository and implemented execution modes.
# ---------------------------------------------------------------------------
replace_once(
    "Cargo.toml",
    'repository = "https://github.com/aGallea/homerun"',
    'repository = "https://github.com/lgg/homerun"',
)
replace_once(
    "README.md",
    '''- **Two run modes** — app-managed (daemon child) or background service (launchd)''',
    '''- **Three execution modes** — app-managed, persistent native (reattached after daemon crashes), or Docker container''',
)
replace_once(
    "README.md",
    '''Until a tap is listed in the release notes, install the signed release assets\nabove instead of assuming an upstream or third-party tap.''',
    '''Until a tap is listed in the release notes, install the release assets above\ninstead of assuming an upstream or third-party tap.''',
)

print("final declared-surfaces patch applied")
