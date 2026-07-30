from pathlib import Path

path = Path("apps/desktop/src/hooks/useRepos.test.ts")
text = path.read_text(encoding="utf-8")
old = '''vi.mock("./AuthContext", () => ({
  useAuth: () => ({ handleUnauthorized: vi.fn() }),
}));'''
new = '''const authMocks = vi.hoisted(() => ({ handleUnauthorized: vi.fn() }));

vi.mock("./AuthContext", () => ({
  useAuth: () => ({ handleUnauthorized: authMocks.handleUnauthorized }),
}));'''
if old not in text:
    if new not in text:
        raise SystemExit("useRepos auth mock pattern not found")
else:
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
