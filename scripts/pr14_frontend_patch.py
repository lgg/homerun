from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        if new in text:
            return
        raise SystemExit(f"pattern not found in {path}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


def regex_once(path: str, pattern: str, replacement: str) -> None:
    text = read(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        if replacement in text:
            return
        raise SystemExit(f"regex matched {count} times in {path}: {pattern[:120]!r}")
    write(path, updated)


# Auth refreshes can overlap with login/logout. Only the latest operation may
# update context state, and logout errors must remain visible to the caller.
replace_once(
    "apps/desktop/src/hooks/AuthContext.tsx",
    '''  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const status = await api.getAuthStatus();
      setAuth(status);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);''',
    '''  const [error, setError] = useState<string | null>(null);
  const operationGeneration = useRef(0);

  const refresh = useCallback(async () => {
    const generation = ++operationGeneration.current;
    try {
      const status = await api.getAuthStatus();
      if (generation !== operationGeneration.current) return;
      setError(null);
      setAuth(status);
    } catch (e) {
      if (generation === operationGeneration.current) setError(String(e));
    } finally {
      if (generation === operationGeneration.current) setLoading(false);
    }
  }, []);''',
)
replace_once(
    "apps/desktop/src/hooks/AuthContext.tsx",
    '''      const status = await api.loginWithToken(token);
      setAuth(status);''',
    '''      const status = await api.loginWithToken(token);
      operationGeneration.current += 1;
      setAuth(status);''',
)
replace_once(
    "apps/desktop/src/hooks/AuthContext.tsx",
    '''  const logout = useCallback(async () => {
    try {
      await api.logout();
      setAuth({ authenticated: false, user: null });
    } catch (e) {
      setError(String(e));
    }
  }, []);''',
    '''  const logout = useCallback(async () => {
    try {
      setError(null);
      await api.logout();
      operationGeneration.current += 1;
      setAuth({ authenticated: false, user: null });
    } catch (e) {
      const message = String(e);
      setError(message);
      throw new Error(message);
    }
  }, []);''',
)
replace_once(
    "apps/desktop/src/hooks/AuthContext.tsx",
    '''import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from "react";''',
    '''import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  type ReactNode,
} from "react";''',
)

# Runner polling: ignore stale responses and preserve the last known list on a
# transient daemon error instead of flashing an empty dashboard.
replace_once(
    "apps/desktop/src/hooks/useRunners.ts",
    '''  const initialFetch = useRef(true);
''',
    '''  const initialFetch = useRef(true);
  const refreshGeneration = useRef(0);
''',
)
regex_once(
    "apps/desktop/src/hooks/useRunners.ts",
    r'''  const refresh = useCallback\(async \(\) => \{.*?\n  \}, \[\]\);''',
    '''  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    try {
      const data = await api.listRunners();
      if (generation !== refreshGeneration.current) return;
      setRunners(data);
      setError(null);
    } catch (e) {
      if (generation === refreshGeneration.current) setError(String(e));
    } finally {
      if (generation === refreshGeneration.current && initialFetch.current) {
        initialFetch.current = false;
        setLoading(false);
      }
    }
  }, []);''',
)

# Repository polling has the same stale-response and transient-empty problems.
replace_once(
    "apps/desktop/src/hooks/useRepos.ts",
    '''import { useState, useEffect, useCallback } from "react";''',
    '''import { useState, useEffect, useCallback, useRef } from "react";''',
)
replace_once(
    "apps/desktop/src/hooks/useRepos.ts",
    '''  const { handleUnauthorized } = useAuth();
''',
    '''  const { handleUnauthorized } = useAuth();
  const refreshGeneration = useRef(0);
''',
)
regex_once(
    "apps/desktop/src/hooks/useRepos.ts",
    r'''  const refresh = useCallback\(async \(\) => \{.*?\n  \}, \[enabled, handleUnauthorized\]\);''',
    '''  const refresh = useCallback(async () => {
    if (!enabled) {
      refreshGeneration.current += 1;
      setRepos([]);
      setError(null);
      setLoading(false);
      return;
    }
    const generation = ++refreshGeneration.current;
    try {
      const data = await api.listRepos();
      if (generation !== refreshGeneration.current) return;
      setError(null);
      setRepos(data);
    } catch (e) {
      if (generation !== refreshGeneration.current) return;
      const msg = String(e);
      if (msg.includes("401") || msg.includes("UNAUTHORIZED")) {
        handleUnauthorized();
      }
      setError(msg);
    } finally {
      if (generation === refreshGeneration.current) setLoading(false);
    }
  }, [enabled, handleUnauthorized]);''',
)

# Preserve metrics/log data across a single failed poll and reject late log
# responses after filters change.
replace_once(
    "apps/desktop/src/hooks/useMetrics.ts",
    '''      setError(String(e));
      setMetrics(null);''',
    '''      setError(String(e));''',
)
replace_once(
    "apps/desktop/src/hooks/useDaemonLogs.ts",
    '''  const lastTimestampRef = useRef<string | null>(null);
''',
    '''  const lastTimestampRef = useRef<string | null>(null);
  const refreshGeneration = useRef(0);
''',
)
regex_once(
    "apps/desktop/src/hooks/useDaemonLogs.ts",
    r'''  const fetchLogs = useCallback\(async \(\) => \{.*?\n  \}, \[level, search\]\);''',
    '''  const fetchLogs = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    try {
      const entries = await api.getDaemonLogsRecent(level, 2000, search || undefined);
      if (generation !== refreshGeneration.current) return;
      setLogs(entries);
      if (entries.length > 0) {
        lastTimestampRef.current = entries[entries.length - 1].timestamp;
      }
      setError(null);
    } catch (e) {
      if (generation === refreshGeneration.current) setError(String(e));
    } finally {
      if (generation === refreshGeneration.current) setLoading(false);
    }
  }, [level, search]);''',
)

# Scan cancellation must work even while the start command is still returning
# operation IDs, and a second scan must not orphan the first one's IDs.
replace_once(
    "apps/desktop/src/hooks/useScan.ts",
    '''  const scanningRef = useRef(false);
''',
    '''  const scanningRef = useRef(false);
  const cancelRequested = useRef(false);
''',
)
replace_once(
    "apps/desktop/src/hooks/useScan.ts",
    '''      const { workspacePath, authenticated } = options;

      if (!workspacePath && !authenticated) {''',
    '''      const { workspacePath, authenticated } = options;

      if (scanningRef.current || launchPending.current) return;
      if (!workspacePath && !authenticated) {''',
)
replace_once(
    "apps/desktop/src/hooks/useScan.ts",
    '''      activeScanIds.current.clear();
      launchPending.current = true;''',
    '''      activeScanIds.current.clear();
      cancelRequested.current = false;
      launchPending.current = true;''',
)
replace_once(
    "apps/desktop/src/hooks/useScan.ts",
    '''        for (const id of scanIds) activeScanIds.current.add(id);
        launchPending.current = false;
        await finishIfIdle();''',
    '''        for (const id of scanIds) activeScanIds.current.add(id);
        launchPending.current = false;
        if (cancelRequested.current) {
          setProgressText("Cancelling scan...");
          await Promise.allSettled(scanIds.map((id) => api.cancelScan(id)));
        }
        await finishIfIdle();''',
)
replace_once(
    "apps/desktop/src/hooks/useScan.ts",
    '''  const cancelScan = useCallback(async () => {
    const ids = [...activeScanIds.current];
    if (ids.length === 0) return;
    setProgressText("Cancelling scan...");''',
    '''  const cancelScan = useCallback(async () => {
    cancelRequested.current = true;
    const ids = [...activeScanIds.current];
    setProgressText("Cancelling scan...");
    if (ids.length === 0) return;''',
)
replace_once(
    "apps/desktop/src/hooks/useScan.ts",
    '''      scanningRef.current = false;
      setScanning(false);''',
    '''      scanningRef.current = false;
      cancelRequested.current = false;
      setScanning(false);''',
)

# Auto-scan cannot start over an active scan. Filter pills become real keyboard-
# accessible controls instead of click-only spans.
replace_once(
    "apps/desktop/src/pages/Repositories.tsx",
    '''    if (preferences?.auto_scan && (preferences.workspace_path || auth.authenticated)) {''',
    '''    if (
      !scanning &&
      preferences?.auto_scan &&
      (preferences.workspace_path || auth.authenticated)
    ) {''',
)
replace_once(
    "apps/desktop/src/pages/Repositories.tsx",
    '''  }, [preferences?.auto_scan, preferences?.workspace_path, auth.authenticated, runScan]);''',
    '''  }, [
    scanning,
    preferences?.auto_scan,
    preferences?.workspace_path,
    auth.authenticated,
    runScan,
  ]);''',
)
replace_once(
    "apps/desktop/src/pages/Repositories.tsx",
    '''    <span
      onClick={onClick}
      style={{''',
    '''    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      style={{''',
)
replace_once(
    "apps/desktop/src/pages/Repositories.tsx",
    '''        border: `1px solid ${active ? "rgba(31, 111, 235, 0.4)" : "var(--border)"}`,
      }}
    >
      {label}
    </span>''',
    '''        border: `1px solid ${active ? "rgba(31, 111, 235, 0.4)" : "var(--border)"}`,
        fontFamily: "inherit",
      }}
    >
      {label}
    </button>''',
)

# Mini view must not present creating/registering/stopping/deleting runners as
# online. Expose transitional count explicitly.
regex_once(
    "apps/desktop/src/pages/MiniView.tsx",
    r'''function countByState\(runners: RunnerInfo\[\]\): Record<string, number> \{.*?\n\}''',
    '''function countByState(runners: RunnerInfo[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const runner of runners) {
    const key =
      runner.state === "busy"
        ? "busy"
        : runner.state === "online"
          ? "online"
          : runner.state === "offline" || runner.state === "error"
            ? "offline"
            : "transitioning";
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}''',
)
replace_once(
    "apps/desktop/src/pages/MiniView.tsx",
    '''          {(counts.offline || 0) > 0 && (
            <span className="mini-count offline">{counts.offline} off</span>
          )}''',
    '''          {(counts.offline || 0) > 0 && (
            <span className="mini-count offline">{counts.offline} off</span>
          )}
          {(counts.transitioning || 0) > 0 && (
            <span className="mini-count">{counts.transitioning} changing</span>
          )}''',
)

# Runner detail uses the shared lifecycle reservation state and protects log
# polling from late responses.
replace_once(
    "apps/desktop/src/pages/RunnerDetail.tsx",
    '''  const { runners, loading, startRunner, stopRunner, restartRunner, deleteRunner } =
    useOutletContext<RunnersContextType>();''',
    '''  const {
    runners,
    loading,
    pendingActions,
    startRunner,
    stopRunner,
    restartRunner,
    deleteRunner,
  } = useOutletContext<RunnersContextType>();''',
)
replace_once(
    "apps/desktop/src/pages/RunnerDetail.tsx",
    '''  const logContainerRef = useRef<HTMLDivElement>(null);
''',
    '''  const logContainerRef = useRef<HTMLDivElement>(null);
  const logsGeneration = useRef(0);
''',
)
replace_once(
    "apps/desktop/src/pages/RunnerDetail.tsx",
    '''    async function fetchLogs() {
      try {
        const entries = await api.getRunnerLogs(id!);
        setLogs(entries);
      } catch {''',
    '''    async function fetchLogs() {
      const generation = ++logsGeneration.current;
      try {
        const entries = await api.getRunnerLogs(id!);
        if (generation === logsGeneration.current) setLogs(entries);
      } catch {''',
)
replace_once(
    "apps/desktop/src/pages/RunnerDetail.tsx",
    '''  const canRestart = isRunning || isStopped;
  const canDelete = !isTransient && state !== "busy";

  async function doAction(fn: () => Promise<void>) {
    if (deleting) return;''',
    '''  const canRestart = isRunning || isStopped;
  const canDelete = !isTransient && state !== "busy";
  const actionPending = pendingActions.has(config.id);

  async function doAction(fn: () => Promise<void>) {
    if (deleting || actionPending) return;''',
)
text = read("apps/desktop/src/pages/RunnerDetail.tsx")
text = text.replace("(isTransient || deleting)", "(isTransient || deleting || actionPending)")
text = text.replace("disabled={deleting}", "disabled={deleting || actionPending}")
text = text.replace("disabled={!canRestart || deleting}", "disabled={!canRestart || deleting || actionPending}")
text = text.replace("disabled={!canDelete || deleting}", "disabled={!canDelete || deleting || actionPending}")
write("apps/desktop/src/pages/RunnerDetail.tsx", text)

# Job step/history polling also rejects stale responses and avoids overlapping
# requests during slow GitHub log retrieval.
replace_once(
    "apps/desktop/src/hooks/useJobSteps.ts",
    '''  const logCacheRef = useRef<Record<number, string[]>>({});
''',
    '''  const logCacheRef = useRef<Record<number, string[]>>({});
  const stepsGeneration = useRef(0);
  const logsGeneration = useRef(0);
''',
)
replace_once(
    "apps/desktop/src/hooks/useJobSteps.ts",
    '''    const fetchSteps = async () => {
      try {
        const data = await api.getRunnerSteps(runnerId);
        setStepsResponse(data);''',
    '''    const fetchSteps = async () => {
      const generation = ++stepsGeneration.current;
      try {
        const data = await api.getRunnerSteps(runnerId);
        if (generation === stepsGeneration.current) setStepsResponse(data);''',
)
replace_once(
    "apps/desktop/src/hooks/useJobSteps.ts",
    '''      } finally {
        setLoading(false);
      }
    };''',
    '''      } finally {
        if (generation === stepsGeneration.current) setLoading(false);
      }
    };''',
)
replace_once(
    "apps/desktop/src/hooks/useJobSteps.ts",
    '''    const fetchLogs = async () => {
      try {
        const data = await api.getStepLogs(runnerId, expandedStep);
        const lines = data.lines;''',
    '''    const fetchLogs = async () => {
      const generation = ++logsGeneration.current;
      try {
        const data = await api.getStepLogs(runnerId, expandedStep);
        if (generation !== logsGeneration.current) return;
        const lines = data.lines;''',
)
replace_once(
    "apps/desktop/src/hooks/useJobHistory.ts",
    '''import { useState, useEffect, useCallback } from "react";''',
    '''import { useState, useEffect, useCallback, useRef } from "react";''',
)
replace_once(
    "apps/desktop/src/hooks/useJobHistory.ts",
    '''  const [loading, setLoading] = useState(false);
''',
    '''  const [loading, setLoading] = useState(false);
  const refreshGeneration = useRef(0);
''',
)
replace_once(
    "apps/desktop/src/hooks/useJobHistory.ts",
    '''    setLoading(true);
    try {
      const entries = await api.getRunnerHistory(runnerId);
      setHistory(entries);''',
    '''    const generation = ++refreshGeneration.current;
    setLoading(true);
    try {
      const entries = await api.getRunnerHistory(runnerId);
      if (generation === refreshGeneration.current) setHistory(entries);''',
)
replace_once(
    "apps/desktop/src/hooks/useJobHistory.ts",
    '''    } finally {
      setLoading(false);
    }''',
    '''    } finally {
      if (generation === refreshGeneration.current) setLoading(false);
    }''',
)

# Settings mutations are single-flight. Text fields save on blur, service changes
# have visible pending/error states, and switches are proper accessible buttons.
replace_once(
    "apps/desktop/src/pages/Settings.tsx",
    '''  const { auth, loading, loginWithToken, logout, refresh } = useAuth();''',
    '''  const { auth, loading, error: authError, loginWithToken, logout, refresh } = useAuth();''',
)
replace_once(
    "apps/desktop/src/pages/Settings.tsx",
    '''  const [launchAtLogin, setLaunchAtLogin] = useState(false);
  const [preferences, setPreferences] = useState<Preferences>({''',
    '''  const [launchAtLogin, setLaunchAtLogin] = useState(false);
  const [launchAtLoginSaving, setLaunchAtLoginSaving] = useState(false);
  const [launchAtLoginError, setLaunchAtLoginError] = useState<string | null>(null);
  const [preferencesSaving, setPreferencesSaving] = useState(false);
  const [preferencesError, setPreferencesError] = useState<string | null>(null);
  const [workspaceInput, setWorkspaceInput] = useState("");
  const [preferences, setPreferences] = useState<Preferences>({''',
)
replace_once(
    "apps/desktop/src/pages/Settings.tsx",
    '''    invoke<boolean>("service_status")
      .then(setLaunchAtLogin)
      .catch(() => {});
    api
      .getPreferences()
      .then(setPreferences)
      .catch(() => {});''',
    '''    invoke<boolean>("service_status")
      .then(setLaunchAtLogin)
      .catch((error) => setLaunchAtLoginError(String(error)));
    api
      .getPreferences()
      .then((saved) => {
        setPreferences(saved);
        setWorkspaceInput(saved.workspace_path ?? "");
      })
      .catch((error) => setPreferencesError(String(error)));''',
)
regex_once(
    "apps/desktop/src/pages/Settings.tsx",
    r'''  function updatePreference\(key: keyof Preferences, value: boolean\) \{.*?\n  \}\n\n  function updatePreferences\(updates: Partial<Preferences>\) \{.*?\n  \}''',
    '''  async function persistPreferences(updated: Preferences) {
    if (preferencesSaving) return;
    const previous = preferences;
    setPreferences(updated);
    setPreferencesSaving(true);
    setPreferencesError(null);
    try {
      const saved = await api.updatePreferences(updated);
      setPreferences(saved);
      setWorkspaceInput(saved.workspace_path ?? "");
    } catch (error) {
      setPreferences(previous);
      setWorkspaceInput(previous.workspace_path ?? "");
      setPreferencesError(String(error));
    } finally {
      setPreferencesSaving(false);
    }
  }

  function updatePreference(key: keyof Preferences, value: boolean) {
    void persistPreferences({ ...preferences, [key]: value });
  }

  function updatePreferences(updates: Partial<Preferences>) {
    void persistPreferences({ ...preferences, ...updates });
  }''',
)
replace_once(
    "apps/desktop/src/pages/Settings.tsx",
    '''  async function handleLogout() {
    await logout();
    setTokenSuccess(false);
    setDeviceFlow({ stage: "idle" });
  }''',
    '''  async function handleLogout() {
    try {
      await logout();
      setTokenSuccess(false);
      setDeviceFlow({ stage: "idle" });
    } catch (error) {
      setTokenError(String(error));
    }
  }''',
)
replace_once(
    "apps/desktop/src/pages/Settings.tsx",
    '''      <div className="page-header">
        <h1 className="page-title">Settings</h1>
      </div>
''',
    '''      <div className="page-header">
        <h1 className="page-title">Settings</h1>
      </div>

      {(authError || preferencesError || launchAtLoginError) && (
        <div className="error-banner" style={{ marginBottom: 16 }}>
          {authError || preferencesError || launchAtLoginError}
        </div>
      )}
''',
)
replace_once(
    "apps/desktop/src/pages/Settings.tsx",
    '''             checked={launchAtLogin}
             onChange={async (checked) => {
               try {
                 if (checked) {
                   await invoke("install_service");
                 } else {
                   await invoke("uninstall_service");
                 }
                 setLaunchAtLogin(checked);
               } catch (e) {
                 console.error("Failed to toggle launch at login:", e);
               }
             }}'''.replace("             ", "            "),
    '''            checked={launchAtLogin}
            disabled={launchAtLoginSaving}
            onChange={async (checked) => {
              if (launchAtLoginSaving) return;
              setLaunchAtLoginSaving(true);
              setLaunchAtLoginError(null);
              try {
                if (checked) await invoke("install_service");
                else await invoke("uninstall_service");
                setLaunchAtLogin(checked);
              } catch (error) {
                setLaunchAtLoginError(String(error));
              } finally {
                setLaunchAtLoginSaving(false);
              }
            }}''',
)
# Disable all preference-backed toggles while a save is outstanding.
settings = read("apps/desktop/src/pages/Settings.tsx")
settings = settings.replace(
    'checked={preferences.start_runners_on_launch}\n            onChange=',
    'checked={preferences.start_runners_on_launch}\n            disabled={preferencesSaving}\n            onChange=',
)
settings = settings.replace(
    'checked={preferences.notify_status_changes}\n            onChange=',
    'checked={preferences.notify_status_changes}\n            disabled={preferencesSaving}\n            onChange=',
)
settings = settings.replace(
    'checked={preferences.notify_job_completions}\n            onChange=',
    'checked={preferences.notify_job_completions}\n            disabled={preferencesSaving}\n            onChange=',
)
settings = settings.replace(
    'checked={preferences.auto_scan}\n            onChange=',
    'checked={preferences.auto_scan}\n            disabled={preferencesSaving}\n            onChange=',
)
settings = settings.replace(
    '''value={preferences.workspace_path ?? ""}
                onChange={(e) => updatePreferences({ workspace_path: e.target.value || null })}''',
    '''value={workspaceInput}
                disabled={preferencesSaving}
                onChange={(e) => setWorkspaceInput(e.target.value)}
                onBlur={() =>
                  updatePreferences({ workspace_path: workspaceInput.trim() || null })
                }
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.currentTarget.blur();
                }}''',
)
settings = settings.replace(
    '''value={labelsInput}
                onChange={(e) => setLabelsInput(e.target.value)}''',
    '''value={labelsInput}
                disabled={preferencesSaving}
                onChange={(e) => setLabelsInput(e.target.value)}''',
)
write("apps/desktop/src/pages/Settings.tsx", settings)

regex_once(
    "apps/desktop/src/pages/Settings.tsx",
    r'''function ToggleSetting\(\{.*?\n\}\n\nfunction Divider''',
    '''function ToggleSetting({
  label,
  description,
  checked,
  disabled = false,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between" style={{ padding: "8px 0" }}>
      <div style={{ flex: 1, marginRight: 24 }}>
        <div style={{ fontWeight: 500, marginBottom: 2, fontSize: 14 }}>{label}</div>
        <p className="text-muted" style={{ margin: 0, fontSize: 12 }}>
          {description}
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        style={{
          width: 40,
          height: 22,
          padding: 0,
          background: checked ? "var(--accent-green)" : "var(--bg-tertiary)",
          border: `1px solid ${checked ? "var(--accent-green)" : "var(--border)"}`,
          borderRadius: 11,
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.6 : 1,
          flexShrink: 0,
          position: "relative",
          transition: "background 0.2s, border-color 0.2s",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: 18,
            height: 18,
            background: checked ? "white" : "var(--text-secondary)",
            borderRadius: "50%",
            position: "absolute",
            top: 1,
            left: checked ? 19 : 1,
            transition: "left 0.2s, background 0.2s",
          }}
        />
      </button>
    </div>
  );
}

function Divider''',
)

print("frontend audit patch applied")
