from pathlib import Path
import re


def read(path):
    return Path(path).read_text()


def write(path, content):
    Path(path).write_text(content)


def replace(path, old, new, count=1):
    data = read(path)
    actual = data.count(old)
    if actual < count:
        raise SystemExit(
            f"{path}: expected at least {count} occurrence(s), found {actual}: {old[:100]!r}"
        )
    write(path, data.replace(old, new, count))


def regex_replace(path, pattern, replacement, count=1):
    data = read(path)
    updated, actual = re.subn(pattern, replacement, data, count=count, flags=re.S)
    if actual != count:
        raise SystemExit(
            f"{path}: expected {count} regex replacement(s), got {actual}: {pattern}"
        )
    write(path, updated)


# ---- Daemon: remove HOME panic and preserve preference defaults ----
replace(
    "crates/daemon/src/config.rs",
    "use anyhow::Result;",
    "use anyhow::{Context, Result};",
)
replace(
    "crates/daemon/src/config.rs",
    "    #[serde(default)]\n    pub auto_scan: bool,\n",
    "    #[serde(default)]\n    pub auto_scan: bool,\n    #[serde(default)]\n    pub hide_offline_runners_in_mini_view: bool,\n    #[serde(default)]\n    pub sort_runners_by_activity: bool,\n",
)
replace(
    "crates/daemon/src/config.rs",
    "            auto_scan: false,\n",
    "            auto_scan: false,\n            hide_offline_runners_in_mini_view: false,\n            sort_runners_by_activity: false,\n",
)
regex_replace(
    "crates/daemon/src/config.rs",
    r"impl Default for Config \{.*?\n\}\n\nimpl Config \{",
    '''impl Config {
    fn from_home(home: Option<PathBuf>) -> Result<Self> {
        let home = home.context("Cannot determine home directory for HomeRun configuration")?;
        Ok(Self {
            base_dir: home.join(".homerun"),
            preferences: Preferences::default(),
        })
    }

    pub fn try_default() -> Result<Self> {
        Self::from_home(dirs::home_dir())
    }''',
)
replace(
    "crates/daemon/src/config.rs",
    "Config::default()",
    "Config::try_default().unwrap()",
    count=3,
)
replace(
    "crates/daemon/src/config.rs",
    "        assert!(!prefs.auto_scan);\n        // Existing fields preserved\n",
    "        assert!(!prefs.auto_scan);\n        assert!(!prefs.hide_offline_runners_in_mini_view);\n        assert!(!prefs.sort_runners_by_activity);\n        // Existing fields preserved\n",
)
replace(
    "crates/daemon/src/config.rs",
    "    #[test]\n    fn test_preferences_scan_fields_roundtrip() {",
    '''    #[test]
    fn test_missing_home_is_reported_without_panicking() {
        let error = Config::from_home(None).err().expect("missing HOME should fail");
        assert!(error.to_string().contains("Cannot determine home directory"));
    }

    #[test]
    fn test_preferences_scan_fields_roundtrip() {''',
)
replace(
    "crates/daemon/src/main.rs",
    "    let mut config = homerund::config::Config::default();",
    "    let mut config = homerund::config::Config::try_default()?;",
)

# ---- Runner update comparison: only newer versions are updates ----
replace(
    "crates/daemon/src/updater.rs",
    "pub async fn fetch_latest_version() -> Result<String> {\n    crate::runner::binary::get_latest_runner_version().await\n}\n",
    '''pub async fn fetch_latest_version() -> Result<String> {
    crate::runner::binary::get_latest_runner_version().await
}

fn parse_runner_version(version: &str) -> Option<Vec<u64>> {
    let trimmed = version.trim().trim_start_matches('v');
    let parsed: Option<Vec<u64>> = trimmed
        .split('.')
        .map(|part| part.parse::<u64>().ok())
        .collect();
    parsed.filter(|parts| !parts.is_empty())
}

pub fn is_newer_runner_version(current: &str, latest: &str) -> bool {
    match (parse_runner_version(current), parse_runner_version(latest)) {
        (Some(current), Some(latest)) => latest > current,
        _ => false,
    }
}
''',
)
replace(
    "crates/daemon/src/updater.rs",
    "    if latest != current {\n        Some(latest)\n    } else {\n        None\n    }",
    "    if is_newer_runner_version(&current, &latest) {\n        Some(latest)\n    } else {\n        None\n    }",
)
replace(
    "crates/daemon/src/updater.rs",
    "    #[test]\n    fn test_read_cached_version_missing_file() {",
    '''    #[test]
    fn test_runner_version_comparison_only_flags_newer_versions() {
        assert!(is_newer_runner_version("2.321.0", "2.322.0"));
        assert!(!is_newer_runner_version("2.322.0", "2.322.0"));
        assert!(!is_newer_runner_version("2.323.0", "2.322.0"));
        assert!(is_newer_runner_version("v2.321.9", "2.322.0"));
        assert!(!is_newer_runner_version("not-a-version", "2.322.0"));
    }

    #[test]
    fn test_read_cached_version_missing_file() {''',
)
replace(
    "crates/daemon/src/api/updates.rs",
    "    let update_available = current.as_deref() != Some(latest.as_str());",
    "    let update_available = current\n        .as_deref()\n        .map(|version| updater::is_newer_runner_version(version, &latest))\n        .unwrap_or(true);",
)

# ---- TUI/CLI DaemonClient: fallible socket resolution ----
replace(
    "crates/tui/src/client.rs",
    '''    pub fn default_socket() -> Self {
        #[cfg(unix)]
        {
            let home = dirs::home_dir().expect("no home directory");
            Self::new(home.join(".homerun/daemon.sock"))
        }
        #[cfg(windows)]
        {
            Self::new_pipe(r"\\\\.\\pipe\\homerun-daemon".to_string())
        }
    }
''',
    '''    #[cfg(unix)]
    fn default_socket_from_home(home: Option<PathBuf>) -> Result<Self> {
        let home = home.context("Cannot determine home directory for HomeRun daemon socket")?;
        Ok(Self::new(home.join(".homerun/daemon.sock")))
    }

    pub fn default_socket() -> Result<Self> {
        #[cfg(unix)]
        {
            Self::default_socket_from_home(dirs::home_dir())
        }
        #[cfg(windows)]
        {
            Ok(Self::new_pipe(r"\\\\.\\pipe\\homerun-daemon".to_string()))
        }
    }
''',
)
replace(
    "crates/tui/src/client.rs",
    "    #[tokio::test]\n    async fn test_parse_runners_response() {",
    '''    #[cfg(unix)]
    #[test]
    fn test_default_socket_reports_missing_home() {
        let error = DaemonClient::default_socket_from_home(None)
            .err()
            .expect("missing HOME should fail");
        assert!(error.to_string().contains("Cannot determine home directory"));
    }

    #[tokio::test]
    async fn test_parse_runners_response() {''',
)
replace(
    "crates/tui/src/main.rs",
    "    let client = DaemonClient::default_socket();",
    "    let client = DaemonClient::default_socket()?;",
)
replace(
    "crates/tui/src/cli.rs",
    "    let client = DaemonClient::default_socket();",
    "    let client = DaemonClient::default_socket()?;",
)

# ---- Desktop client: resolve HOME once and clone connection config ----
replace(
    "apps/desktop/src-tauri/src/client.rs",
    "    pub uptime_secs: Option<u64>,\n    pub jobs_completed: u32,",
    "    pub uptime_secs: Option<u64>,\n    #[serde(default)]\n    pub started_at: Option<String>,\n    pub jobs_completed: u32,",
)
replace(
    "apps/desktop/src-tauri/src/client.rs",
    "    #[serde(default)]\n    pub auto_scan: bool,\n}",
    "    #[serde(default)]\n    pub auto_scan: bool,\n    #[serde(default)]\n    pub hide_offline_runners_in_mini_view: bool,\n    #[serde(default)]\n    pub sort_runners_by_activity: bool,\n}",
)
replace(
    "apps/desktop/src-tauri/src/client.rs",
    '''    pub fn default_socket() -> Self {
        #[cfg(unix)]
        {
            let home = dirs::home_dir().expect("no home directory");
            Self::new(home.join(".homerun/daemon.sock"))
        }
        #[cfg(windows)]
        {
            Self::new_pipe(r"\\\\.\\pipe\\homerun-daemon".to_string())
        }
    }
''',
    '''    #[cfg(unix)]
    fn default_socket_from_home(home: Option<PathBuf>) -> Result<Self, String> {
        let home = home
            .ok_or_else(|| "Cannot determine home directory for HomeRun daemon socket".to_string())?;
        Ok(Self::new(home.join(".homerun/daemon.sock")))
    }

    pub fn default_socket() -> Result<Self, String> {
        #[cfg(unix)]
        {
            Self::default_socket_from_home(dirs::home_dir())
        }
        #[cfg(windows)]
        {
            Ok(Self::new_pipe(r"\\\\.\\pipe\\homerun-daemon".to_string()))
        }
    }
''',
)
data = read("apps/desktop/src-tauri/src/client.rs")
if "test_default_socket_reports_missing_home" not in data:
    data += '''

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn test_default_socket_reports_missing_home() {
        let error = DaemonClient::default_socket_from_home(None)
            .err()
            .expect("missing HOME should fail");
        assert!(error.contains("Cannot determine home directory"));
    }
}
'''
    write("apps/desktop/src-tauri/src/client.rs", data)

replace(
    "apps/desktop/src-tauri/src/lib.rs",
    '''pub fn run() {
    let client = DaemonClient::default_socket();

    tauri::Builder::default()''',
    '''pub fn run() {
    let client = match DaemonClient::default_socket() {
        Ok(client) => client,
        Err(error) => {
            eprintln!("Failed to resolve HomeRun daemon connection: {error}");
            return;
        }
    };
    let sidecar_client = client.clone_connection();
    let event_client = client.clone_connection();

    tauri::Builder::default()''',
)
replace(
    "apps/desktop/src-tauri/src/lib.rs",
    ".setup(|app| {",
    ".setup(move |app| {",
)
replace(
    "apps/desktop/src-tauri/src/lib.rs",
    '''            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let client = crate::client::DaemonClient::default_socket();
                if client.health().await.is_ok() {''',
    '''            let handle = app.handle().clone();
            let client = sidecar_client.clone_connection();
            tauri::async_runtime::spawn(async move {
                if client.health().await.is_ok() {''',
)
replace(
    "apps/desktop/src-tauri/src/lib.rs",
    '''            let event_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                use futures::StreamExt;
                use tokio_tungstenite::tungstenite::Message;

                loop {
                    let client = crate::client::DaemonClient::default_socket();
                    if let Ok(mut events) = client.connect_events().await {''',
    '''            let event_handle = app.handle().clone();
            let client = event_client.clone_connection();
            tauri::async_runtime::spawn(async move {
                use futures::StreamExt;
                use tokio_tungstenite::tungstenite::Message;

                loop {
                    if let Ok(mut events) = client.connect_events().await {''',
)

# Desktop daemon lifecycle uses the already-resolved AppState client.
regex_replace(
    "apps/desktop/src-tauri/src/commands.rs",
    r"#\[tauri::command\]\npub async fn start_daemon\(app_handle: tauri::AppHandle\) -> Result<bool, String> \{.*?\n\}\n\n/// Helper: stop the daemon",
    '''async fn do_start_daemon(
    app_handle: tauri::AppHandle,
    client: crate::client::DaemonClient,
) -> Result<bool, String> {
    use std::time::Duration;
    use tauri_plugin_shell::ShellExt;

    if client.socket_exists() {
        let check = tokio::time::timeout(std::time::Duration::from_secs(2), client.health()).await;
        if matches!(check, Ok(Ok(_))) {
            return Err("Daemon is already running".to_string());
        }
        remove_stale_socket(&client);
    }

    let sidecar = app_handle
        .shell()
        .sidecar("homerund")
        .map_err(|e| format!("Failed to find sidecar: {e}"))?;

    let (_rx, _child) = sidecar
        .spawn()
        .map_err(|e| format!("Failed to spawn daemon: {e}"))?;

    let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
    loop {
        if client.health().await.is_ok() {
            return Ok(true);
        }
        if tokio::time::Instant::now() >= deadline {
            return Err(
                "Daemon failed to start within 5 seconds — check logs at ~/.homerun/logs/"
                    .to_string(),
            );
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
}

#[tauri::command]
pub async fn start_daemon(
    app_handle: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<bool, String> {
    let client = state.client.lock().await.clone_connection();
    do_start_daemon(app_handle, client).await
}

/// Helper: stop the daemon''',
)
regex_replace(
    "apps/desktop/src-tauri/src/commands.rs",
    r"#\[tauri::command\]\npub async fn restart_daemon\(\n    app_handle: tauri::AppHandle,\n    state: State<'_, AppState>,\n\) -> Result<bool, String> \{.*?\n\}",
    '''#[tauri::command]
pub async fn restart_daemon(
    app_handle: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<bool, String> {
    let client = state.client.lock().await.clone_connection();
    do_stop_daemon(client.clone_connection()).await?;
    tokio::time::sleep(std::time::Duration::from_millis(300)).await;
    do_start_daemon(app_handle, client).await
}''',
)

# ---- Desktop API types + persisted preference shape ----
replace(
    "apps/desktop/src/api/types.ts",
    "  uptime_secs: number | null;\n  jobs_completed: number;",
    "  uptime_secs: number | null;\n  started_at?: string | null;\n  jobs_completed: number;",
)
replace(
    "apps/desktop/src/api/types.ts",
    "  workspace_path: string | null;\n  auto_scan: boolean;\n}",
    "  workspace_path: string | null;\n  auto_scan: boolean;\n  hide_offline_runners_in_mini_view: boolean;\n  sort_runners_by_activity: boolean;\n}",
)

# ---- Shared display preference hook ----
Path("apps/desktop/src/hooks/useRunnerDisplayPreferences.ts").write_text('''import { useEffect, useState } from "react";
import { api } from "../api/commands";

export interface RunnerDisplayPreferences {
  hideOfflineRunnersInMiniView: boolean;
  sortRunnersByActivity: boolean;
}

const defaults: RunnerDisplayPreferences = {
  hideOfflineRunnersInMiniView: false,
  sortRunnersByActivity: false,
};

export function useRunnerDisplayPreferences(): RunnerDisplayPreferences {
  const [preferences, setPreferences] = useState(defaults);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const saved = await api.getPreferences();
        if (cancelled) return;
        setPreferences({
          hideOfflineRunnersInMiniView: saved.hide_offline_runners_in_mini_view ?? false,
          sortRunnersByActivity: saved.sort_runners_by_activity ?? false,
        });
      } catch {
        // Keep backward-compatible defaults while the daemon is unavailable.
      }
    }

    void refresh();
    const interval = window.setInterval(() => void refresh(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  return preferences;
}
''')

# ---- Shared activity ordering / compact filtering ----
Path("apps/desktop/src/utils/runnerOrdering.ts").write_text('''import type { RunnerInfo } from "../api/types";

export function isOfflineRunner(runner: RunnerInfo): boolean {
  return runner.state === "offline" || runner.state === "error";
}

function parseTimestamp(value: string | null | undefined): number {
  if (!value) return Number.NEGATIVE_INFINITY;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

export function runnerActivityTimestamp(runner: RunnerInfo): number {
  return Math.max(
    parseTimestamp(runner.job_started_at),
    parseTimestamp(runner.last_completed_job?.completed_at),
    parseTimestamp(runner.started_at),
  );
}

export function compareRunnersByActivity(a: RunnerInfo, b: RunnerInfo): number {
  const aOffline = isOfflineRunner(a);
  const bOffline = isOfflineRunner(b);
  if (aOffline !== bOffline) return aOffline ? 1 : -1;

  const aBusy = a.state === "busy";
  const bBusy = b.state === "busy";
  if (aBusy !== bBusy) return aBusy ? -1 : 1;

  const activityDifference = runnerActivityTimestamp(b) - runnerActivityTimestamp(a);
  if (Number.isFinite(activityDifference) && activityDifference !== 0) return activityDifference;

  const aName = a.config.display_name ?? a.config.name;
  const bName = b.config.display_name ?? b.config.name;
  return aName.localeCompare(bName, undefined, { numeric: true });
}

export function sortRunnersByActivity(runners: RunnerInfo[]): RunnerInfo[] {
  return [...runners].sort(compareRunnersByActivity);
}

export function filterCompactRunners(runners: RunnerInfo[], hideOffline: boolean): RunnerInfo[] {
  return hideOffline ? runners.filter((runner) => !isOfflineRunner(runner)) : runners;
}
''')
Path("apps/desktop/src/utils/runnerOrdering.test.ts").write_text('''import { describe, expect, it } from "vitest";
import type { RunnerInfo, RunnerState } from "../api/types";
import { filterCompactRunners, sortRunnersByActivity } from "./runnerOrdering";

function runner(
  name: string,
  state: RunnerState,
  options: { completedAt?: string; jobStartedAt?: string; startedAt?: string } = {},
): RunnerInfo {
  return {
    config: {
      id: name,
      name,
      repo_owner: "owner",
      repo_name: "repo",
      labels: [],
      mode: "app",
      work_dir: `/tmp/${name}`,
    },
    state,
    pid: null,
    uptime_secs: null,
    started_at: options.startedAt ?? null,
    jobs_completed: options.completedAt ? 1 : 0,
    jobs_failed: 0,
    current_job: state === "busy" ? "build" : null,
    job_started_at: options.jobStartedAt ?? null,
    last_completed_job: options.completedAt
      ? {
          job_name: "build",
          succeeded: true,
          completed_at: options.completedAt,
          duration_secs: 5,
        }
      : null,
  };
}

describe("runner activity ordering", () => {
  it("puts active work first, then recent activity, and offline runners last", () => {
    const busy = runner("busy", "busy", { jobStartedAt: "2026-08-28T12:00:00Z" });
    const recent = runner("recent", "online", { completedAt: "2026-08-28T11:59:59Z" });
    const old = runner("old", "online", { completedAt: "2026-08-27T12:00:00Z" });
    const offline = runner("offline", "offline", { completedAt: "2026-08-28T12:01:00Z" });

    expect(sortRunnersByActivity([offline, old, recent, busy]).map((item) => item.config.name)).toEqual([
      "busy",
      "recent",
      "old",
      "offline",
    ]);
  });

  it("hides only offline/error runners from compact lists", () => {
    const online = runner("online", "online");
    const busy = runner("busy", "busy");
    const offline = runner("offline", "offline");
    const error = runner("error", "error");
    expect(filterCompactRunners([online, offline, error, busy], true)).toEqual([online, busy]);
    expect(filterCompactRunners([online, offline], false)).toEqual([online, offline]);
  });
});
''')

# ---- Settings UI: defaults remain current behavior (both false) ----
replace(
    "apps/desktop/src/pages/Settings.tsx",
    "    workspace_path: null,\n    auto_scan: false,\n  });",
    "    workspace_path: null,\n    auto_scan: false,\n    hide_offline_runners_in_mini_view: false,\n    sort_runners_by_activity: false,\n  });",
)
replace(
    "apps/desktop/src/pages/Settings.tsx",
    "      {/* Repository Scanning */}",
    '''      {/* Runner Lists */}
      <section style={{ marginBottom: 32 }}>
        <SectionHeader title="Runner Lists" />
        <div className="card">
          <ToggleSetting
            label="Hide offline runners in Mini View"
            description="Hide offline and error runners from the compact Mini View list. The main Runners page is unchanged."
            checked={preferences.hide_offline_runners_in_mini_view}
            disabled={preferencesLoading || preferencesSaving}
            onChange={(checked) => updatePreference("hide_offline_runners_in_mini_view", checked)}
          />
          <Divider />
          <ToggleSetting
            label="Sort runners by recent activity"
            description="Show currently busy and most recently active runners first in both the main and compact lists. Offline runners stay at the bottom."
            checked={preferences.sort_runners_by_activity}
            disabled={preferencesLoading || preferencesSaving}
            onChange={(checked) => updatePreference("sort_runners_by_activity", checked)}
          />
        </div>
      </section>

      {/* Repository Scanning */}''',
)
replace(
    "apps/desktop/src/pages/Settings.test.tsx",
    "  workspace_path: null,\n  auto_scan: false,\n};",
    "  workspace_path: null,\n  auto_scan: false,\n  hide_offline_runners_in_mini_view: false,\n  sort_runners_by_activity: false,\n};",
)
replace(
    "apps/desktop/src/pages/Settings.test.tsx",
    "  it(\"opens About links through the Tauri shell bridge\", async () => {",
    '''  it("persists runner list display preferences", async () => {
    render(<Settings />);
    const hideOffline = await screen.findByRole("switch", {
      name: "Hide offline runners in Mini View",
    });
    await waitFor(() => expect(hideOffline).not.toBeDisabled());
    fireEvent.click(hideOffline);
    await waitFor(() =>
      expect(mocks.updatePreferences).toHaveBeenCalledWith(
        expect.objectContaining({ hide_offline_runners_in_mini_view: true }),
      ),
    );
  });

  it("opens About links through the Tauri shell bridge", async () => {''',
)

# ---- Full dashboard: activity sorting opt-in only ----
replace(
    "apps/desktop/src/pages/Dashboard.tsx",
    'import { NewRunnerWizard } from "../components/NewRunnerWizard";',
    'import { NewRunnerWizard } from "../components/NewRunnerWizard";\nimport { useRunnerDisplayPreferences } from "../hooks/useRunnerDisplayPreferences";',
)
replace(
    "apps/desktop/src/pages/Dashboard.tsx",
    "  const { metrics } = useMetrics();\n  const [showWizard, setShowWizard] = useState(false);",
    "  const { metrics } = useMetrics();\n  const { sortRunnersByActivity } = useRunnerDisplayPreferences();\n  const [showWizard, setShowWizard] = useState(false);",
)
replace(
    "apps/desktop/src/pages/Dashboard.tsx",
    "        readOnly={!isAuthenticated}\n      />",
    "        readOnly={!isAuthenticated}\n        sortByActivity={sortRunnersByActivity}\n      />",
)

# RunnerTable preserves name ordering unless the new preference is enabled.
replace(
    "apps/desktop/src/components/RunnerTable.tsx",
    'import { DockerBadge } from "./DockerBadge";',
    'import { DockerBadge } from "./DockerBadge";\nimport { compareRunnersByActivity } from "../utils/runnerOrdering";',
)
replace(
    "apps/desktop/src/components/RunnerTable.tsx",
    "  readOnly?: boolean;\n}",
    "  readOnly?: boolean;\n  sortByActivity?: boolean;\n}",
)
replace(
    "apps/desktop/src/components/RunnerTable.tsx",
    "  pendingActions,\n  readOnly = false,\n}: RunnerTableProps) {",
    "  pendingActions,\n  readOnly = false,\n  sortByActivity = false,\n}: RunnerTableProps) {",
)
regex_replace(
    "apps/desktop/src/components/RunnerTable.tsx",
    r"  const \{ groups, soloRunners \} = useMemo\(\(\) => \{.*?\n  \}, \[runners\]\);",
    '''  const { groups, soloRunners } = useMemo(() => {
    const byName = (a: RunnerInfo, b: RunnerInfo) =>
      a.config.name.localeCompare(b.config.name, undefined, { numeric: true });
    const orderRunners = (items: RunnerInfo[]) =>
      items.sort(sortByActivity ? compareRunnersByActivity : byName);

    // Group by name prefix + repo (merges runners from separate batch creates)
    const mergedMap = new Map<string, RunnerInfo[]>();
    const solo: RunnerInfo[] = [];
    for (const runner of runners) {
      if (runner.config.group_id) {
        const prefix = runner.config.name.replace(/-\d+$/, "");
        const repo = `${runner.config.repo_owner}/${runner.config.repo_name}`;
        const key = `${prefix}::${repo}`;
        const existing = mergedMap.get(key) ?? [];
        existing.push(runner);
        mergedMap.set(key, existing);
      } else {
        solo.push(runner);
      }
    }

    for (const group of mergedMap.values()) orderRunners(group);
    orderRunners(solo);

    const groupEntries = Array.from(mergedMap.entries());
    if (sortByActivity) {
      groupEntries.sort(([aKey, aRunners], [bKey, bRunners]) => {
        const byActivity = compareRunnersByActivity(aRunners[0], bRunners[0]);
        return byActivity || aKey.localeCompare(bKey, undefined, { numeric: true });
      });
    }
    return { groups: groupEntries, soloRunners: solo };
  }, [runners, sortByActivity]);''',
)
replace(
    "apps/desktop/src/components/RunnerTable.tsx",
    "{Array.from(groups.entries()).map(([groupKey, groupRunners]) => {",
    "{groups.map(([groupKey, groupRunners]) => {",
)

# ---- Compact tray runner list: hide offline opt-in; activity sort opt-in ----
replace(
    "apps/desktop/src/pages/TrayPanel.tsx",
    'import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";',
    'import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";\nimport { useRunnerDisplayPreferences } from "../hooks/useRunnerDisplayPreferences";\nimport { filterCompactRunners, sortRunnersByActivity } from "../utils/runnerOrdering";',
)
replace(
    "apps/desktop/src/pages/TrayPanel.tsx",
    "  const [daemonStopping, setDaemonStopping] = useState(false);\n  const containerRef = useRef<HTMLDivElement>(null);",
    "  const [daemonStopping, setDaemonStopping] = useState(false);\n  const { hideOfflineRunnersInMiniView, sortRunnersByActivity: sortByRecentActivity } =\n    useRunnerDisplayPreferences();\n  const containerRef = useRef<HTMLDivElement>(null);",
)
regex_replace(
    "apps/desktop/src/pages/TrayPanel.tsx",
    r"  const sorted = \[\.\.\.runners\]\.sort\(\(a, b\) => \{.*?\n  \}\);",
    '''  const compactRunners = filterCompactRunners(runners, hideOfflineRunnersInMiniView);
  const sorted = sortByRecentActivity
    ? sortRunnersByActivity(compactRunners)
    : [...compactRunners].sort((a, b) => {
        const order: Record<string, number> = { busy: 0, online: 1, creating: 1, registering: 1 };
        return (order[a.state] ?? 2) - (order[b.state] ?? 2);
      });''',
)
replace(
    "apps/desktop/src/pages/TrayPanel.tsx",
    "  }, [runners, resizeToFit]);",
    "  }, [runners, hideOfflineRunnersInMiniView, resizeToFit]);",
)
replace(
    "apps/desktop/src/pages/TrayPanel.tsx",
    '''        {runners.length === 0 && (
          <div className="tray-no-runners">
            {daemonOk ? "No runners configured" : "Cannot reach daemon"}
          </div>
        )}''',
    '''        {sorted.length === 0 && (
          <div className="tray-no-runners">
            {!daemonOk
              ? "Cannot reach daemon"
              : runners.length > 0 && hideOfflineRunnersInMiniView
                ? "Offline runners hidden"
                : "No runners configured"}
          </div>
        )}''',
)

# ---- Toggle Mini View: opt-in activity ordering for its busy cards ----
replace(
    "apps/desktop/src/pages/MiniView.tsx",
    'import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";',
    'import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";\nimport { useRunnerDisplayPreferences } from "../hooks/useRunnerDisplayPreferences";\nimport { sortRunnersByActivity } from "../utils/runnerOrdering";',
)
replace(
    "apps/desktop/src/pages/MiniView.tsx",
    "  const { runners, loading, error } = useRunners();\n  const positionSaved = useRef(false);",
    "  const { runners, loading, error } = useRunners();\n  const { sortRunnersByActivity: sortByRecentActivity } = useRunnerDisplayPreferences();\n  const positionSaved = useRef(false);",
)
regex_replace(
    "apps/desktop/src/pages/MiniView.tsx",
    r"  const busy = runners\n    \.filter\(\(r\) => r\.state === \"busy\"\)\n    \.sort\(\(a, b\) => \{.*?\n    \}\);",
    '''  const busyRunners = runners.filter((runner) => runner.state === "busy");
  const busy = sortByRecentActivity
    ? sortRunnersByActivity(busyRunners)
    : busyRunners.sort((a, b) => {
        const aTime = a.job_started_at ? new Date(a.job_started_at).getTime() : -Infinity;
        const bTime = b.job_started_at ? new Date(b.job_started_at).getTime() : -Infinity;
        return bTime - aTime;
      });''',
)

# ---- Preferences API regression coverage ----
replace(
    "crates/daemon/src/api/preferences.rs",
    '        assert_eq!(json["notify_job_completions"], true);',
    '        assert_eq!(json["notify_job_completions"], true);\n        assert_eq!(json["hide_offline_runners_in_mini_view"], false);\n        assert_eq!(json["sort_runners_by_activity"], false);',
)
replace(
    "crates/daemon/src/api/preferences.rs",
    'r#"{\"start_runners_on_launch\":true,\"notify_status_changes\":false,\"notify_job_completions\":true}"#',
    'r#"{\"start_runners_on_launch\":true,\"notify_status_changes\":false,\"notify_job_completions\":true,\"hide_offline_runners_in_mini_view\":true,\"sort_runners_by_activity\":true}"#',
)
replace(
    "crates/daemon/src/api/preferences.rs",
    '        assert_eq!(json["notify_status_changes"], false);',
    '        assert_eq!(json["notify_status_changes"], false);\n        assert_eq!(json["hide_offline_runners_in_mini_view"], true);\n        assert_eq!(json["sort_runners_by_activity"], true);',
)

# Remove the one-shot patch machinery from the feature commit.
Path(".github/workflows/apply-runtime-runner-display-patch.yml").unlink()
Path(".github/apply_runtime_runner_display_patch.py").unlink()
