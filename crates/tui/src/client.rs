use anyhow::{bail, Context, Result};
use bytes::Bytes;
use futures_util::StreamExt;
use http_body_util::{BodyExt, Full};
use hyper::{Method, Request, StatusCode};
use hyper_util::rt::TokioIo;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use tokio::net::UnixStream;

#[cfg(windows)]
use tokio::net::windows::named_pipe::ClientOptions;

use homerun_common::ipc::{self, daemon_endpoint};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunnerConfig {
    pub id: String,
    pub name: String,
    pub display_name: Option<String>,
    pub repo_owner: String,
    pub repo_name: String,
    pub labels: Vec<String>,
    pub mode: RunnerMode,
    pub work_dir: PathBuf,
    pub runner_version: Option<String>,
    pub group_id: Option<String>,
    pub container: Option<ContainerConfig>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContainerConfig {
    pub image: String,
    pub memory_limit: Option<u64>,
    pub cpu_limit: Option<f64>,
    pub env: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RunnerMode {
    AppManaged,
    Service,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RunnerState {
    Creating,
    Registering,
    Online,
    Busy,
    Stopping,
    Offline,
    Error,
    Deleting,
}

impl RunnerState {
    pub fn is_active(&self) -> bool {
        matches!(self, Self::Online | Self::Busy)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CurrentJob {
    pub name: String,
    pub started_at: chrono::DateTime<chrono::Utc>,
    pub workflow_name: Option<String>,
    pub repository: Option<String>,
    pub branch: Option<String>,
    pub commit_sha: Option<String>,
    pub run_url: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobHistoryEntry {
    pub id: String,
    pub job_name: String,
    pub workflow_name: Option<String>,
    pub repository: Option<String>,
    pub branch: Option<String>,
    pub commit_sha: Option<String>,
    pub run_url: Option<String>,
    pub started_at: chrono::DateTime<chrono::Utc>,
    pub completed_at: chrono::DateTime<chrono::Utc>,
    pub succeeded: bool,
    pub duration_seconds: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunnerInfo {
    pub config: RunnerConfig,
    pub state: RunnerState,
    pub pid: Option<u32>,
    pub current_job: Option<String>,
    pub current_job_info: Option<CurrentJob>,
    pub last_completed_job: Option<JobHistoryEntry>,
    pub error_message: Option<String>,
    pub cpu_percent: f32,
    pub memory_bytes: u64,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MetricsResponse {
    pub system: SystemMetrics,
    pub runners: Vec<RunnerMetrics>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SystemMetrics {
    pub cpu_percent: f32,
    pub memory_used: u64,
    pub memory_total: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunnerMetrics {
    pub runner_id: String,
    pub cpu_percent: f32,
    pub memory_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthStatus {
    pub authenticated: bool,
    pub user: Option<GitHubUser>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GitHubUser {
    pub login: String,
    pub name: Option<String>,
    pub avatar_url: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceFlowResponse {
    pub device_code: String,
    pub user_code: String,
    pub verification_uri: String,
    pub expires_in: u64,
    pub interval: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RepoInfo {
    pub full_name: String,
    pub private: bool,
    pub default_branch: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiscoveredRepo {
    pub full_name: String,
    pub source: String,
    pub workflow_files: Vec<String>,
    pub local_path: Option<String>,
    pub matched_labels: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogEntry {
    pub runner_id: String,
    pub timestamp: chrono::DateTime<chrono::Utc>,
    pub line: String,
    pub stream: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StepInfo {
    pub number: u32,
    pub name: String,
    pub status: String,
    pub started_at: Option<chrono::DateTime<chrono::Utc>>,
    pub completed_at: Option<chrono::DateTime<chrono::Utc>>,
    pub conclusion: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StepsResponse {
    pub runner_id: String,
    pub steps: Vec<StepInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DaemonLogEntry {
    pub timestamp: String,
    pub level: String,
    pub target: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceStatusResponse {
    pub installed: bool,
    pub enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateRunnerRequest {
    pub repo_full_name: String,
    pub name: Option<String>,
    pub labels: Option<Vec<String>>,
    pub mode: Option<RunnerMode>,
    pub group_id: Option<String>,
    pub container: Option<ContainerConfig>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchCreateResponse {
    pub runners: Vec<RunnerInfo>,
    pub group_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupActionResult {
    pub runner_id: String,
    pub success: bool,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupActionResponse {
    pub group_id: String,
    pub results: Vec<GroupActionResult>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScaleGroupResponse {
    pub group_id: String,
    pub target_count: u32,
    pub added: Vec<RunnerInfo>,
    pub removed: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DockerStatusResponse {
    pub available: bool,
    pub version: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DaemonClient {
    endpoint: String,
}

impl Default for DaemonClient {
    fn default() -> Self {
        Self::new(daemon_endpoint())
    }
}

impl DaemonClient {
    pub fn new(endpoint: String) -> Self {
        Self { endpoint }
    }

    pub fn socket_path(&self) -> &Path {
        Path::new(&self.endpoint)
    }

    fn http_request(
        method: &str,
        path: &str,
        body: Option<String>,
    ) -> Result<Request<Full<Bytes>>> {
        let method = Method::from_bytes(method.as_bytes()).context("invalid HTTP method")?;
        let mut builder = Request::builder().method(method).uri(path);
        if body.is_some() {
            builder = builder.header("content-type", "application/json");
        }
        builder
            .body(Full::new(Bytes::from(body.unwrap_or_default())))
            .context("building daemon request")
    }

    #[cfg(unix)]
    async fn send_request(
        &self,
        method: &str,
        path: &str,
        body: Option<String>,
    ) -> Result<(StatusCode, String)> {
        let stream = UnixStream::connect(&self.endpoint)
            .await
            .with_context(|| format!("connecting to daemon socket {}", self.endpoint))?;
        let (mut sender, connection) =
            hyper::client::conn::http1::handshake(TokioIo::new(stream)).await?;
        tokio::spawn(async move {
            let _ = connection.await;
        });

        let response = sender
            .send_request(Self::http_request(method, path, body)?)
            .await?;
        let status = response.status();
        let bytes = response.into_body().collect().await?.to_bytes();
        Ok((status, String::from_utf8_lossy(&bytes).into_owned()))
    }

    #[cfg(windows)]
    async fn send_request(
        &self,
        method: &str,
        path: &str,
        body: Option<String>,
    ) -> Result<(StatusCode, String)> {
        let pipe_name = ipc::windows_pipe_name(&self.endpoint);
        let stream = loop {
            match ClientOptions::new().open(&pipe_name) {
                Ok(stream) => break stream,
                Err(error) if error.raw_os_error() == Some(231) => {
                    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                }
                Err(error) => {
                    return Err(error)
                        .with_context(|| format!("connecting to daemon pipe {pipe_name}"));
                }
            }
        };
        let (mut sender, connection) =
            hyper::client::conn::http1::handshake(TokioIo::new(stream)).await?;
        tokio::spawn(async move {
            let _ = connection.await;
        });
        let response = sender
            .send_request(Self::http_request(method, path, body)?)
            .await?;
        let status = response.status();
        let bytes = response.into_body().collect().await?.to_bytes();
        Ok((status, String::from_utf8_lossy(&bytes).into_owned()))
    }

    #[cfg(not(any(unix, windows)))]
    async fn send_request(
        &self,
        _method: &str,
        _path: &str,
        _body: Option<String>,
    ) -> Result<(StatusCode, String)> {
        bail!("unsupported platform")
    }

    async fn request(&self, method: &str, path: &str, body: Option<String>) -> Result<String> {
        let (status_code, text) = self.send_request(method, path, body).await?;
        if status_code.is_success() {
            Ok(text)
        } else {
            bail!("daemon returned {status_code}: {text}")
        }
    }

    async fn raw_request(
        &self,
        method: &str,
        path: &str,
        body: Option<String>,
    ) -> Result<(StatusCode, String)> {
        self.send_request(method, path, body).await
    }

    pub async fn health(&self) -> Result<()> {
        self.request("GET", "/health", None).await?;
        Ok(())
    }

    /// Returns the number of active runners being stopped during shutdown.
    pub async fn shutdown(&self) -> Result<usize> {
        let body = self.request("POST", "/daemon/shutdown", None).await?;
        let json: serde_json::Value = serde_json::from_str(&body).unwrap_or_default();
        Ok(json["active_runners"].as_u64().unwrap_or(0) as usize)
    }

    pub async fn auth_status(&self) -> Result<AuthStatus> {
        let body = self.request("GET", "/auth/status", None).await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn login_with_token(&self, token: &str) -> Result<AuthStatus> {
        let body = serde_json::json!({ "token": token }).to_string();
        let response = self.request("POST", "/auth/token", Some(body)).await?;
        Ok(serde_json::from_str(&response)?)
    }

    pub async fn logout(&self) -> Result<()> {
        self.request("DELETE", "/auth", None).await?;
        Ok(())
    }

    pub async fn start_device_flow(&self) -> Result<DeviceFlowResponse> {
        let body = self.request("POST", "/auth/device", None).await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn poll_device_flow(
        &self,
        device_code: &str,
        interval: u64,
    ) -> Result<Option<AuthStatus>> {
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(interval)).await;
            let payload = serde_json::json!({ "device_code": device_code }).to_string();
            let (status, body) = self
                .raw_request("POST", "/auth/device/poll", Some(payload.to_string()))
                .await?;
            if status == StatusCode::ACCEPTED {
                continue;
            }
            if status.is_success() {
                return Ok(Some(serde_json::from_str(&body)?));
            }
            bail!("device flow failed ({status}): {body}")
        }
    }

    pub async fn list_runners(&self) -> Result<Vec<RunnerInfo>> {
        let body = self.request("GET", "/runners", None).await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn get_runner(&self, id: &str) -> Result<RunnerInfo> {
        let body = self.request("GET", &format!("/runners/{id}"), None).await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn create_runner(&self, req: &CreateRunnerRequest) -> Result<RunnerInfo> {
        let body = self
            .request("POST", "/runners", Some(serde_json::to_string(req)?))
            .await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn update_runner(
        &self,
        id: &str,
        labels: Option<Vec<String>>,
        mode: Option<RunnerMode>,
        display_name: Option<String>,
    ) -> Result<RunnerInfo> {
        let mut body = serde_json::Map::new();
        if let Some(labels) = labels {
            body.insert("labels".to_string(), serde_json::json!(labels));
        }
        if let Some(mode) = mode {
            body.insert("mode".to_string(), serde_json::json!(mode));
        }
        if let Some(display_name) = display_name {
            body.insert("display_name".to_string(), serde_json::json!(display_name));
        }
        let response = self
            .request(
                "PATCH",
                &format!("/runners/{id}"),
                Some(serde_json::Value::Object(body).to_string()),
            )
            .await?;
        Ok(serde_json::from_str(&response)?)
    }

    pub async fn delete_runner(&self, id: &str) -> Result<()> {
        self.request("DELETE", &format!("/runners/{id}"), None)
            .await?;
        Ok(())
    }

    pub async fn start_runner(&self, id: &str) -> Result<()> {
        self.request("POST", &format!("/runners/{id}/start"), None)
            .await?;
        Ok(())
    }

    pub async fn stop_runner(&self, id: &str) -> Result<()> {
        self.request("POST", &format!("/runners/{id}/stop"), None)
            .await?;
        Ok(())
    }

    pub async fn restart_runner(&self, id: &str) -> Result<()> {
        self.request("POST", &format!("/runners/{id}/restart"), None)
            .await?;
        Ok(())
    }

    pub async fn recent_runner_logs(&self, id: &str) -> Result<Vec<LogEntry>> {
        let body = self
            .request("GET", &format!("/runners/{id}/logs/recent"), None)
            .await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn list_repos(&self) -> Result<Vec<RepoInfo>> {
        let body = self.request("GET", "/repos", None).await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn get_metrics(&self) -> Result<MetricsResponse> {
        let body = self.request("GET", "/metrics", None).await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn scan_local(&self, path: &str) -> Result<Vec<DiscoveredRepo>> {
        let req = serde_json::json!({ "workspace_path": path });
        let body = self
            .request("POST", "/scan/local", Some(serde_json::to_string(&req)?))
            .await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn scan_remote(&self) -> Result<Vec<DiscoveredRepo>> {
        let body = self.request("POST", "/scan/remote", None).await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn create_batch(
        &self,
        repo_full_name: &str,
        count: u32,
        name_prefix: Option<String>,
        labels: Option<Vec<String>>,
        mode: Option<RunnerMode>,
        container: Option<ContainerConfig>,
    ) -> Result<BatchCreateResponse> {
        let body = serde_json::json!({
            "repo_full_name": repo_full_name,
            "count": count,
            "name_prefix": name_prefix,
            "labels": labels,
            "mode": mode,
            "container": container,
        });
        let response = self
            .request("POST", "/runners/batch", Some(body.to_string()))
            .await?;
        Ok(serde_json::from_str(&response)?)
    }

    pub async fn start_group(&self, group_id: &str) -> Result<GroupActionResponse> {
        let body = self
            .request("POST", &format!("/runners/groups/{group_id}/start"), None)
            .await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn stop_group(&self, group_id: &str) -> Result<GroupActionResponse> {
        let body = self
            .request("POST", &format!("/runners/groups/{group_id}/stop"), None)
            .await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn restart_group(&self, group_id: &str) -> Result<GroupActionResponse> {
        let body = self
            .request("POST", &format!("/runners/groups/{group_id}/restart"), None)
            .await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn delete_group(&self, group_id: &str) -> Result<GroupActionResponse> {
        let body = self
            .request("DELETE", &format!("/runners/groups/{group_id}"), None)
            .await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn scale_group(&self, group_id: &str, count: u32) -> Result<ScaleGroupResponse> {
        let body = serde_json::json!({ "count": count }).to_string();
        let response = self
            .request("PATCH", &format!("/runners/groups/{group_id}"), Some(body))
            .await?;
        Ok(serde_json::from_str(&response)?)
    }

    pub async fn service_status(&self) -> Result<ServiceStatusResponse> {
        let body = self.request("GET", "/service/status", None).await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn install_service(&self) -> Result<()> {
        self.request("POST", "/service/install", None).await?;
        Ok(())
    }

    pub async fn uninstall_service(&self) -> Result<()> {
        self.request("POST", "/service/uninstall", None).await?;
        Ok(())
    }

    pub async fn daemon_logs(&self, level: Option<&str>) -> Result<Vec<DaemonLogEntry>> {
        let query = level.map(|l| format!("?level={l}")).unwrap_or_default();
        let body = self
            .request("GET", &format!("/daemon/logs/recent{query}"), None)
            .await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn runner_steps(&self, runner_id: &str) -> Result<StepsResponse> {
        let body = self
            .request("GET", &format!("/runners/{runner_id}/steps"), None)
            .await?;
        Ok(serde_json::from_str(&body)?)
    }

    pub async fn runner_history(&self, runner_id: &str) -> Result<Vec<JobHistoryEntry>> {
        let body = self
            .request("GET", &format!("/runners/{runner_id}/history"), None)
            .await?;
        Ok(serde_json::from_str(&body)?)
    }
}
