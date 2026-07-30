import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
