import { describe, expect, it } from "vitest";
import { isLaunchableRepository } from "./repository";

describe("isLaunchableRepository", () => {
  it("requires an exact non-empty owner/repository pair", () => {
    expect(isLaunchableRepository("owner/repo")).toBe(true);
    expect(isLaunchableRepository("repo")).toBe(false);
    expect(isLaunchableRepository("owner/")).toBe(false);
    expect(isLaunchableRepository("/repo")).toBe(false);
    expect(isLaunchableRepository(" owner/repo")).toBe(false);
    expect(isLaunchableRepository("owner/repo ")).toBe(false);
    expect(isLaunchableRepository("owner\t/repo")).toBe(false);
    expect(isLaunchableRepository("owner//repo")).toBe(false);
    expect(isLaunchableRepository("owner/repo/extra")).toBe(false);
  });
});
