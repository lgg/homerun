from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
files = [
    "apps/desktop/src-tauri/src/client.rs",
    "apps/desktop/src-tauri/src/commands.rs",
    "apps/desktop/src-tauri/src/lib.rs",
]
subprocess.run(["git", "checkout", "origin/master", "--", *files], cwd=root, check=True)


def replace_once(relative: str, old: str, new: str) -> None:
    path = root / relative
    content = path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {relative}, got {count}")
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


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

print("Minimal Tauri diff applied")
