from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        if new in text:
            return
        raise SystemExit(f"pattern not found in {path}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


def regex_once(path: str, pattern: str, replacement: str) -> None:
    text = read(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        if replacement in text:
            return
        raise SystemExit(f"regex matched {count} times in {path}: {pattern[:120]!r}")
    write(path, updated)


# Shared crash-safe persistence helper.
write(
    "crates/daemon/src/persistence.rs",
    r'''use anyhow::{Context, Result};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use uuid::Uuid;

fn temporary_path(path: &Path) -> Result<PathBuf> {
    let parent = path
        .parent()
        .context("Persistence path has no parent directory")?;
    let file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .context("Persistence path has no UTF-8 file name")?;
    Ok(parent.join(format!(
        ".{file_name}.{}.{}.tmp",
        std::process::id(),
        Uuid::new_v4()
    )))
}

/// Write a small state file without exposing a partially-written destination.
///
/// The data is written and synced through a unique sibling file first. Unix
/// then replaces the destination atomically. Windows uses a rollback-safe
/// backup because `rename` cannot replace an existing destination there.
pub fn atomic_write(path: &Path, contents: &[u8], unix_mode: Option<u32>) -> Result<()> {
    let parent = path
        .parent()
        .context("Persistence path has no parent directory")?;
    std::fs::create_dir_all(parent)
        .with_context(|| format!("Failed to create {}", parent.display()))?;

    let temp = temporary_path(path)?;
    let result = (|| -> Result<()> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        if let Some(mode) = unix_mode {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(mode);
        }

        let mut file = options
            .open(&temp)
            .with_context(|| format!("Failed to create {}", temp.display()))?;
        file.write_all(contents)
            .with_context(|| format!("Failed to write {}", temp.display()))?;
        file.sync_all()
            .with_context(|| format!("Failed to sync {}", temp.display()))?;
        drop(file);

        replace_destination(&temp, path)?;

        #[cfg(unix)]
        File::open(parent)
            .and_then(|directory| directory.sync_all())
            .with_context(|| format!("Failed to sync {}", parent.display()))?;
        Ok(())
    })();

    if result.is_err() {
        let _ = std::fs::remove_file(&temp);
    }
    result
}

#[cfg(not(windows))]
fn replace_destination(temp: &Path, destination: &Path) -> Result<()> {
    std::fs::rename(temp, destination).with_context(|| {
        format!(
            "Failed to replace {} with {}",
            destination.display(),
            temp.display()
        )
    })
}

#[cfg(windows)]
fn replace_destination(temp: &Path, destination: &Path) -> Result<()> {
    let backup = destination.with_extension(format!("bak-{}", Uuid::new_v4()));
    let had_destination = destination.exists();
    if had_destination {
        std::fs::rename(destination, &backup).with_context(|| {
            format!(
                "Failed to stage existing persistence file {}",
                destination.display()
            )
        })?;
    }

    match std::fs::rename(temp, destination) {
        Ok(()) => {
            if had_destination {
                let _ = std::fs::remove_file(&backup);
            }
            Ok(())
        }
        Err(error) => {
            if had_destination {
                let _ = std::fs::rename(&backup, destination);
            }
            Err(error).with_context(|| {
                format!(
                    "Failed to replace persistence file {}",
                    destination.display()
                )
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn atomic_write_replaces_existing_file_and_cleans_temporary_file() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("state.json");
        std::fs::write(&path, b"old").unwrap();

        atomic_write(&path, b"new", Some(0o600)).unwrap();

        assert_eq!(std::fs::read(&path).unwrap(), b"new");
        let leftovers = std::fs::read_dir(directory.path())
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp"))
            .count();
        assert_eq!(leftovers, 0);
    }
}
''',
)
replace_once(
    "crates/daemon/src/lib.rs",
    "pub mod platform;\npub mod runner;",
    "pub mod platform;\npub mod persistence;\npub mod runner;",
)

# Config files become crash-safe.
replace_once(
    "crates/daemon/src/config.rs",
    "use anyhow::Result;\n",
    "use crate::persistence::atomic_write;\nuse anyhow::Result;\n",
)
replace_once(
    "crates/daemon/src/config.rs",
    '''    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(path, toml::to_string_pretty(self)?)?;
        Ok(())
    }''',
    '''    pub fn save(&self, path: &Path) -> Result<()> {
        let serialized = toml::to_string_pretty(self)?;
        atomic_write(path, serialized.as_bytes(), Some(0o600))
    }''',
)

# Credential writes are atomic and permission-restricted from creation time.
replace_once(
    "crates/daemon/src/auth/keychain.rs",
    "use anyhow::Result;\n",
    "use crate::persistence::atomic_write;\nuse anyhow::Result;\n",
)
regex_once(
    "crates/daemon/src/auth/keychain.rs",
    r'''fn store_token_at\(path: &std::path::Path, token: &str\) -> Result<\(\)> \{.*?\n\}''',
    '''fn store_token_at(path: &std::path::Path, token: &str) -> Result<()> {
    atomic_write(path, token.as_bytes(), Some(0o600))
}''',
)
replace_once(
    "crates/daemon/src/auth/keychain.rs",
    '''        Ok(token) if !token.is_empty() => Ok(Some(token)),''',
    '''        Ok(token) if !token.trim().is_empty() => Ok(Some(token.trim().to_string())),''',
)

# Scanner results use the same crash-safe primitive.
replace_once(
    "crates/daemon/src/scanner/persistence.rs",
    '''    let json = serde_json::to_string_pretty(results)?;
    tokio::fs::write(path, json).await?;
    Ok(())''',
    '''    let json = serde_json::to_string_pretty(results)?;
    let path = path.to_path_buf();
    tokio::task::spawn_blocking(move || {
        crate::persistence::atomic_write(&path, json.as_bytes(), Some(0o600))
    })
    .await??;
    Ok(())''',
)

# Preferences are committed to memory only after durable persistence succeeds.
replace_once(
    "crates/daemon/src/api/preferences.rs",
    '''    let mut config = state.config.write().await;
    config.preferences = prefs.clone();

    let config_path = config.config_path();
    config
        .save(&config_path)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    state''',
    '''    let mut config = state.config.write().await;
    let mut updated = config.clone();
    updated.preferences = prefs.clone();

    let config_path = updated.config_path();
    updated
        .save(&config_path)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    *config = updated;
    drop(config);

    state''',
)

# Auth state: never report an unvalidated token as authenticated, invalidate stale
# login/device-flow attempts, and never lazily resurrect a failed logout.
replace_once(
    "crates/daemon/src/auth/mod.rs",
    '''use std::sync::Arc;
use tokio::sync::RwLock;''',
    '''use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{Mutex, RwLock};''',
)
replace_once(
    "crates/daemon/src/auth/mod.rs",
    '''pub struct AuthManager {
    state: Arc<RwLock<Option<AuthState>>>,
}''',
    '''pub struct AuthManager {
    state: Arc<RwLock<Option<AuthState>>>,
    generation: Arc<AtomicU64>,
    commit_lock: Arc<Mutex<()>>,
}''',
)
replace_once(
    "crates/daemon/src/auth/mod.rs",
    '''        Self {
            state: Arc::new(RwLock::new(None)),
        }''',
    '''        Self {
            state: Arc::new(RwLock::new(None)),
            generation: Arc::new(AtomicU64::new(0)),
            commit_lock: Arc::new(Mutex::new(())),
        }''',
)
replace_once(
    "crates/daemon/src/auth/mod.rs",
    '''            state: Arc::new(RwLock::new(Some(AuthState {
                token: "ghp_test_token".to_string(),
                user: GitHubUser {
                    login: "test-user".to_string(),
                    avatar_url: "https://example.com/avatar.png".to_string(),
                },
            }))),
        }''',
    '''            state: Arc::new(RwLock::new(Some(AuthState {
                token: "ghp_test_token".to_string(),
                user: GitHubUser {
                    login: "test-user".to_string(),
                    avatar_url: "https://example.com/avatar.png".to_string(),
                },
            }))),
            generation: Arc::new(AtomicU64::new(0)),
            commit_lock: Arc::new(Mutex::new(())),
        }''',
)
regex_once(
    "crates/daemon/src/auth/mod.rs",
    r'''    /// Attempt to restore a previously saved token from the credential store on startup\..*?    /// Validate the PAT via the GitHub API, store it in the credential store, and update state\.\n''',
    '''    fn begin_auth_attempt(&self) -> u64 {
        self.generation.fetch_add(1, Ordering::SeqCst) + 1
    }

    fn is_current_attempt(&self, generation: u64) -> bool {
        self.generation.load(Ordering::SeqCst) == generation
    }

    /// Attempt to restore a previously saved token from the credential store on startup.
    pub async fn try_restore(&self) -> Result<()> {
        let Some(token) = keychain::get_token(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)? else {
            return Ok(());
        };
        let generation = self.begin_auth_attempt();
        match self.validate_token(&token).await {
            Ok(user) => {
                let _commit = self.commit_lock.lock().await;
                if !self.is_current_attempt(generation) {
                    return Ok(());
                }
                tracing::info!("Restored GitHub authentication from the credential store");
                *self.state.write().await = Some(AuthState { token, user });
            }
            Err(error) => {
                // Keep the credential file because this can be a transient network failure,
                // but do not claim that an unvalidated/expired token is authenticated.
                tracing::warn!("Could not validate stored token (keeping it for the next restart): {error}");
                *self.state.write().await = None;
            }
        }
        Ok(())
    }

    /// Validate the PAT via the GitHub API, store it in the credential store, and update state.
''',
)
regex_once(
    "crates/daemon/src/auth/mod.rs",
    r'''    pub async fn login_with_pat\(&self, token: &str\) -> Result<GitHubUser> \{.*?\n    \}\n\n    /// Remove the token''',
    '''    pub async fn login_with_pat(&self, token: &str) -> Result<GitHubUser> {
        let generation = self.begin_auth_attempt();
        let user = self.validate_token(token).await?;
        let _commit = self.commit_lock.lock().await;
        if !self.is_current_attempt(generation) {
            return Err(anyhow!("Authentication attempt was superseded"));
        }
        keychain::store_token(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, token)?;
        *self.state.write().await = Some(AuthState {
            token: token.to_string(),
            user: user.clone(),
        });
        Ok(user)
    }

    /// Remove the token''',
)
regex_once(
    "crates/daemon/src/auth/mod.rs",
    r'''    pub async fn logout\(&self\) -> Result<\(\)> \{.*?\n    \}\n\n    pub async fn status''',
    '''    pub async fn logout(&self) -> Result<()> {
        // Invalidate every in-flight PAT/device-flow attempt before touching state.
        self.begin_auth_attempt();
        let _commit = self.commit_lock.lock().await;
        keychain::delete_token(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)?;
        *self.state.write().await = None;
        Ok(())
    }

    pub async fn status''',
)
regex_once(
    "crates/daemon/src/auth/mod.rs",
    r'''    pub async fn status\(&self\) -> AuthStatus \{.*?\n    \}\n\n    pub async fn token\(&self\) -> Option<String> \{.*?\n    \}\n\n    /// Initiate''',
    '''    pub async fn status(&self) -> AuthStatus {
        match &*self.state.read().await {
            Some(state) => AuthStatus {
                authenticated: true,
                user: Some(state.user.clone()),
            },
            None => AuthStatus {
                authenticated: false,
                user: None,
            },
        }
    }

    pub async fn token(&self) -> Option<String> {
        self.state
            .read()
            .await
            .as_ref()
            .map(|state| state.token.clone())
    }

    /// Initiate''',
)
regex_once(
    "crates/daemon/src/auth/mod.rs",
    r'''    pub async fn poll_device_flow\(&self, device_code: &str, interval: u64\) -> Result<GitHubUser> \{.*?\n    \}\n\n    async fn validate_token''',
    '''    pub async fn poll_device_flow(&self, device_code: &str, interval: u64) -> Result<GitHubUser> {
        let generation = self.begin_auth_attempt();
        let client = reqwest::Client::new();
        let deadline =
            std::time::Instant::now() + std::time::Duration::from_secs(DEVICE_FLOW_TIMEOUT_SECS);
        let mut poll_interval = interval;

        loop {
            if !self.is_current_attempt(generation) {
                return Err(anyhow!("Device flow was cancelled or superseded"));
            }
            if std::time::Instant::now() > deadline {
                return Err(anyhow!("Device flow authorization timed out"));
            }

            tokio::time::sleep(std::time::Duration::from_secs(poll_interval)).await;
            if !self.is_current_attempt(generation) {
                return Err(anyhow!("Device flow was cancelled or superseded"));
            }

            let response = client
                .post(ACCESS_TOKEN_URL)
                .header("Accept", "application/json")
                .form(&[
                    ("client_id", GITHUB_CLIENT_ID),
                    ("device_code", device_code),
                    ("grant_type", "urn:ietf:params:oauth:grant-type:device_code"),
                ])
                .send()
                .await?;

            let poll: PollResponse = response.json().await?;

            if let Some(token) = poll.access_token {
                let user = self.validate_token(&token).await?;
                let _commit = self.commit_lock.lock().await;
                if !self.is_current_attempt(generation) {
                    return Err(anyhow!("Device flow was cancelled or superseded"));
                }
                keychain::store_token(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, &token)?;
                tracing::info!("Token stored in the HomeRun credential store");
                *self.state.write().await = Some(AuthState {
                    token,
                    user: user.clone(),
                });
                return Ok(user);
            }

            match poll.error.as_deref() {
                Some("authorization_pending") => {}
                Some("slow_down") => {
                    poll_interval = poll.interval.unwrap_or(poll_interval + 5);
                }
                Some("expired_token") => {
                    return Err(anyhow!("Device flow code expired. Please start again."));
                }
                Some("access_denied") => {
                    return Err(anyhow!("Authorization denied by user."));
                }
                Some(other) => return Err(anyhow!("Device flow error: {other}")),
                None => return Err(anyhow!("Unexpected empty response from GitHub")),
            }
        }
    }

    async fn validate_token''',
)
replace_once(
    "crates/daemon/src/auth/mod.rs",
    '''            state: Arc::new(RwLock::new(Some(AuthState {
                token: "ghp_fake_test_token".to_string(),
                user,
            }))),
        }''',
    '''            state: Arc::new(RwLock::new(Some(AuthState {
                token: "ghp_fake_test_token".to_string(),
                user,
            }))),
            generation: Arc::new(AtomicU64::new(0)),
            commit_lock: Arc::new(Mutex::new(())),
        }''',
)
# Add a deterministic generation regression test before the test module closes.
auth_text = read("crates/daemon/src/auth/mod.rs")
marker = "\n}\n"
insert_at = auth_text.rfind(marker)
if insert_at < 0:
    raise SystemExit("auth test module closing brace not found")
if "test_logout_invalidates_in_flight_auth_attempt" not in auth_text:
    auth_text = auth_text[:insert_at] + '''

    #[tokio::test]
    async fn test_logout_invalidates_in_flight_auth_attempt() {
        let manager = authenticated_manager("octocat");
        let attempt = manager.begin_auth_attempt();
        manager.logout().await.unwrap();
        assert!(!manager.is_current_attempt(attempt));
        assert!(manager.token().await.is_none());
    }
''' + auth_text[insert_at:]
    write("crates/daemon/src/auth/mod.rs", auth_text)

# Authenticated test state must use the same AuthManager inside RunnerManager.
replace_once(
    "crates/daemon/src/server.rs",
    '''        let mut state = Self::new_test();
        state.auth = AuthManager::new_test_authenticated();
        state''',
    '''        let mut state = Self::new_test();
        state.auth = AuthManager::new_test_authenticated();
        state.runner_manager.set_auth_manager(state.auth.clone());
        state''',
)

# Do not hold the global Tauri client mutex across daemon I/O.
commands_path = "apps/desktop/src-tauri/src/commands.rs"
commands = read(commands_path)
commands = commands.replace(
    "let client = state.client.lock().await;",
    "let client = state.client.lock().await.clone_connection();",
)
write(commands_path, commands)

# Honest release documentation and a workflow typo found by the packaging audit.
replace_once(
    "README.md",
    "https://img.shields.io/github/v/release/aGallea/homerun",
    "https://img.shields.io/github/v/release/lgg/homerun",
)
replace_once(
    "README.md",
    '''### Install (macOS — Homebrew)

```sh
brew tap aGallea/homerun

# CLI tools (homerun + homerund)
brew install homerun

# Desktop app (not code-signed — remove quarantine after install)
brew install --cask homerun
xattr -cr /Applications/HomeRun.app
```
''',
    '''### Install (macOS — Homebrew)

Homebrew publication is optional and only runs when the repository variable
`HOMEBREW_TAP_REPOSITORY` and the `TAP_GITHUB_TOKEN` secret are configured.
Until a tap is listed in the release notes, install the signed release assets
above instead of assuming an upstream or third-party tap.
''',
)
replace_once(
    ".github/workflows/release-build.yml",
    "- name: Packae CII binaries",
    "- name: Package CLI binaries",
)

print("backend audit patch applied")
