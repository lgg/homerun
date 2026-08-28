import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useNotifications } from "./useNotifications";
import { makeRunner } from "../test/factories";
import type { Preferences, RunnerInfo } from "../api/types";

const mockInvoke = vi.fn();
const mockResolveResource = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => mockInvoke(...args),
}));

vi.mock("@tauri-apps/api/path", () => ({
  resolveResource: (...args: unknown[]) => mockResolveResource(...args),
}));

function makePrefs(overrides?: Partial<Preferences>): Preferences {
  return {
    start_runners_on_launch: false,
    notify_status_changes: true,
    notify_job_completions: true,
    scan_labels: [],
    workspace_path: null,
    auto_scan: false,
    hide_offline_runners_in_mini_view: false,
    sort_runners_by_activity: false,
    ...overrides,
  };
}

function runnerWithJob(
  name: string,
  state: string,
  succeeded: boolean,
  jobName: string,
  completedAt: string,
  durationSecs = 90,
): RunnerInfo {
  const base = makeRunner({ name, state: state as RunnerInfo["state"] });
  return {
    ...base,
    jobs_completed: succeeded ? base.jobs_completed + 1 : base.jobs_completed,
    jobs_failed: succeeded ? base.jobs_failed : base.jobs_failed + 1,
    last_completed_job: {
      job_name: jobName,
      succeeded,
      completed_at: completedAt,
      duration_secs: durationSecs,
    },
  };
}

beforeEach(() => {
  mockInvoke.mockClear();
  mockResolveResource.mockClear();
  mockInvoke.mockResolvedValue(undefined);
  mockResolveResource.mockImplementation((path: string) => Promise.resolve(`/resolved/${path}`));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useNotifications", () => {
  it("does nothing when preferences is null", () => {
    const runners = [makeRunner({ name: "r1" })];
    renderHook(() => useNotifications(runners, null));
    expect(mockInvoke).not.toHaveBeenCalled();
  });

  it("does nothing when runners is empty", () => {
    renderHook(() => useNotifications([], makePrefs()));
    expect(mockInvoke).not.toHaveBeenCalled();
  });

  it("does not send notifications on first render (initialization)", () => {
    const runners = [makeRunner({ name: "r1", state: "online" })];
    renderHook(() => useNotifications(runners, makePrefs()));
    expect(mockInvoke).not.toHaveBeenCalled();
  });

  it("sends Runner Online notification when state changes to online", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "offline" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });

    const updated = [makeRunner({ name: "r1", state: "online" })];
    rerender({ runners: updated, prefs });

    await vi.waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith("send_notification", {
        title: "Runner Online",
        body: "r1 is now online and ready for jobs",
        icon_path: "/resolved/resources/notifications/active.png",
      });
    });
  });

  it("sends Runner Offline notification when state changes to offline", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "online" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });

    const updated = [makeRunner({ name: "r1", state: "offline" })];
    rerender({ runners: updated, prefs });

    await vi.waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith("send_notification", {
        title: "Runner Offline",
        body: "r1 went offline",
        icon_path: "/resolved/resources/notifications/offline.png",
      });
    });
  });

  it("sends Runner Error notification when state changes to error", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "online" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });

    const updated = [makeRunner({ name: "r1", state: "error" })];
    rerender({ runners: updated, prefs });

    await vi.waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith("send_notification", {
        title: "Runner Error",
        body: "r1 encountered an error",
        icon_path: "/resolved/resources/notifications/error.png",
      });
    });
  });

  it("respects notify_status_changes=false", async () => {
    const prefs = makePrefs({ notify_status_changes: false });
    const initial = [makeRunner({ name: "r1", state: "offline" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    rerender({ runners: [makeRunner({ name: "r1", state: "online" })], prefs });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(mockInvoke).not.toHaveBeenCalled();
  });

  it("does not notify for transitional states", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "offline" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    rerender({ runners: [makeRunner({ name: "r1", state: "creating" })], prefs });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(mockInvoke).not.toHaveBeenCalled();
  });

  it("does not notify for removed runners", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "online" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    rerender({ runners: [], prefs });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(mockInvoke).not.toHaveBeenCalled();
  });

  it("notifies when a job completes successfully", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "busy" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    const completed = [
      runnerWithJob("r1", "online", true, "Build", "2026-08-28T12:00:00Z", 65),
    ];
    rerender({ runners: completed, prefs });
    await vi.waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith("send_notification", {
        title: "Job Completed",
        body: "Build completed on r1 in 1m 5s",
        icon_path: "/resolved/resources/notifications/success.png",
      });
    });
  });

  it("notifies when a job fails", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "busy" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    const completed = [runnerWithJob("r1", "online", false, "Test", "2026-08-28T12:00:00Z", 7)];
    rerender({ runners: completed, prefs });
    await vi.waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith("send_notification", {
        title: "Job Failed",
        body: "Test failed on r1 after 7s",
        icon_path: "/resolved/resources/notifications/failure.png",
      });
    });
  });

  it("respects notify_job_completions=false", async () => {
    const prefs = makePrefs({ notify_job_completions: false });
    const initial = [makeRunner({ name: "r1", state: "busy" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    rerender({ runners: [runnerWithJob("r1", "online", true, "Build", "2026-08-28T12:00:00Z")], prefs });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(mockInvoke).not.toHaveBeenCalled();
  });

  it("does not re-notify for the same completed job", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "busy" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    const completed = [runnerWithJob("r1", "online", true, "Build", "2026-08-28T12:00:00Z")];
    rerender({ runners: completed, prefs });
    await vi.waitFor(() => expect(mockInvoke).toHaveBeenCalledTimes(2));
    mockInvoke.mockClear();
    rerender({ runners: completed, prefs });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(mockInvoke).not.toHaveBeenCalled();
  });

  it("detects distinct completed jobs by timestamp", async () => {
    const prefs = makePrefs();
    const first = [runnerWithJob("r1", "online", true, "Build", "2026-08-28T12:00:00Z")];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: first, prefs },
    });
    mockInvoke.mockClear();
    const second = [runnerWithJob("r1", "online", true, "Build", "2026-08-28T12:05:00Z")];
    rerender({ runners: second, prefs });
    await vi.waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith("send_notification", expect.objectContaining({
        title: "Job Completed",
      }));
    });
  });

  it("formats minute-only durations cleanly", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "busy" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    rerender({
      runners: [runnerWithJob("r1", "online", true, "Build", "2026-08-28T12:00:00Z", 120)],
      prefs,
    });
    await vi.waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith("send_notification", expect.objectContaining({
        body: "Build completed on r1 in 2m",
      }));
    });
  });

  it("formats hour durations cleanly", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "busy" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    rerender({
      runners: [runnerWithJob("r1", "online", true, "Build", "2026-08-28T12:00:00Z", 3660)],
      prefs,
    });
    await vi.waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith("send_notification", expect.objectContaining({
        body: "Build completed on r1 in 1h 1m",
      }));
    });
  });

  it("uses display name in notification body when configured", async () => {
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", displayName: "Worker A", state: "offline" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    rerender({ runners: [makeRunner({ name: "r1", displayName: "Worker A", state: "online" })], prefs });
    await vi.waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith("send_notification", expect.objectContaining({
        body: "Worker A is now online and ready for jobs",
      }));
    });
  });

  it("reports notification errors without throwing", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    mockInvoke.mockRejectedValueOnce(new Error("notification failed"));
    const prefs = makePrefs();
    const initial = [makeRunner({ name: "r1", state: "offline" })];
    const { rerender } = renderHook(({ runners, prefs }) => useNotifications(runners, prefs), {
      initialProps: { runners: initial, prefs },
    });
    rerender({ runners: [makeRunner({ name: "r1", state: "online" })], prefs });
    await vi.waitFor(() => expect(consoleError).toHaveBeenCalled());
    consoleError.mockRestore();
  });
});
