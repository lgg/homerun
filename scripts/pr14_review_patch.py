from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        if new in text:
            return
        raise SystemExit(f"pattern not found in {path}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


# Cross-platform warning safety and rollback-aware Windows cleanup.
persistence = read("crates/daemon/src/persistence.rs")
persistence = persistence.replace(
    "use anyhow::{Context, Result};\nuse std::fs::{File, OpenOptions};",
    "use anyhow::{anyhow, Context, Result};\n#[cfg(unix)]\nuse std::fs::File;\nuse std::fs::OpenOptions;",
)
persistence = persistence.replace(
    '''    let parent = path
        .parent()
        .context("Persistence path has no parent directory")?;''',
    '''    #[cfg(windows)]
    let _ = unix_mode;

    let parent = path
        .parent()
        .context("Persistence path has no parent directory")?;''',
    1,
)
persistence = persistence.replace(
    '''        Ok(()) => {
            if had_destination {
                let _ = std::fs::remove_file(&backup);
            }
            Ok(())
        }''',
    '''        Ok(()) => {
            if had_destination {
                if let Err(cleanup_error) = std::fs::remove_file(&backup) {
                    let rollback = std::fs::remove_file(destination)
                        .and_then(|_| std::fs::rename(&backup, destination));
                    return match rollback {
                        Ok(()) => Err(cleanup_error).with_context(|| {
                            format!(
                                "Failed to remove staged persistence backup {}; update was rolled back",
                                backup.display()
                            )
                        }),
                        Err(rollback_error) => Err(anyhow!(
                            "Failed to remove staged backup {} ({cleanup_error}) and failed to restore it ({rollback_error})",
                            backup.display()
                        )),
                    };
                }
            }
            Ok(())
        }''',
)
persistence = persistence.replace(
    '''    #[test]
    fn atomic_write_replaces_existing_file_and_cleans_temporary_file() {''',
    '''    #[test]
    fn atomic_write_creates_new_file() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("new-state.json");

        atomic_write(&path, b"created", Some(0o600)).unwrap();

        assert_eq!(std::fs::read(&path).unwrap(), b"created");
    }

    #[test]
    fn atomic_write_replaces_existing_file_and_cleans_temporary_file() {''',
)
persistence = persistence.replace(
    '''        assert_eq!(leftovers, 0);
    }
}''',
    '''        assert_eq!(leftovers, 0);
    }

    #[cfg(unix)]
    #[test]
    fn atomic_write_applies_restrictive_mode_at_creation() {
        use std::os::unix::fs::PermissionsExt;

        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("secret");
        atomic_write(&path, b"token", Some(0o600)).unwrap();

        assert_eq!(std::fs::metadata(path).unwrap().permissions().mode() & 0o777, 0o600);
    }
}''',
)
write("crates/daemon/src/persistence.rs", persistence)

# Public credential operations must never touch a developer's real auth file
# from daemon unit tests. Path-specific helpers remain fully tested.
keychain = read("crates/daemon/src/auth/keychain.rs")
keychain = keychain.replace(
    '''pub fn store_token(_service: &str, _account: &str, token: &str) -> Result<()> {
    store_token_at(&auth_file_path(), token)
}

pub fn get_token(_service: &str, _account: &str) -> Result<Option<String>> {
    get_token_at(&auth_file_path())
}

pub fn delete_token(_service: &str, _account: &str) -> Result<()> {
    delete_token_at(&auth_file_path())
}''',
    '''#[cfg(not(test))]
pub fn store_token(_service: &str, _account: &str, token: &str) -> Result<()> {
    store_token_at(&auth_file_path(), token)
}

#[cfg(test)]
pub fn store_token(_service: &str, _account: &str, _token: &str) -> Result<()> {
    Ok(())
}

#[cfg(not(test))]
pub fn get_token(_service: &str, _account: &str) -> Result<Option<String>> {
    get_token_at(&auth_file_path())
}

#[cfg(test)]
pub fn get_token(_service: &str, _account: &str) -> Result<Option<String>> {
    Ok(None)
}

#[cfg(not(test))]
pub fn delete_token(_service: &str, _account: &str) -> Result<()> {
    delete_token_at(&auth_file_path())
}

#[cfg(test)]
pub fn delete_token(_service: &str, _account: &str) -> Result<()> {
    Ok(())
}''',
)
keychain = keychain.replace(
    '''        let retrieved = get_token_at(&path).unwrap();
        assert_eq!(retrieved, Some(token.to_string()));''',
    '''        let retrieved = get_token_at(&path).unwrap();
        assert_eq!(retrieved, Some(token.to_string()));

        std::fs::write(&path, format!("  {token}\\n")).unwrap();
        assert_eq!(get_token_at(&path).unwrap(), Some(token.to_string()));''',
)
write("crates/daemon/src/auth/keychain.rs", keychain)

# Auth restore/logout ordering: stale restore failures cannot erase a newer
# login, starting a new Device Flow cancels the old one immediately, and logout
# always clears process memory even when credential deletion reports an error.
auth = read("crates/daemon/src/auth/mod.rs")
auth = auth.replace(
    '''            Err(error) => {
                // Keep the credential file because this can be a transient network failure,
                // but do not claim that an unvalidated/expired token is authenticated.
                tracing::warn!(
                    "Could not validate stored token (keeping it for the next restart): {error}"
                );
                *self.state.write().await = None;
            }''',
    '''            Err(error) => {
                // Keep the credential file because this can be a transient network failure,
                // but do not let an older restore attempt erase a newer login.
                let _commit = self.commit_lock.lock().await;
                if self.is_current_attempt(generation) {
                    tracing::warn!(
                        "Could not validate stored token (keeping it for the next restart): {error}"
                    );
                    *self.state.write().await = None;
                }
            }''',
)
auth = auth.replace(
    '''        let _commit = self.commit_lock.lock().await;
        keychain::delete_token(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)?;
        *self.state.write().await = None;
        Ok(())''',
    '''        let _commit = self.commit_lock.lock().await;
        let delete_result = keychain::delete_token(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT);
        *self.state.write().await = None;
        delete_result''',
)
auth = auth.replace(
    '''    pub async fn start_device_flow(&self) -> Result<DeviceFlowResponse> {
        let client = reqwest::Client::new();''',
    '''    pub async fn start_device_flow(&self) -> Result<DeviceFlowResponse> {
        // A newly requested code supersedes any older poll immediately, even
        // while GitHub is still returning this new code.
        self.begin_auth_attempt();
        let client = reqwest::Client::new();''',
)
auth = auth.replace(
    '''        // Logout — may fail on keychain, but state should still be cleared
        let _ = manager.logout().await;
        let status = manager.status().await;
        assert!(!status.authenticated);''',
    '''        manager.logout().await.unwrap();
        let status = manager.status().await;
        assert!(!status.authenticated);''',
)
write("crates/daemon/src/auth/mod.rs", auth)

# Frontend auth mirrors daemon logout semantics: an on-disk deletion error is
# surfaced, while the already-cleared in-memory session is reflected at once.
auth_context = read("apps/desktop/src/hooks/AuthContext.tsx")
auth_context = auth_context.replace(
    '''    } catch (e) {
      const message = String(e);
      setError(message);
      throw new Error(message);
    }
  }, []);''',
    '''    } catch (e) {
      const message = String(e);
      operationGeneration.current += 1;
      setAuth({ authenticated: false, user: null });
      setError(message);
      throw new Error(message);
    }
  }, []);''',
    1,
)
write("apps/desktop/src/hooks/AuthContext.tsx", auth_context)

# Serial runner refreshes and lifecycle calls: polling, WebSocket events and UI
# actions share one queued refresh, while duplicate actions share one promise.
write(
    "apps/desktop/src/hooks/useRunners.ts",
    '''import { useState, useEffect, useCallback, useRef } from "react";
import type {
  BatchCreateResponse,
  CreateBatchRequest,
  CreateRunnerRequest,
  GroupActionResponse,
  RunnerInfo,
  ScaleGroupResponse,
} from "../api/types";
import { api } from "../api/commands";
import { useEvents } from "./useEvents";

export function useRunners() {
  const [runners, setRunners] = useState<RunnerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingActions, setPendingActions] = useState<Set<string>>(new Set());
  const initialFetch = useRef(true);
  const refreshGeneration = useRef(0);
  const refreshPromise = useRef<Promise<void> | null>(null);
  const refreshQueued = useRef(false);
  const actionPromises = useRef(new Map<string, Promise<unknown>>());

  const refresh = useCallback((): Promise<void> => {
    if (refreshPromise.current) {
      refreshQueued.current = true;
      return refreshPromise.current;
    }

    const promise = (async () => {
      do {
        refreshQueued.current = false;
        const generation = ++refreshGeneration.current;
        try {
          const data = await api.listRunners();
          if (generation !== refreshGeneration.current) continue;
          setRunners(data);
          setError(null);
        } catch (cause) {
          if (generation === refreshGeneration.current) setError(String(cause));
        } finally {
          if (generation === refreshGeneration.current && initialFetch.current) {
            initialFetch.current = false;
            setLoading(false);
          }
        }
      } while (refreshQueued.current);
    })();

    refreshPromise.current = promise;
    void promise.finally(() => {
      if (refreshPromise.current === promise) refreshPromise.current = null;
    });
    return promise;
  }, []);

  useEffect(() => {
    void refresh();
    const interval = setInterval(() => void refresh(), 2000);
    return () => {
      clearInterval(interval);
      refreshGeneration.current += 1;
    };
  }, [refresh]);

  useEvents(refresh);

  const runPending = useCallback(
    function runPending<T>(id: string, operation: () => Promise<T>): Promise<T> {
      const existing = actionPromises.current.get(id) as Promise<T> | undefined;
      if (existing) return existing;

      setPendingActions((previous) => new Set(previous).add(id));
      const promise = operation().finally(() => {
        actionPromises.current.delete(id);
        setPendingActions((previous) => {
          const next = new Set(previous);
          next.delete(id);
          return next;
        });
      });
      actionPromises.current.set(id, promise);
      return promise;
    },
    [],
  );

  const createRunner = useCallback(
    async (request: CreateRunnerRequest) => {
      const runner = await api.createRunner(request);
      await refresh();
      return runner;
    },
    [refresh],
  );

  const deleteRunner = useCallback(
    (id: string) =>
      runPending(id, async () => {
        await api.deleteRunner(id);
        await refresh();
      }),
    [refresh, runPending],
  );

  const startRunner = useCallback(
    (id: string) =>
      runPending(id, async () => {
        await api.startRunner(id);
        await refresh();
      }),
    [refresh, runPending],
  );

  const stopRunner = useCallback(
    (id: string) =>
      runPending(id, async () => {
        await api.stopRunner(id);
        await refresh();
      }),
    [refresh, runPending],
  );

  const restartRunner = useCallback(
    (id: string) =>
      runPending(id, async () => {
        await api.restartRunner(id);
        await refresh();
      }),
    [refresh, runPending],
  );

  const createBatch = useCallback(
    async (request: CreateBatchRequest): Promise<BatchCreateResponse> => {
      const result = await api.createBatch(request);
      await refresh();
      return result;
    },
    [refresh],
  );

  const startGroup = useCallback(
    (groupId: string): Promise<GroupActionResponse> =>
      runPending(groupId, async () => {
        const result = await api.startGroup(groupId);
        await refresh();
        return result;
      }),
    [refresh, runPending],
  );

  const stopGroup = useCallback(
    (groupId: string): Promise<GroupActionResponse> =>
      runPending(groupId, async () => {
        const result = await api.stopGroup(groupId);
        await refresh();
        return result;
      }),
    [refresh, runPending],
  );

  const restartGroup = useCallback(
    (groupId: string): Promise<GroupActionResponse> =>
      runPending(groupId, async () => {
        const result = await api.restartGroup(groupId);
        await refresh();
        return result;
      }),
    [refresh, runPending],
  );

  const deleteGroup = useCallback(
    (groupId: string): Promise<GroupActionResponse> =>
      runPending(groupId, async () => {
        const result = await api.deleteGroup(groupId);
        await refresh();
        return result;
      }),
    [refresh, runPending],
  );

  const scaleGroup = useCallback(
    (groupId: string, count: number): Promise<ScaleGroupResponse> =>
      runPending(groupId, async () => {
        const result = await api.scaleGroup(groupId, count);
        await refresh();
        return result;
      }),
    [refresh, runPending],
  );

  return {
    runners,
    loading,
    error,
    refresh,
    pendingActions,
    createRunner,
    deleteRunner,
    startRunner,
    stopRunner,
    restartRunner,
    createBatch,
    startGroup,
    stopGroup,
    restartGroup,
    deleteGroup,
    scaleGroup,
  };
}

export type RunnersContextType = ReturnType<typeof useRunners> & {
  daemonStarting: boolean;
  handleStartDaemon: () => Promise<void>;
};
''',
)

# Disabling remote repositories invalidates any request started while enabled.
repos = read("apps/desktop/src/hooks/useRepos.ts")
repos = repos.replace(
    '''    if (!enabled) {
      setRepos([]);''',
    '''    if (!enabled) {
      refreshGeneration.current += 1;
      setRepos([]);''',
    1,
)
repos = repos.replace(
    '''    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);''',
    '''    const interval = setInterval(refresh, 5000);
    return () => {
      clearInterval(interval);
      refreshGeneration.current += 1;
    };''',
)
write("apps/desktop/src/hooks/useRepos.ts", repos)

# Scan session bookkeeping handles terminal events that beat the start command
# response and filters provisional stale starts after the expected IDs arrive.
write(
    "apps/desktop/src/hooks/useScan.ts",
    '''import { useState, useEffect, useCallback, useRef } from "react";
import { listen } from "@tauri-apps/api/event";
import type { DiscoveredRepo, ScanProgressEvent } from "../api/types";
import { api } from "../api/commands";

interface ScanOptions {
  workspacePath: string | null;
  authenticated: boolean;
}

export function useScan() {
  const [discoveredRepos, setDiscoveredRepos] = useState<DiscoveredRepo[]>([]);
  const [scanning, setScanning] = useState(false);
  const [lastScanAt, setLastScanAt] = useState<string | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  const [progressText, setProgressText] = useState<string | null>(null);
  const activeScanIds = useRef(new Set<string>());
  const expectedScanIds = useRef(new Set<string>());
  const terminalScanIds = useRef(new Set<string>());
  const launchPending = useRef(false);
  const scanningRef = useRef(false);
  const cancelRequested = useRef(false);

  const refreshResults = useCallback(async () => {
    const results = await api.getScanResults();
    if (results) {
      setDiscoveredRepos(results.merged_results);
      setLastScanAt(results.last_scan_at);
    }
  }, []);

  const finishIfIdle = useCallback(async () => {
    if (launchPending.current || activeScanIds.current.size > 0) return;
    try {
      await refreshResults();
    } catch (error) {
      setScanError((current) => current ?? String(error));
    } finally {
      scanningRef.current = false;
      cancelRequested.current = false;
      expectedScanIds.current.clear();
      terminalScanIds.current.clear();
      setScanning(false);
      setProgressText(null);
    }
  }, [refreshResults]);

  useEffect(() => {
    refreshResults().catch(() => {});
  }, [refreshResults]);

  useEffect(() => {
    const unlisten = listen<string>("scan-progress", (event) => {
      try {
        const data: ScanProgressEvent = JSON.parse(event.payload);
        if (!scanningRef.current) return;

        if (data.type === "started") {
          if (!launchPending.current && !expectedScanIds.current.has(data.scan_id)) return;
          if (terminalScanIds.current.has(data.scan_id)) return;
          activeScanIds.current.add(data.scan_id);
          setProgressText(`Starting ${data.scan_type} scan (${data.total} repositories)...`);
          return;
        }
        if (!activeScanIds.current.has(data.scan_id)) return;

        switch (data.type) {
          case "checking":
            setProgressText(
              `Scanning ${data.repo} (${data.index}/${data.total}) via ${data.scan_type} discovery...`,
            );
            break;
          case "found":
            setProgressText(`Found ${data.full_name}`);
            break;
          case "warning":
            setScanError((current) =>
              [current, `${data.repo ?? data.scan_type}: ${data.message}`]
                .filter(Boolean)
                .join("\\n"),
            );
            break;
          case "done":
          case "cancelled":
            terminalScanIds.current.add(data.scan_id);
            activeScanIds.current.delete(data.scan_id);
            void finishIfIdle();
            break;
          case "failed":
            terminalScanIds.current.add(data.scan_id);
            activeScanIds.current.delete(data.scan_id);
            setScanError((current) =>
              [current, `${data.scan_type} scan failed: ${data.message}`]
                .filter(Boolean)
                .join("\\n"),
            );
            void finishIfIdle();
            break;
        }
      } catch {
        // Ignore malformed events from an incompatible/older daemon.
      }
    });

    return () => {
      void unlisten.then((fn) => fn()).catch(() => {});
    };
  }, [finishIfIdle]);

  const runScan = useCallback(
    async (options: ScanOptions) => {
      const { workspacePath, authenticated } = options;

      if (scanningRef.current || launchPending.current) return;
      if (!workspacePath && !authenticated) {
        setScanError("Configure a workspace path or sign in to scan.");
        return;
      }

      activeScanIds.current.clear();
      expectedScanIds.current.clear();
      terminalScanIds.current.clear();
      cancelRequested.current = false;
      launchPending.current = true;
      scanningRef.current = true;
      setScanning(true);
      setScanError(null);
      setProgressText("Starting scan...");

      try {
        const scanIds = await api.startScan(workspacePath, authenticated);
        expectedScanIds.current = new Set(scanIds);
        activeScanIds.current = new Set(
          scanIds.filter((scanId) => !terminalScanIds.current.has(scanId)),
        );
        launchPending.current = false;
        if (cancelRequested.current) {
          setProgressText("Cancelling scan...");
          await Promise.allSettled(scanIds.map((scanId) => api.cancelScan(scanId)));
        }
        await finishIfIdle();
      } catch (error) {
        launchPending.current = false;
        activeScanIds.current.clear();
        expectedScanIds.current.clear();
        terminalScanIds.current.clear();
        cancelRequested.current = false;
        scanningRef.current = false;
        setScanError(String(error));
        setScanning(false);
        setProgressText(null);
      }
    },
    [finishIfIdle],
  );

  const cancelScan = useCallback(async () => {
    cancelRequested.current = true;
    const ids = [...activeScanIds.current];
    setProgressText("Cancelling scan...");
    if (ids.length === 0) return;
    const results = await Promise.allSettled(ids.map((id) => api.cancelScan(id)));
    const failures = results.filter((result) => result.status === "rejected");
    if (failures.length > 0) {
      setScanError(`Failed to cancel ${failures.length} scan operation(s).`);
    }
  }, []);

  const clearResults = useCallback(() => {
    setDiscoveredRepos([]);
    setLastScanAt(null);
    setScanError(null);
    setProgressText(null);
  }, []);

  return {
    discoveredRepos,
    scanning,
    lastScanAt,
    scanError,
    progressText,
    runScan,
    cancelScan,
    clearResults,
  };
}
''',
)

# Auto-scan is exactly once per page load; a manual scan started while
# preferences load also consumes that one auto-scan attempt.
repositories = read("apps/desktop/src/pages/Repositories.tsx")
repositories = repositories.replace(
    'import { useState, useMemo, useEffect } from "react";',
    'import { useState, useMemo, useEffect, useRef } from "react";',
)
repositories = repositories.replace(
    '''  const [sourceFilter, setSourceFilter] = useState<string | null>(null);
''',
    '''  const [sourceFilter, setSourceFilter] = useState<string | null>(null);
  const autoScanAttempted = useRef(false);
''',
)
repositories = re.sub(
    r'''  // Auto-scan on mount if enabled\n  useEffect\(\(\) => \{.*?\n  \}, \[scanning, preferences\?\.auto_scan, preferences\?\.workspace_path, auth\.authenticated, runScan\]\);''',
    '''  // Auto-scan at most once for this page mount. Depending directly on the
  // scanning state without this latch would start a new scan after every finish.
  useEffect(() => {
    if (!preferences || autoScanAttempted.current) return;
    if (scanning) {
      autoScanAttempted.current = true;
      return;
    }

    autoScanAttempted.current = true;
    if (preferences.auto_scan && (preferences.workspace_path || auth.authenticated)) {
      void runScan({
        workspacePath: preferences.workspace_path,
        authenticated: auth.authenticated,
      });
    }
  }, [scanning, preferences, auth.authenticated, runScan]);''',
    repositories,
    count=1,
    flags=re.S,
)
write("apps/desktop/src/pages/Repositories.tsx", repositories)

# Step and step-log polling is recursive (never overlapping), invalidates stale
# work during runner/state changes, and only restarts log polling when the
# expanded step's status actually changes.
write(
    "apps/desktop/src/hooks/useJobSteps.ts",
    '''import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/commands";
import type { StepInfo, StepsResponse } from "../api/types";

interface UseJobStepsResult {
  steps: StepInfo[];
  stepsDiscovered: number;
  jobName: string | null;
  loading: boolean;
  expandedStep: number | null;
  stepLogs: Record<number, string[]>;
  toggleStep: (stepNumber: number) => void;
}

export function useJobSteps(runnerId: string | undefined, isBusy: boolean): UseJobStepsResult {
  const [stepsResponse, setStepsResponse] = useState<StepsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [expandedStep, setExpandedStep] = useState<number | null>(null);
  const [stepLogs, setStepLogs] = useState<Record<number, string[]>>({});
  const logCacheRef = useRef<Record<number, string[]>>({});
  const stepsGeneration = useRef(0);
  const logsGeneration = useRef(0);

  useEffect(() => {
    const generation = ++stepsGeneration.current;
    if (!isBusy || !runnerId) {
      setStepsResponse(null);
      setExpandedStep(null);
      setStepLogs({});
      logCacheRef.current = {};
      setLoading(false);
      return;
    }

    let cancelled = false;
    let timer: number | undefined;
    setLoading(true);

    const fetchSteps = async () => {
      try {
        const data = await api.getRunnerSteps(runnerId);
        if (!cancelled && generation === stepsGeneration.current) setStepsResponse(data);
      } catch {
        // Ignore polling errors while the runner/job transitions.
      } finally {
        if (!cancelled && generation === stepsGeneration.current) {
          setLoading(false);
          timer = window.setTimeout(() => void fetchSteps(), 1000);
        }
      }
    };

    void fetchSteps();
    return () => {
      cancelled = true;
      stepsGeneration.current += 1;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [isBusy, runnerId]);

  const expandedStepStatus =
    expandedStep === null
      ? null
      : (stepsResponse?.steps.find((step) => step.number === expandedStep)?.status ?? null);

  useEffect(() => {
    const generation = ++logsGeneration.current;
    if (expandedStep === null || !runnerId) return;

    const isRunning = expandedStepStatus === "running";
    const isCached = logCacheRef.current[expandedStep] !== undefined;
    if (isCached && !isRunning) return;

    let cancelled = false;
    let timer: number | undefined;
    const fetchLogs = async () => {
      try {
        const data = await api.getStepLogs(runnerId, expandedStep);
        if (cancelled || generation !== logsGeneration.current) return;
        const lines = data.lines;
        if (!isRunning) logCacheRef.current[expandedStep] = lines;
        setStepLogs((previous) => ({ ...previous, [expandedStep]: lines }));
      } catch {
        if (
          !cancelled &&
          generation === logsGeneration.current &&
          !logCacheRef.current[expandedStep]
        ) {
          setStepLogs((previous) => ({ ...previous, [expandedStep]: [] }));
        }
      } finally {
        if (!cancelled && generation === logsGeneration.current && isRunning) {
          timer = window.setTimeout(() => void fetchLogs(), 5000);
        }
      }
    };

    void fetchLogs();
    return () => {
      cancelled = true;
      logsGeneration.current += 1;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [expandedStep, expandedStepStatus, runnerId]);

  const toggleStep = useCallback((stepNumber: number) => {
    setExpandedStep((previous) => (previous === stepNumber ? null : stepNumber));
  }, []);

  return {
    steps: stepsResponse?.steps ?? [],
    stepsDiscovered: stepsResponse?.steps_discovered ?? 0,
    jobName: stepsResponse?.job_name ?? null,
    loading,
    expandedStep,
    stepLogs,
    toggleStep,
  };
}
''',
)

# Runner logs use completion-driven polling rather than overlapping intervals.
runner_detail = read("apps/desktop/src/pages/RunnerDetail.tsx")
runner_detail = runner_detail.replace(
    '''  useEffect(() => {
    if (!id) return;
    async function fetchLogs() {
      const generation = ++logsGeneration.current;
      try {
        const entries = await api.getRunnerLogs(id!);
        if (generation === logsGeneration.current) setLogs(entries);
      } catch {
        // ignore errors (runner may be offline)
      }
    }
    fetchLogs();
    const timer = setInterval(fetchLogs, 2000);
    return () => clearInterval(timer);
  }, [id]);''',
    '''  useEffect(() => {
    if (!id) return;
    const generation = ++logsGeneration.current;
    let cancelled = false;
    let timer: number | undefined;

    async function fetchLogs() {
      try {
        const entries = await api.getRunnerLogs(id!);
        if (!cancelled && generation === logsGeneration.current) setLogs(entries);
      } catch {
        // Ignore errors while the runner is offline or transitioning.
      } finally {
        if (!cancelled && generation === logsGeneration.current) {
          timer = window.setTimeout(() => void fetchLogs(), 2000);
        }
      }
    }

    void fetchLogs();
    return () => {
      cancelled = true;
      logsGeneration.current += 1;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [id]);''',
)
write("apps/desktop/src/pages/RunnerDetail.tsx", runner_detail)

# Extend scan regression coverage for both early cancellation and terminal-before-
# response ordering.
scan_test = read("apps/desktop/src/hooks/useScan.test.ts")
insert = '''

  it("cancels IDs returned after cancellation was requested", async () => {
    let resolveStart: ((ids: string[]) => void) | undefined;
    mockedApi.startScan.mockReturnValue(
      new Promise<string[]>((resolve) => {
        resolveStart = resolve;
      }),
    );
    const { result } = renderHook(() => useScan());

    let runPromise: Promise<void> | undefined;
    await act(async () => {
      runPromise = result.current.runScan({ workspacePath: "/workspace", authenticated: true });
      await Promise.resolve();
      await result.current.cancelScan();
    });

    await act(async () => {
      resolveStart?.(["late-1"]);
      await runPromise;
    });
    expect(mockedApi.cancelScan).toHaveBeenCalledWith("late-1");

    await act(async () => {
      emitProgress({ type: "cancelled", scan_id: "late-1", scan_type: "local" });
      await Promise.resolve();
    });
    expect(result.current.scanning).toBe(false);
  });

  it("does not re-add a scan that finished before startScan returned", async () => {
    let resolveStart: ((ids: string[]) => void) | undefined;
    mockedApi.startScan.mockReturnValue(
      new Promise<string[]>((resolve) => {
        resolveStart = resolve;
      }),
    );
    const { result } = renderHook(() => useScan());

    let runPromise: Promise<void> | undefined;
    await act(async () => {
      runPromise = result.current.runScan({ workspacePath: "/workspace", authenticated: true });
      await Promise.resolve();
    });
    act(() => {
      emitProgress({ type: "started", scan_id: "fast-1", scan_type: "local", total: 1 });
      emitProgress({
        type: "done",
        scan_id: "fast-1",
        scan_type: "local",
        total_found: 0,
        total_checked: 1,
      });
    });

    await act(async () => {
      resolveStart?.(["fast-1"]);
      await runPromise;
    });
    expect(result.current.scanning).toBe(false);
  });
'''
scan_test = scan_test.replace('\n  it("sets error when neither workspace nor auth is available"', insert + '\n  it("sets error when neither workspace nor auth is available"')
write("apps/desktop/src/hooks/useScan.test.ts", scan_test)

# A focused stale-response regression test for useRepos.
write(
    "apps/desktop/src/hooks/useRepos.test.ts",
    '''import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useRepos } from "./useRepos";

vi.mock("./AuthContext", () => ({
  useAuth: () => ({ handleUnauthorized: vi.fn() }),
}));

vi.mock("../api/commands", () => ({
  api: { listRepos: vi.fn() },
}));

import { api } from "../api/commands";

const mockedApi = vi.mocked(api);

describe("useRepos", () => {
  beforeEach(() => vi.clearAllMocks());

  it("ignores an enabled request that resolves after the hook is disabled", async () => {
    let resolveRepos: ((repos: Awaited<ReturnType<typeof api.listRepos>>) => void) | undefined;
    mockedApi.listRepos.mockReturnValue(
      new Promise((resolve) => {
        resolveRepos = resolve;
      }),
    );

    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => useRepos(enabled),
      { initialProps: { enabled: true } },
    );

    rerender({ enabled: false });
    await act(async () => {
      resolveRepos?.([
        {
          id: 1,
          full_name: "acme/api",
          name: "api",
          owner: "acme",
          private: false,
          html_url: "https://github.com/acme/api",
          is_org: false,
        },
      ]);
      await Promise.resolve();
    });

    expect(result.current.repos).toEqual([]);
    expect(result.current.loading).toBe(false);
  });
});
''',
)

print("final review patch applied")
