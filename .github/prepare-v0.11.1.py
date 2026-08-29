from pathlib import Path
import json
import re

OLD = "0.11.0"
NEW = "0.11.1"


def write_json(path: str, data: object) -> None:
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def update_json_version(path: str) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != OLD:
        raise RuntimeError(f"{path}: expected version {OLD}, got {data.get('version')!r}")
    data["version"] = NEW
    write_json(path, data)


def update_toml_version(path: str, section: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    pattern = rf'(?ms)(^\[{re.escape(section)}\]\s*.*?^version\s*=\s*")([^"\n]+)(")'
    match = re.search(pattern, text)
    if not match:
        raise RuntimeError(f"{path}: could not find version in [{section}]")
    if match.group(2) != OLD:
        raise RuntimeError(f"{path}: expected version {OLD}, got {match.group(2)!r}")
    text = text[: match.start(2)] + NEW + text[match.end(2) :]
    Path(path).write_text(text, encoding="utf-8")


manifest_path = ".release-please-manifest.json"
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
if manifest.get(".") != OLD:
    raise RuntimeError(f"{manifest_path}: expected root version {OLD}, got {manifest.get('.')!r}")
manifest["."] = NEW
write_json(manifest_path, manifest)

update_toml_version("Cargo.toml", "workspace.package")
update_json_version("apps/desktop/package.json")
update_json_version("apps/desktop/src-tauri/tauri.conf.json")
update_toml_version("apps/desktop/src-tauri/Cargo.toml", "package")

changelog_path = Path("CHANGELOG.md")
changelog = changelog_path.read_text(encoding="utf-8")
heading = f"## [{NEW}](https://github.com/lgg/homerun/compare/v{OLD}...v{NEW}) (2026-08-29)"
if heading in changelog:
    raise RuntimeError(f"CHANGELOG.md already contains {NEW}")
marker = "---\n\n"
if marker not in changelog:
    raise RuntimeError("CHANGELOG.md insertion marker not found")
entry = (
    f"{heading}\n\n"
    "### Bug Fixes\n\n"
    "* harden daemon runtime platform handling ([#43](https://github.com/lgg/homerun/issues/43))\n\n"
)
changelog = changelog.replace(marker, marker + entry, 1)
changelog_path.write_text(changelog, encoding="utf-8")

print(f"Prepared release metadata for {NEW}")