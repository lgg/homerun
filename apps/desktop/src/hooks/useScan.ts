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
