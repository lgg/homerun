import { useState, useEffect, useCallback } from "react";
import type { RepoInfo } from "../api/types";
import { api } from "../api/commands";
import { useAuth } from "./AuthContext";

export function useRepos(enabled = true) {
  const [repos, setRepos] = useState<RepoInfo[]>([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const { handleUnauthorized } = useAuth();

  const refresh = useCallback(async () => {
    if (!enabled) {
      setRepos([]);
      setError(null);
      setLoading(false);
      return;
    }
    try {
      setError(null);
      const data = await api.listRepos();
      setRepos(data);
    } catch (e) {
      const msg = String(e);
      if (msg.includes("401") || msg.includes("UNAUTHORIZED")) {
        handleUnauthorized();
      }
      setError(msg);
      setRepos([]);
    } finally {
      setLoading(false);
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
