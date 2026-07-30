from pathlib import Path

# Keep the useRepos auth callback stable across test renders.
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

# These symbols intentionally exist only in their respective build modes.
path = Path("crates/daemon/src/persistence.rs")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "use anyhow::{anyhow, Context, Result};",
    "#[cfg(windows)]\nuse anyhow::anyhow;\nuse anyhow::{Context, Result};",
    1,
)
path.write_text(text, encoding="utf-8")

path = Path("crates/daemon/src/auth/keychain.rs")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "/// Returns the path to the auth token file: `~/.homerun/auth.json`\nfn auth_file_path()",
    "/// Returns the path to the auth token file: `~/.homerun/auth.json`\n#[cfg(not(test))]\nfn auth_file_path()",
    1,
)
path.write_text(text, encoding="utf-8")
