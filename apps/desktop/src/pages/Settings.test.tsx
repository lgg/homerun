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
