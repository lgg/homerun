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
