from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]

# Revert an unrelated rustfmt-only change introduced by the validation toolchain.
subprocess.run(
    ["git", "checkout", "origin/master", "--", "apps/desktop/src-tauri/src/window.rs"],
    cwd=root,
    check=True,
)

# Validation logs must never be committed to the feature branch.
for relative in ("cargo-test.log", "tauri-check.log"):
    path = root / relative
    if path.exists():
        path.unlink()

# The TUI mirrors daemon response types. Preserve the optional alias so its tests and fixtures
# remain source-compatible while older daemon responses continue to deserialize.
client_path = root / "crates/tui/src/client.rs"
content = client_path.read_text(encoding="utf-8")
old = """pub struct RunnerConfig {\n    pub id: String,\n    pub name: String,\n    pub repo_owner: String,\n"""
new = """pub struct RunnerConfig {\n    pub id: String,\n    pub name: String,\n    #[serde(default)]\n    pub display_name: Option<String>,\n    pub repo_owner: String,\n"""
if content.count(old) != 1:
    raise RuntimeError("Unable to locate TUI RunnerConfig declaration")
client_path.write_text(content.replace(old, new, 1), encoding="utf-8")

# Serde normally maps both an omitted optional field and explicit JSON null to None. The PATCH
# contract needs to distinguish those states so null can clear an existing alias.
types_path = root / "crates/daemon/src/runner/types.rs"
content = types_path.read_text(encoding="utf-8")
old = """    #[serde(default)]\n    pub display_name: Option<Option<String>>,\n"""
new = """    #[serde(default, deserialize_with = \"deserialize_display_name_update\")]\n    pub display_name: Option<Option<String>>,\n"""
if content.count(old) != 1:
    raise RuntimeError("Unable to locate display_name update field")
content = content.replace(old, new, 1)

marker = """fn normalize_display_name(value: Option<String>) -> anyhow::Result<Option<String>> {\n"""
deserializer = """fn deserialize_display_name_update<'de, D>(\n    deserializer: D,\n) -> Result<Option<Option<String>>, D::Error>\nwhere\n    D: serde::Deserializer<'de>,\n{\n    Ok(Some(Option::<String>::deserialize(deserializer)?))\n}\n\n"""
if content.count(marker) != 1:
    raise RuntimeError("Unable to locate display-name normalization helper")
content = content.replace(marker, deserializer + marker, 1)
types_path.write_text(content, encoding="utf-8")

print("Display-name PR cleanup applied")
