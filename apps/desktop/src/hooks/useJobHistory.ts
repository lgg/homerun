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
      // ignore errors (runner may not exist yet)
    } finally {
      if (generation === refreshGeneration.current) setLoading(false);
    }
  }, [runnerId]);

  useEffect(() => {
    if (!runnerId) return;
    fetchHistory();
    const timer = setInterval(fetchHistory, 10000);
    return () => clearInterval(timer);
  }, [runnerId, fetchHistory]);

  return { history, loading, refresh: fetchHistory };
}
