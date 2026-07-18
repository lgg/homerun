from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new, 1))


def add_display_name_to_runner_config_literals(path: Path) -> bool:
    content = path.read_text(encoding="utf-8")
    original = content
    cursor = 0

    while True:
        start = content.find("RunnerConfig {", cursor)
        if start == -1:
            break
        brace = content.find("{", start)
        depth = 0
        end = None
        for index in range(brace, len(content)):
            char = content[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is None:
            raise RuntimeError(f"Unbalanced RunnerConfig block in {path}")

        block = content[start : end + 1]
        if "display_name:" not in block:
            match = re.search(
                r"(?m)^(?P<indent>\s*)name(?:\s*:\s*[^\n]+)?\s*,\s*$",
                block,
            )
            if match:
                insertion = f"{match.group(0)}\n{match.group('indent')}display_name: None,"
                block = block[: match.start()] + insertion + block[match.end() :]
                content = content[:start] + block + content[end + 1 :]
                end = start + len(block) - 1
        cursor = end + 1

    if content != original:
        path.write_text(content, encoding="utf-8")
        return True
    return False


# RunnerConfig is constructed in the daemon and tests. Backfill the new optional field in every
# Rust struct literal while leaving the struct declarations untouched.
for rust_file in ROOT.rglob("*.rs"):
    if "target" not in rust_file.parts:
        add_display_name_to_runner_config_literals(rust_file)

replace_once(
    "crates/daemon/src/api/runners.rs",
    """pub async fn update_runner(\n    State(state): State<AppState>,\n    Path(id): Path<String>,\n    Json(req): Json<UpdateRunnerRequest>,\n) -> Result<Json<RunnerInfo>, (StatusCode, String)> {\n    state\n        .runner_manager\n        .update(&id, req)\n        .await\n        .map(Json)\n        .map_err(|e| (StatusCode::NOT_FOUND, e.to_string()))\n}\n""",
    """pub async fn update_runner(\n    State(state): State<AppState>,\n    Path(id): Path<String>,\n    Json(req): Json<UpdateRunnerRequest>,\n) -> Result<Json<RunnerInfo>, (StatusCode, String)> {\n    let display_name = req.display_name.clone();\n    let mut updated = state\n        .runner_manager\n        .update(&id, req)\n        .await\n        .map_err(|e| (StatusCode::NOT_FOUND, e.to_string()))?;\n\n    if let Some(value) = display_name {\n        updated = state\n            .runner_manager\n            .update_display_name(&id, value)\n            .await\n            .map_err(|e| {\n                let message = e.to_string();\n                let status = if message == \"Runner not found\" {\n                    StatusCode::NOT_FOUND\n                } else {\n                    StatusCode::BAD_REQUEST\n                };\n                (status, message)\n            })?;\n    }\n\n    // Persist every PATCH operation. This also fixes labels/mode updates being lost on restart.\n    state\n        .runner_manager\n        .save_to_disk()\n        .await\n        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;\n\n    Ok(Json(updated))\n}\n""",
)

replace_once(
    "apps/desktop/src-tauri/src/client.rs",
    """pub struct RunnerConfig {\n    pub id: String,\n    pub name: String,\n    pub repo_owner: String,\n""",
    """pub struct RunnerConfig {\n    pub id: String,\n    pub name: String,\n    #[serde(default)]\n    pub display_name: Option<String>,\n    pub repo_owner: String,\n""",
)

replace_once(
    "apps/desktop/src-tauri/src/client.rs",
    """    pub async fn create_runner(&self, req: &CreateRunnerRequest) -> Result<RunnerInfo, String> {\n        let body = self\n            .request(\n                \"POST\",\n                \"/runners\",\n                Some(serde_json::to_string(req).map_err(|e| e.to_string())?),\n            )\n            .await?;\n        serde_json::from_str(&body).map_err(|e| e.to_string())\n    }\n\n    pub async fn delete_runner(&self, id: &str) -> Result<(), String> {\n""",
    """    pub async fn create_runner(&self, req: &CreateRunnerRequest) -> Result<RunnerInfo, String> {\n        let body = self\n            .request(\n                \"POST\",\n                \"/runners\",\n                Some(serde_json::to_string(req).map_err(|e| e.to_string())?),\n            )\n            .await?;\n        serde_json::from_str(&body).map_err(|e| e.to_string())\n    }\n\n    pub async fn update_runner_display_name(\n        &self,\n        id: &str,\n        display_name: Option<&str>,\n    ) -> Result<RunnerInfo, String> {\n        let payload = serde_json::json!({ \"display_name\": display_name }).to_string();\n        let body = self\n            .request(\"PATCH\", &format!(\"/runners/{id}\"), Some(payload))\n            .await?;\n        serde_json::from_str(&body).map_err(|e| e.to_string())\n    }\n\n    pub async fn delete_runner(&self, id: &str) -> Result<(), String> {\n""",
)

replace_once(
    "apps/desktop/src-tauri/src/commands.rs",
    """#[tauri::command]\npub async fn create_runner(\n    state: State<'_, AppState>,\n    req: CreateRunnerRequest,\n) -> Result<RunnerInfo, String> {\n    let client = state.client.lock().await;\n    client.create_runner(&req).await\n}\n\n#[tauri::command]\npub async fn delete_runner(state: State<'_, AppState>, id: String) -> Result<(), String> {\n""",
    """#[tauri::command]\npub async fn create_runner(\n    state: State<'_, AppState>,\n    req: CreateRunnerRequest,\n) -> Result<RunnerInfo, String> {\n    let client = state.client.lock().await;\n    client.create_runner(&req).await\n}\n\n#[tauri::command(rename_all = \"snake_case\")]\npub async fn update_runner_display_name(\n    state: State<'_, AppState>,\n    id: String,\n    display_name: Option<String>,\n) -> Result<RunnerInfo, String> {\n    let client = state.client.lock().await;\n    client\n        .update_runner_display_name(&id, display_name.as_deref())\n        .await\n}\n\n#[tauri::command]\npub async fn delete_runner(state: State<'_, AppState>, id: String) -> Result<(), String> {\n""",
)

replace_once(
    "apps/desktop/src-tauri/src/lib.rs",
    """            commands::list_runners,\n            commands::create_runner,\n            commands::delete_runner,\n""",
    """            commands::list_runners,\n            commands::create_runner,\n            commands::update_runner_display_name,\n            commands::delete_runner,\n""",
)

replace_once(
    "apps/desktop/src/api/types.ts",
    """export interface RunnerConfig {\n  id: string;\n  name: string;\n  repo_owner: string;\n""",
    """export interface RunnerConfig {\n  id: string;\n  /** Technical name registered with GitHub Actions. */\n  name: string;\n  /** Optional HomeRun-only alias. */\n  display_name?: string | null;\n  repo_owner: string;\n""",
)

replace_once(
    "apps/desktop/src/api/commands.ts",
    """  listRunners: () => invoke<RunnerInfo[]>(\"list_runners\"),\n  createRunner: (req: CreateRunnerRequest) => invoke<RunnerInfo>(\"create_runner\", { req }),\n  deleteRunner: (id: string) => invoke<void>(\"delete_runner\", { id }),\n""",
    """  listRunners: () => invoke<RunnerInfo[]>(\"list_runners\"),\n  createRunner: (req: CreateRunnerRequest) => invoke<RunnerInfo>(\"create_runner\", { req }),\n  updateRunnerDisplayName: (id: string, displayName: string | null) =>\n    invoke<RunnerInfo>(\"update_runner_display_name\", { id, display_name: displayName }),\n  deleteRunner: (id: string) => invoke<void>(\"delete_runner\", { id }),\n""",
)

rename_dialog = """import { FormEvent, useEffect, useRef, useState } from \"react\";\nimport type { RunnerInfo } from \"../api/types\";\nimport { api } from \"../api/commands\";\n\ninterface RenameRunnerDialogProps {\n  runner: RunnerInfo;\n  onClose: () => void;\n}\n\nexport function RenameRunnerDialog({ runner, onClose }: RenameRunnerDialogProps) {\n  const [value, setValue] = useState(runner.config.display_name ?? \"\");\n  const [saving, setSaving] = useState(false);\n  const [error, setError] = useState<string | null>(null);\n  const inputRef = useRef<HTMLInputElement>(null);\n\n  const currentValue = runner.config.display_name ?? null;\n  const normalizedValue = value.trim() || null;\n  const unchanged = normalizedValue === currentValue;\n\n  useEffect(() => {\n    inputRef.current?.focus();\n    inputRef.current?.select();\n  }, []);\n\n  useEffect(() => {\n    function handleKeyDown(event: KeyboardEvent) {\n      if (event.key === \"Escape\" && !saving) onClose();\n    }\n    document.addEventListener(\"keydown\", handleKeyDown);\n    return () => document.removeEventListener(\"keydown\", handleKeyDown);\n  }, [onClose, saving]);\n\n  async function handleSubmit(event: FormEvent) {\n    event.preventDefault();\n    if (saving || unchanged) return;\n\n    setSaving(true);\n    setError(null);\n    try {\n      await api.updateRunnerDisplayName(runner.config.id, normalizedValue);\n      onClose();\n    } catch (e) {\n      setError(String(e));\n      setSaving(false);\n    }\n  }\n\n  return (\n    <div className=\"dialog-overlay\" onClick={saving ? undefined : onClose}>\n      <form className=\"dialog\" onSubmit={handleSubmit} onClick={(e) => e.stopPropagation()}>\n        <h3 className=\"dialog-title\">Rename Runner</h3>\n        <p className=\"dialog-message\">\n          This changes only the name shown in HomeRun. The GitHub runner name{\" \"}\n          <strong>{runner.config.name}</strong> will not change. Clear the field to restore the\n          GitHub name.\n        </p>\n        <input\n          ref={inputRef}\n          className=\"input\"\n          value={value}\n          maxLength={100}\n          placeholder={runner.config.name}\n          aria-label=\"Runner display name\"\n          disabled={saving}\n          onChange={(event) => setValue(event.target.value)}\n          style={{ width: \"100%\", marginBottom: 12 }}\n        />\n        {error && <div className=\"error-banner\">{error}</div>}\n        <div className=\"dialog-actions\">\n          <button type=\"button\" className=\"btn\" onClick={onClose} disabled={saving}>\n            Cancel\n          </button>\n          <button type=\"submit\" className=\"btn btn-primary\" disabled={saving || unchanged}>\n            {saving ? \"Saving…\" : normalizedValue ? \"Save Display Name\" : \"Use GitHub Name\"}\n          </button>\n        </div>\n      </form>\n    </div>\n  );\n}\n"""
write("apps/desktop/src/components/RenameRunnerDialog.tsx", rename_dialog)

replace_once(
    "apps/desktop/src/components/RunnerActions.tsx",
    """import type { RunnerInfo } from \"../api/types\";\nimport { ConfirmDialog } from \"./ConfirmDialog\";\n""",
    """import type { RunnerInfo } from \"../api/types\";\nimport { ConfirmDialog } from \"./ConfirmDialog\";\nimport { RenameRunnerDialog } from \"./RenameRunnerDialog\";\n""",
)
replace_once(
    "apps/desktop/src/components/RunnerActions.tsx",
    """}: RunnerActionsProps) {\n  if (readOnly) return null;\n  const [confirm, setConfirm] = useState<\"delete\" | null>(null);\n  const [menuOpen, setMenuOpen] = useState(false);\n""",
    """}: RunnerActionsProps) {\n  const [confirm, setConfirm] = useState<\"delete\" | null>(null);\n  const [renameOpen, setRenameOpen] = useState(false);\n  const [menuOpen, setMenuOpen] = useState(false);\n""",
)
replace_once(
    "apps/desktop/src/components/RunnerActions.tsx",
    """  }, [menuOpen]);\n\n  return (\n""",
    """  }, [menuOpen]);\n\n  if (readOnly) return null;\n\n  return (\n""",
)
replace_once(
    "apps/desktop/src/components/RunnerActions.tsx",
    """        <button\n          className=\"icon-btn icon-btn-danger\"\n          onClick={() => setConfirm(\"delete\")}\n""",
    """        <button\n          className=\"icon-btn\"\n          onClick={() => setRenameOpen(true)}\n          title=\"Rename display name\"\n          disabled={loading}\n        >\n          ✎\n        </button>\n        <button\n          className=\"icon-btn icon-btn-danger\"\n          onClick={() => setConfirm(\"delete\")}\n""",
)
replace_once(
    "apps/desktop/src/components/RunnerActions.tsx",
    """            <button\n              className=\"actions-dropdown-item actions-dropdown-item-danger\"\n              disabled={!canDelete}\n""",
    """            <button\n              className=\"actions-dropdown-item\"\n              onClick={() => {\n                setRenameOpen(true);\n                setMenuOpen(false);\n              }}\n            >\n              ✎ Rename display name\n            </button>\n            <button\n              className=\"actions-dropdown-item actions-dropdown-item-danger\"\n              disabled={!canDelete}\n""",
)
replace_once(
    "apps/desktop/src/components/RunnerActions.tsx",
    """      {confirm === \"delete\" && (\n""",
    """      {renameOpen && (\n        <RenameRunnerDialog runner={runner} onClose={() => setRenameOpen(false)} />\n      )}\n\n      {confirm === \"delete\" && (\n""",
)
replace_once(
    "apps/desktop/src/components/RunnerActions.tsx",
    """          message={`Are you sure you want to delete \"${runner.config.name}\"? This will stop the runner, deregister it from GitHub, and remove its local data.`}\n""",
    """          message={`Are you sure you want to delete \"${runner.config.display_name ?? runner.config.name}\"? This will stop the runner, deregister it from GitHub, and remove its local data.`}\n""",
)

replace_once(
    "apps/desktop/src/components/RunnerTable.tsx",
    """  onClick: () => void;\n}) {\n  return (\n""",
    """  onClick: () => void;\n}) {\n  const displayName = runner.config.display_name ?? runner.config.name;\n\n  return (\n""",
)
replace_once(
    "apps/desktop/src/components/RunnerTable.tsx",
    """            <span\n              className=\"font-mono\"\n              style={{\n                fontSize: 14,\n                fontWeight: 500,\n                overflow: \"hidden\",\n                textOverflow: \"ellipsis\",\n                whiteSpace: \"nowrap\",\n              }}\n            >\n              {runner.config.name}\n            </span>\n          </div>\n          {!inGroup && (\n            <div\n              style={{\n                fontSize: 11,\n                color: \"var(--text-secondary)\",\n                marginTop: 1,\n                paddingLeft: runner.config.mode === \"service\" ? 32 : 0,\n              }}\n            >\n              {runner.config.repo_owner}/{runner.config.repo_name}\n            </div>\n          )}\n""",
    """            <span\n              className=\"font-mono\"\n              title={runner.config.display_name ? `GitHub runner: ${runner.config.name}` : undefined}\n              style={{\n                fontSize: 14,\n                fontWeight: 500,\n                overflow: \"hidden\",\n                textOverflow: \"ellipsis\",\n                whiteSpace: \"nowrap\",\n              }}\n            >\n              {displayName}\n            </span>\n          </div>\n          {(runner.config.display_name || !inGroup) && (\n            <div\n              style={{\n                fontSize: 11,\n                color: \"var(--text-secondary)\",\n                marginTop: 1,\n                paddingLeft: runner.config.mode === \"service\" ? 32 : 0,\n                overflow: \"hidden\",\n                textOverflow: \"ellipsis\",\n                whiteSpace: \"nowrap\",\n              }}\n            >\n              {runner.config.display_name && <>GitHub: {runner.config.name}</>}\n              {runner.config.display_name && !inGroup && <span> · </span>}\n              {!inGroup && (\n                <>\n                  {runner.config.repo_owner}/{runner.config.repo_name}\n                </>\n              )}\n            </div>\n          )}\n""",
)

replace_once(
    "apps/desktop/src/pages/Dashboard.tsx",
    """        const nameMatch = runner.config.name.toLowerCase().includes(q);\n        const repoMatch = `${runner.config.repo_owner}/${runner.config.repo_name}`\n""",
    """        const nameMatch = runner.config.name.toLowerCase().includes(q);\n        const displayNameMatch = runner.config.display_name?.toLowerCase().includes(q) ?? false;\n        const repoMatch = `${runner.config.repo_owner}/${runner.config.repo_name}`\n""",
)
replace_once(
    "apps/desktop/src/pages/Dashboard.tsx",
    """        if (nameMatch || repoMatch || prefixMatch) {\n""",
    """        if (displayNameMatch || nameMatch || repoMatch || prefixMatch) {\n""",
)
replace_once(
    "apps/desktop/src/pages/Dashboard.tsx",
    """          r.config.name.toLowerCase().includes(q) ||\n          `${r.config.repo_owner}/${r.config.repo_name}`.toLowerCase().includes(q)\n""",
    """          (r.config.display_name?.toLowerCase().includes(q) ?? false) ||\n          r.config.name.toLowerCase().includes(q) ||\n          `${r.config.repo_owner}/${r.config.repo_name}`.toLowerCase().includes(q)\n""",
)
replace_once(
    "apps/desktop/src/pages/Dashboard.tsx",
    """        (runner.config.name.toLowerCase().includes(q) ||\n          `${runner.config.repo_owner}/${runner.config.repo_name}`.toLowerCase().includes(q))\n""",
    """        ((runner.config.display_name?.toLowerCase().includes(q) ?? false) ||\n          runner.config.name.toLowerCase().includes(q) ||\n          `${runner.config.repo_owner}/${runner.config.repo_name}`.toLowerCase().includes(q))\n""",
)

replace_once(
    "apps/desktop/src/pages/RunnerDetail.tsx",
    """          <span className=\"breadcrumb-current\">{config.name}</span>\n""",
    """          <span\n            className=\"breadcrumb-current\"\n            title={config.display_name ? `GitHub runner: ${config.name}` : undefined}\n          >\n            {config.display_name ?? config.name}\n          </span>\n""",
)

replace_once(
    "apps/desktop/src/pages/MiniView.tsx",
    """              <span className=\"mini-runner-name\">{runner.config.name}</span>\n""",
    """              <span\n                className=\"mini-runner-name\"\n                title={runner.config.display_name ? `GitHub runner: ${runner.config.name}` : undefined}\n              >\n                {runner.config.display_name ?? runner.config.name}\n              </span>\n""",
)

replace_once(
    "apps/desktop/src/pages/TrayPanel.tsx",
    """                    {runner.config.name}\n""",
    """                    {runner.config.display_name ?? runner.config.name}\n""",
)

replace_once(
    "apps/desktop/src/components/ActiveRunners.tsx",
    """            title={`${runner.config.name} — ${runner.current_job ?? \"Starting...\"}`}\n""",
    """            title={`${runner.config.display_name ?? runner.config.name} — ${runner.current_job ?? \"Starting...\"}`}\n""",
)
replace_once(
    "apps/desktop/src/components/ActiveRunners.tsx",
    """              <span className=\"sidebar-active-name\">{runner.config.name}</span>\n""",
    """              <span className=\"sidebar-active-name\">\n                {runner.config.display_name ?? runner.config.name}\n              </span>\n""",
)

replace_once(
    "apps/desktop/src/hooks/useNotifications.ts",
    """          name: r.config.name,\n""",
    """          name: r.config.display_name ?? r.config.name,\n""",
)
replace_once(
    "apps/desktop/src/hooks/useNotifications.ts",
    """      const name = r.config.name;\n""",
    """      const name = r.config.display_name ?? r.config.name;\n""",
)

print("Display-name implementation applied successfully")
