from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]


def write(relative: str, content: str) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip(), encoding="utf-8")


write(
    "apps/desktop/src/hooks/useScan.ts",
    r'''
    import { useState, useEffect, useCallback, useRef } from "react";
    import { listen, type Event } from "@tauri-apps/api/event";
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
      const progressUnlisten = useRef<(() => void) | null>(null);
      const mounted = useRef(true);

      const releaseProgressListener = useCallback(() => {
        const unlisten = progressUnlisten.current;
        progressUnlisten.current = null;
        unlisten?.();
      }, []);

      useEffect(() => {
        mounted.current = true;
        return () => {
          mounted.current = false;
          launchPending.current = false;
          scanningRef.current = false;
          releaseProgressListener();
        };
      }, [releaseProgressListener]);

      const refreshResults = useCallback(async () => {
        const results = await api.getScanResults();
        if (results && mounted.current) {
          setDiscoveredRepos(results.merged_results);
          setLastScanAt(results.last_scan_at);
        }
      }, []);

      const finishIfIdle = useCallback(async () => {
        if (launchPending.current || activeScanIds.current.size > 0) return;
        try {
          await refreshResults();
        } catch (error) {
          if (mounted.current) setScanError((current) => current ?? String(error));
        } finally {
          scanningRef.current = false;
          cancelRequested.current = false;
          expectedScanIds.current.clear();
          terminalScanIds.current.clear();
          releaseProgressListener();
          if (mounted.current) {
            setScanning(false);
            setProgressText(null);
          }
        }
      }, [refreshResults, releaseProgressListener]);

      useEffect(() => {
        refreshResults().catch(() => {});
      }, [refreshResults]);

      const handleProgress = useCallback(
        (event: Event<string>) => {
          try {
            const data: ScanProgressEvent = JSON.parse(event.payload);
            if (!scanningRef.current) return;

            if (data.type === "started") {
              if (!launchPending.current && !expectedScanIds.current.has(data.scan_id)) return;
              if (terminalScanIds.current.has(data.scan_id)) return;
              activeScanIds.current.add(data.scan_id);
              if (mounted.current) {
                setProgressText(
                  `Starting ${data.scan_type} scan (${data.total} repositories)...`,
                );
              }
              return;
            }
            if (!activeScanIds.current.has(data.scan_id)) return;

            switch (data.type) {
              case "checking":
                if (mounted.current) {
                  setProgressText(
                    `Scanning ${data.repo} (${data.index}/${data.total}) via ${data.scan_type} discovery...`,
                  );
                }
                break;
              case "found":
                if (mounted.current) setProgressText(`Found ${data.full_name}`);
                break;
              case "warning":
                if (mounted.current) {
                  setScanError((current) =>
                    [current, `${data.repo ?? data.scan_type}: ${data.message}`]
                      .filter(Boolean)
                      .join("\n"),
                  );
                }
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
                if (mounted.current) {
                  setScanError((current) =>
                    [current, `${data.scan_type} scan failed: ${data.message}`]
                      .filter(Boolean)
                      .join("\n"),
                  );
                }
                void finishIfIdle();
                break;
            }
          } catch {
            // Ignore malformed events from an incompatible/older daemon.
          }
        },
        [finishIfIdle],
      );

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
            // Install the event listener before asking the daemon to start. A very
            // fast scan can otherwise emit its terminal event before the async
            // Tauri subscription is ready, leaving the page stuck in scanning.
            const unlisten = await listen<string>("scan-progress", handleProgress);
            if (!mounted.current) {
              unlisten();
              return;
            }
            releaseProgressListener();
            progressUnlisten.current = unlisten;

            const scanIds = await api.startScan(workspacePath, authenticated);
            if (!mounted.current) {
              await Promise.allSettled(scanIds.map((scanId) => api.cancelScan(scanId)));
              releaseProgressListener();
              return;
            }

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
            releaseProgressListener();
            if (mounted.current) {
              setScanError(String(error));
              setScanning(false);
              setProgressText(null);
            }
          }
        },
        [finishIfIdle, handleProgress, releaseProgressListener],
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

write(
    "apps/desktop/src/hooks/useJobSteps.ts",
    r'''
    import { useState, useEffect, useCallback, useRef } from "react";
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

        const isTerminal =
          expandedStepStatus !== null &&
          expandedStepStatus !== "pending" &&
          expandedStepStatus !== "running";
        const isCached = logCacheRef.current[expandedStep] !== undefined;
        if (isCached && isTerminal) return;

        let cancelled = false;
        let timer: number | undefined;
        const fetchLogs = async () => {
          try {
            const data = await api.getStepLogs(runnerId, expandedStep);
            if (cancelled || generation !== logsGeneration.current) return;
            const lines = data.lines;
            // A null/pending status is not final. Caching at that point can freeze
            // an early partial response and prevent the completed log from ever
            // being fetched after the step transitions to a terminal state.
            if (isTerminal) logCacheRef.current[expandedStep] = lines;
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
            if (!cancelled && generation === logsGeneration.current && !isTerminal) {
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

write(
    "apps/desktop/src/hooks/useDaemonLogs.ts",
    r'''
    import { useState, useEffect, useCallback, useRef } from "react";
    import { api } from "../api/commands";
    import type { DaemonLogEntry } from "../api/types";

    export function useDaemonLogs(pollInterval = 2000) {
      const [logs, setLogs] = useState<DaemonLogEntry[]>([]);
      const [level, setLevel] = useState<string>("INFO");
      const [search, setSearch] = useState<string>("");
      const [follow, setFollow] = useState(true);
      const [loading, setLoading] = useState(true);
      const [error, setError] = useState<string | null>(null);
      const refreshGeneration = useRef(0);

      const fetchLogs = useCallback(async () => {
        const generation = ++refreshGeneration.current;
        try {
          const entries = await api.getDaemonLogsRecent(level, 2000, search || undefined);
          if (generation !== refreshGeneration.current) return;
          setLogs(entries);
          setError(null);
        } catch (e) {
          if (generation === refreshGeneration.current) setError(String(e));
        } finally {
          if (generation === refreshGeneration.current) setLoading(false);
        }
      }, [level, search]);

      useEffect(() => {
        let cancelled = false;
        let timer: number | undefined;

        const poll = async () => {
          await fetchLogs();
          if (!cancelled) {
            timer = window.setTimeout(() => void poll(), pollInterval);
          }
        };

        void poll();
        return () => {
          cancelled = true;
          refreshGeneration.current += 1;
          if (timer !== undefined) window.clearTimeout(timer);
        };
      }, [fetchLogs, pollInterval]);

      return {
        logs,
        level,
        setLevel,
        search,
        setSearch,
        follow,
        setFollow,
        loading,
        error,
        refresh: fetchLogs,
      };
    }
    ''',
)

write(
    "apps/desktop/src/hooks/useJobHistory.ts",
    r'''
    import { useState, useEffect, useCallback, useRef } from "react";
    import { api } from "../api/commands";
    import type { JobHistoryEntry } from "../api/types";

    export function useJobHistory(runnerId: string | undefined) {
      const [history, setHistory] = useState<JobHistoryEntry[]>([]);
      const [loading, setLoading] = useState(false);
      const refreshGeneration = useRef(0);

      const fetchHistory = useCallback(async () => {
        if (!runnerId) return;
        const generation = ++refreshGeneration.current;
        setLoading(true);
        try {
          const entries = await api.getRunnerHistory(runnerId);
          if (generation === refreshGeneration.current) setHistory(entries);
        } catch {
          // Ignore errors (the runner may not exist yet).
        } finally {
          if (generation === refreshGeneration.current) setLoading(false);
        }
      }, [runnerId]);

      useEffect(() => {
        refreshGeneration.current += 1;
        setHistory([]);
        if (!runnerId) {
          setLoading(false);
          return;
        }

        let cancelled = false;
        let timer: number | undefined;
        const poll = async () => {
          await fetchHistory();
          if (!cancelled) {
            timer = window.setTimeout(() => void poll(), 10_000);
          }
        };

        void poll();
        return () => {
          cancelled = true;
          refreshGeneration.current += 1;
          if (timer !== undefined) window.clearTimeout(timer);
        };
      }, [runnerId, fetchHistory]);

      return { history, loading, refresh: fetchHistory };
    }
    ''',
)

write(
    "apps/desktop/src/hooks/useRepos.ts",
    r'''
    import { useState, useEffect, useCallback, useRef } from "react";
    import type { RepoInfo } from "../api/types";
    import { api } from "../api/commands";
    import { useAuth } from "./AuthContext";

    export function useRepos(enabled = true) {
      const [repos, setRepos] = useState<RepoInfo[]>([]);
      const [loading, setLoading] = useState(enabled);
      const [error, setError] = useState<string | null>(null);
      const { handleUnauthorized } = useAuth();
      const refreshGeneration = useRef(0);

      const refresh = useCallback(async () => {
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
      }, [enabled, handleUnauthorized]);

      useEffect(() => {
        refreshGeneration.current += 1;
        if (!enabled) {
          setRepos([]);
          setError(null);
          setLoading(false);
          return;
        }

        let cancelled = false;
        let timer: number | undefined;
        setLoading(true);

        const poll = async () => {
          await refresh();
          if (!cancelled) {
            timer = window.setTimeout(() => void poll(), 5000);
          }
        };

        void poll();
        return () => {
          cancelled = true;
          refreshGeneration.current += 1;
          if (timer !== undefined) window.clearTimeout(timer);
        };
      }, [enabled, refresh]);

      return { repos, loading, error, refresh };
    }
    ''',
)

write(
    "apps/desktop/src/hooks/useScan.test.ts",
    r'''
    import { describe, it, expect, vi, beforeEach } from "vitest";
    import { renderHook, act } from "@testing-library/react";
    import type { Event } from "@tauri-apps/api/event";
    import { useScan } from "./useScan";

    let progressListener: ((event: Event<string>) => void) | undefined;
    let delayListener = false;
    let resolveListener: (() => void) | undefined;

    vi.mock("@tauri-apps/api/event", () => ({
      listen: vi.fn().mockImplementation((_name: string, listener: (event: Event<string>) => void) => {
        progressListener = listener;
        if (delayListener) {
          return new Promise<() => void>((resolve) => {
            resolveListener = () => resolve(() => {});
          });
        }
        return Promise.resolve(() => {});
      }),
    }));

    vi.mock("../api/commands", () => ({
      api: {
        startScan: vi.fn().mockResolvedValue(["local-1", "remote-1"]),
        cancelScan: vi.fn().mockResolvedValue({ cancelled: true }),
        getScanResults: vi.fn().mockResolvedValue(null),
      },
    }));

    import { api } from "../api/commands";

    const mockedApi = vi.mocked(api);

    function emitProgress(payload: object) {
      progressListener?.({ payload: JSON.stringify(payload) } as Event<string>);
    }

    describe("useScan", () => {
      beforeEach(() => {
        vi.clearAllMocks();
        progressListener = undefined;
        delayListener = false;
        resolveListener = undefined;
        mockedApi.startScan.mockResolvedValue(["local-1", "remote-1"]);
        mockedApi.getScanResults.mockResolvedValue(null);
      });

      it("starts with no results and not scanning", () => {
        const { result } = renderHook(() => useScan());
        expect(result.current.discoveredRepos).toEqual([]);
        expect(result.current.scanning).toBe(false);
        expect(result.current.lastScanAt).toBeNull();
        expect(result.current.progressText).toBeNull();
      });

      it("loads persisted results on mount", async () => {
        mockedApi.getScanResults.mockResolvedValue({
          last_scan_at: "2026-03-28T13:00:00Z",
          local_results: [],
          remote_results: [],
          merged_results: [
            {
              full_name: "acme/api",
              source: "local",
              workflow_files: ["ci.yml"],
              local_path: null,
              matched_labels: ["self-hosted"],
            },
          ],
        });

        const { result } = renderHook(() => useScan());

        await act(async () => {
          await Promise.resolve();
        });

        expect(result.current.discoveredRepos).toHaveLength(1);
        expect(result.current.lastScanAt).toBe("2026-03-28T13:00:00Z");
      });

      it("waits for the progress listener before starting a daemon scan", async () => {
        delayListener = true;
        const { result, unmount } = renderHook(() => useScan());
        let runPromise!: Promise<void>;

        await act(async () => {
          runPromise = result.current.runScan({ workspacePath: "/workspace", authenticated: true });
          await Promise.resolve();
        });

        expect(mockedApi.startScan).not.toHaveBeenCalled();

        await act(async () => {
          resolveListener?.();
          await runPromise;
        });

        expect(mockedApi.startScan).toHaveBeenCalledTimes(1);
        unmount();
      });

      it("tracks exact scan IDs and finishes only after every source is terminal", async () => {
        const { result } = renderHook(() => useScan());

        await act(async () => {
          await result.current.runScan({ workspacePath: "/workspace", authenticated: true });
        });
        expect(result.current.scanning).toBe(true);

        act(() => {
          emitProgress({
            type: "done",
            scan_id: "local-1",
            scan_type: "local",
            total_found: 1,
            total_checked: 1,
          });
        });
        expect(result.current.scanning).toBe(true);

        await act(async () => {
          emitProgress({
            type: "done",
            scan_id: "remote-1",
            scan_type: "remote",
            total_found: 2,
            total_checked: 2,
          });
          await Promise.resolve();
        });
        expect(result.current.scanning).toBe(false);
      });

      it("ignores stale terminal events from an unknown scan", async () => {
        const { result } = renderHook(() => useScan());
        await act(async () => {
          await result.current.runScan({ workspacePath: "/workspace", authenticated: true });
        });

        act(() => {
          emitProgress({
            type: "done",
            scan_id: "old-scan",
            scan_type: "local",
            total_found: 0,
            total_checked: 0,
          });
        });
        expect(result.current.scanning).toBe(true);
      });

      it("exposes cancellation for every active scan ID", async () => {
        const { result } = renderHook(() => useScan());
        await act(async () => {
          await result.current.runScan({ workspacePath: "/workspace", authenticated: true });
          await result.current.cancelScan();
        });

        expect(mockedApi.cancelScan).toHaveBeenCalledWith("local-1");
        expect(mockedApi.cancelScan).toHaveBeenCalledWith("remote-1");
      });

      it("surfaces terminal scan failures and releases scanning state", async () => {
        mockedApi.startScan.mockResolvedValue(["remote-1"]);
        const { result } = renderHook(() => useScan());
        await act(async () => {
          await result.current.runScan({ workspacePath: null, authenticated: true });
        });

        await act(async () => {
          emitProgress({
            type: "failed",
            scan_id: "remote-1",
            scan_type: "remote",
            message: "GitHub unavailable",
          });
          await Promise.resolve();
        });

        expect(result.current.scanning).toBe(false);
        expect(result.current.scanError).toContain("GitHub unavailable");
      });

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

      it("sets error when neither workspace nor auth is available", async () => {
        const { result } = renderHook(() => useScan());

        await act(async () => {
          await result.current.runScan({ workspacePath: null, authenticated: false });
        });

        expect(result.current.scanning).toBe(false);
        expect(result.current.scanError).toBeTruthy();
      });
    });
    ''',
)

write(
    "apps/desktop/src/hooks/useJobSteps.test.ts",
    r'''
    import { act, renderHook, waitFor } from "@testing-library/react";
    import { beforeEach, describe, expect, it, vi } from "vitest";
    import { useJobSteps } from "./useJobSteps";

    vi.mock("../api/commands", () => ({
      api: {
        getRunnerSteps: vi.fn(),
        getStepLogs: vi.fn(),
      },
    }));

    import { api } from "../api/commands";

    const mockedApi = vi.mocked(api);

    describe("useJobSteps", () => {
      beforeEach(() => vi.clearAllMocks());

      it("refetches final logs when a step was expanded before its status was known", async () => {
        let resolveSteps:
          | ((value: Awaited<ReturnType<typeof api.getRunnerSteps>>) => void)
          | undefined;
        mockedApi.getRunnerSteps.mockReturnValue(
          new Promise((resolve) => {
            resolveSteps = resolve;
          }),
        );
        mockedApi.getStepLogs
          .mockResolvedValueOnce({ step_number: 1, step_name: "Build", lines: ["partial"] })
          .mockResolvedValueOnce({ step_number: 1, step_name: "Build", lines: ["final"] });

        const { result, unmount } = renderHook(() => useJobSteps("runner-1", true));

        act(() => result.current.toggleStep(1));
        await waitFor(() => expect(result.current.stepLogs[1]).toEqual(["partial"]));

        await act(async () => {
          resolveSteps?.({
            job_name: "CI",
            steps_discovered: 1,
            steps: [
              {
                number: 1,
                name: "Build",
                status: "succeeded",
                started_at: "2026-07-31T10:00:00Z",
                completed_at: "2026-07-31T10:00:05Z",
              },
            ],
          });
          await Promise.resolve();
        });

        await waitFor(() => expect(result.current.stepLogs[1]).toEqual(["final"]));
        expect(mockedApi.getStepLogs).toHaveBeenCalledTimes(2);
        unmount();
      });
    });
    ''',
)

write(
    "apps/desktop/src/hooks/useDaemonLogs.test.ts",
    r'''
    import { act, renderHook } from "@testing-library/react";
    import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
    import { useDaemonLogs } from "./useDaemonLogs";

    vi.mock("../api/commands", () => ({
      api: { getDaemonLogsRecent: vi.fn() },
    }));

    import { api } from "../api/commands";

    const mockedApi = vi.mocked(api);

    describe("useDaemonLogs", () => {
      beforeEach(() => vi.clearAllMocks());
      afterEach(() => vi.useRealTimers());

      it("waits for a slow request before scheduling the next poll", async () => {
        vi.useFakeTimers();
        let resolveFirst:
          | ((value: Awaited<ReturnType<typeof api.getDaemonLogsRecent>>) => void)
          | undefined;
        mockedApi.getDaemonLogsRecent
          .mockReturnValueOnce(
            new Promise((resolve) => {
              resolveFirst = resolve;
            }),
          )
          .mockResolvedValue([]);

        const { unmount } = renderHook(() => useDaemonLogs(1000));
        await act(async () => Promise.resolve());
        expect(mockedApi.getDaemonLogsRecent).toHaveBeenCalledTimes(1);

        act(() => vi.advanceTimersByTime(5000));
        expect(mockedApi.getDaemonLogsRecent).toHaveBeenCalledTimes(1);

        await act(async () => {
          resolveFirst?.([]);
          await Promise.resolve();
        });
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1000);
        });
        expect(mockedApi.getDaemonLogsRecent).toHaveBeenCalledTimes(2);
        unmount();
      });
    });
    ''',
)

write(
    "apps/desktop/src/hooks/useJobHistory.test.ts",
    r'''
    import { act, renderHook } from "@testing-library/react";
    import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
    import { useJobHistory } from "./useJobHistory";

    vi.mock("../api/commands", () => ({
      api: { getRunnerHistory: vi.fn() },
    }));

    import { api } from "../api/commands";

    const mockedApi = vi.mocked(api);

    describe("useJobHistory", () => {
      beforeEach(() => vi.clearAllMocks());
      afterEach(() => vi.useRealTimers());

      it("does not overlap a slow history request", async () => {
        vi.useFakeTimers();
        let resolveFirst:
          | ((value: Awaited<ReturnType<typeof api.getRunnerHistory>>) => void)
          | undefined;
        mockedApi.getRunnerHistory
          .mockReturnValueOnce(
            new Promise((resolve) => {
              resolveFirst = resolve;
            }),
          )
          .mockResolvedValue([]);

        const { unmount } = renderHook(() => useJobHistory("runner-1"));
        await act(async () => Promise.resolve());
        expect(mockedApi.getRunnerHistory).toHaveBeenCalledTimes(1);

        act(() => vi.advanceTimersByTime(30_000));
        expect(mockedApi.getRunnerHistory).toHaveBeenCalledTimes(1);

        await act(async () => {
          resolveFirst?.([]);
          await Promise.resolve();
        });
        await act(async () => {
          await vi.advanceTimersByTimeAsync(10_000);
        });
        expect(mockedApi.getRunnerHistory).toHaveBeenCalledTimes(2);
        unmount();
      });
    });
    ''',
)

write(
    "apps/desktop/src/hooks/useRepos.test.ts",
    r'''
    import { act, renderHook } from "@testing-library/react";
    import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
    import { useRepos } from "./useRepos";

    const authMocks = vi.hoisted(() => ({ handleUnauthorized: vi.fn() }));

    vi.mock("./AuthContext", () => ({
      useAuth: () => ({ handleUnauthorized: authMocks.handleUnauthorized }),
    }));

    vi.mock("../api/commands", () => ({
      api: { listRepos: vi.fn() },
    }));

    import { api } from "../api/commands";

    const mockedApi = vi.mocked(api);

    describe("useRepos", () => {
      beforeEach(() => vi.clearAllMocks());
      afterEach(() => vi.useRealTimers());

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

      it("waits for a slow request before scheduling the next poll", async () => {
        vi.useFakeTimers();
        let resolveFirst: ((repos: Awaited<ReturnType<typeof api.listRepos>>) => void) | undefined;
        mockedApi.listRepos
          .mockReturnValueOnce(
            new Promise((resolve) => {
              resolveFirst = resolve;
            }),
          )
          .mockResolvedValue([]);

        const { unmount } = renderHook(() => useRepos(true));
        await act(async () => Promise.resolve());
        expect(mockedApi.listRepos).toHaveBeenCalledTimes(1);

        act(() => vi.advanceTimersByTime(15_000));
        expect(mockedApi.listRepos).toHaveBeenCalledTimes(1);

        await act(async () => {
          resolveFirst?.([]);
          await Promise.resolve();
        });
        await act(async () => {
          await vi.advanceTimersByTimeAsync(5000);
        });
        expect(mockedApi.listRepos).toHaveBeenCalledTimes(2);
        unmount();
      });
    });
    ''',
)

write(
    "apps/desktop/src/pages/Settings.test.tsx",
    r'''
    import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
    import { beforeEach, describe, expect, it, vi } from "vitest";
    import { Settings } from "./Settings";
    import type { Preferences } from "../api/types";

    const mocks = vi.hoisted(() => ({
      getPreferences: vi.fn(),
      updatePreferences: vi.fn(),
      invoke: vi.fn(),
    }));

    vi.mock("../hooks/useAuth", () => ({
      useAuth: () => ({
        auth: {
          authenticated: true,
          user: { login: "octocat", avatar_url: "https://example.com/avatar.png" },
        },
        loading: false,
        error: null,
        loginWithToken: vi.fn(),
        logout: vi.fn(),
        refresh: vi.fn(),
      }),
    }));

    vi.mock("../api/commands", () => ({
      api: {
        getPreferences: mocks.getPreferences,
        updatePreferences: mocks.updatePreferences,
        startDeviceFlow: vi.fn(),
        pollDeviceFlow: vi.fn(),
      },
    }));

    vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
    vi.mock("@tauri-apps/api/app", () => ({ getVersion: vi.fn().mockResolvedValue("0.9.1") }));

    const preferences: Preferences = {
      start_runners_on_launch: false,
      notify_status_changes: true,
      notify_job_completions: true,
      scan_labels: ["self-hosted"],
      workspace_path: null,
      auto_scan: false,
    };

    describe("Settings", () => {
      beforeEach(() => {
        vi.clearAllMocks();
        mocks.invoke.mockResolvedValue(false);
        mocks.updatePreferences.mockImplementation(async (value: Preferences) => value);
      });

      it("keeps preference controls disabled until saved preferences are loaded", async () => {
        let resolvePreferences: ((value: Preferences) => void) | undefined;
        mocks.getPreferences.mockReturnValue(
          new Promise((resolve) => {
            resolvePreferences = resolve;
          }),
        );

        render(<Settings />);
        const restoreToggle = screen.getByRole("switch", { name: "Restore runners on launch" });
        expect(restoreToggle).toBeDisabled();
        fireEvent.click(restoreToggle);
        expect(mocks.updatePreferences).not.toHaveBeenCalled();

        await act(async () => {
          resolvePreferences?.(preferences);
          await Promise.resolve();
        });

        await waitFor(() => expect(restoreToggle).not.toBeDisabled());
      });
    });
    ''',
)

settings_path = ROOT / "apps/desktop/src/pages/Settings.tsx"
settings = settings_path.read_text(encoding="utf-8")
settings = settings.replace(
    "  const [preferencesSaving, setPreferencesSaving] = useState(false);\n",
    "  const [preferencesLoading, setPreferencesLoading] = useState(true);\n"
    "  const [preferencesSaving, setPreferencesSaving] = useState(false);\n",
    1,
)
settings = settings.replace(
    "      .catch((error) => setPreferencesError(String(error)));\n",
    "      .catch((error) => setPreferencesError(String(error)))\n"
    "      .finally(() => setPreferencesLoading(false));\n",
    1,
)
settings = settings.replace(
    "    if (preferencesSaving) return;\n",
    "    if (preferencesLoading || preferencesSaving) return;\n",
    1,
)
settings = settings.replace(
    "disabled={preferencesSaving}",
    "disabled={preferencesLoading || preferencesSaving}",
)
settings_path.write_text(settings, encoding="utf-8")

# The helper is intentionally temporary and must not be part of the PR.
Path(__file__).unlink()
