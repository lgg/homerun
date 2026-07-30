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
    if (!enabled) {
      setRepos([]);
      setError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    void refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [enabled, refresh]);

  return { repos, loading, error, refresh };
}
