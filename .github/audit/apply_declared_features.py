from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    content = read(path)
    actual = content.count(old)
    if actual < count:
        raise RuntimeError(f"{path}: expected at least {count} occurrence(s), found {actual}: {old[:120]!r}")
    content = content.replace(old, new, count)
    write(path, content)


# ---------------------------------------------------------------------------
# Workflow label parsing shared by local and remote discovery.
# ---------------------------------------------------------------------------
replace(
    "crates/daemon/src/lib.rs",
    "pub mod updater;",
    "pub mod updater;\npub mod workflow;",
)

write(
    "crates/daemon/src/workflow.rs",
    r'''use std::collections::HashSet;

/// Return configured runner labels referenced by `runs-on` declarations in a
/// GitHub Actions workflow. The parser deliberately handles the documented
/// scalar, quoted, flow-sequence, and block-sequence YAML forms without trying
/// to evaluate expressions such as `${{ matrix.runner }}`.
pub fn matching_runs_on_labels(content: &str, labels: &[String]) -> Vec<String> {
    let mut wanted = Vec::new();
    let mut seen_wanted = HashSet::new();
    for label in labels {
        let trimmed = label.trim();
        if trimmed.is_empty() {
            continue;
        }
        let normalized = trimmed.to_ascii_lowercase();
        if seen_wanted.insert(normalized.clone()) {
            wanted.push((normalized, trimmed.to_string()));
        }
    }
    if wanted.is_empty() {
        return Vec::new();
    }

    let lines: Vec<&str> = content.lines().collect();
    let mut candidates = Vec::new();

    for (index, raw_line) in lines.iter().enumerate() {
        let line = strip_yaml_comment(raw_line);
        let trimmed = line.trim_start();
        let Some(value) = trimmed.strip_prefix("runs-on:") else {
            continue;
        };

        let value = value.trim();
        if !value.is_empty() {
            candidates.extend(parse_inline_value(value));
            continue;
        }

        let base_indent = indentation(line);
        for following in lines.iter().skip(index + 1) {
            let following = strip_yaml_comment(following);
            if following.trim().is_empty() {
                continue;
            }
            if indentation(following) <= base_indent {
                break;
            }
            let nested = following.trim_start();
            if let Some(item) = nested.strip_prefix("- ") {
                candidates.push(normalize_scalar(item));
            }
        }
    }

    let candidate_set: HashSet<String> = candidates
        .into_iter()
        .filter(|candidate| !candidate.is_empty())
        .collect();
    wanted
        .into_iter()
        .filter_map(|(normalized, original)| candidate_set.contains(&normalized).then_some(original))
        .collect()
}

fn parse_inline_value(value: &str) -> Vec<String> {
    let trimmed = value.trim();
    if trimmed.starts_with('[') && trimmed.ends_with(']') {
        return trimmed[1..trimmed.len() - 1]
            .split(',')
            .map(normalize_scalar)
            .filter(|value| !value.is_empty())
            .collect();
    }
    vec![normalize_scalar(trimmed)]
}

fn normalize_scalar(value: &str) -> String {
    let mut value = value.trim().trim_end_matches(',').trim();
    loop {
        let bytes = value.as_bytes();
        if bytes.len() >= 2
            && ((bytes[0] == b'"' && bytes[bytes.len() - 1] == b'"')
                || (bytes[0] == b'\'' && bytes[bytes.len() - 1] == b'\''))
        {
            value = value[1..value.len() - 1].trim();
        } else {
            break;
        }
    }
    value.to_ascii_lowercase()
}

fn strip_yaml_comment(line: &str) -> &str {
    let mut in_single = false;
    let mut in_double = false;
    let mut escaped = false;
    for (index, ch) in line.char_indices() {
        if escaped {
            escaped = false;
            continue;
        }
        match ch {
            '\\' if in_double => escaped = true,
            '\'' if !in_double => in_single = !in_single,
            '"' if !in_single => in_double = !in_double,
            '#' if !in_single && !in_double => return &line[..index],
            _ => {}
        }
    }
    line
}

fn indentation(line: &str) -> usize {
    line.chars().take_while(|ch| ch.is_whitespace()).count()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn labels() -> Vec<String> {
        vec!["self-hosted".to_string(), "Linux".to_string(), "x64".to_string()]
    }

    #[test]
    fn matches_scalar_and_quoted_values() {
        assert_eq!(
            matching_runs_on_labels("jobs:\n  build:\n    runs-on: self-hosted\n", &labels()),
            vec!["self-hosted"]
        );
        assert_eq!(
            matching_runs_on_labels("runs-on: \"SELF-HOSTED\"\n", &labels()),
            vec!["self-hosted"]
        );
    }

    #[test]
    fn matches_inline_and_block_sequences() {
        assert_eq!(
            matching_runs_on_labels("runs-on: [self-hosted, Linux, x64]\n", &labels()),
            vec!["self-hosted", "Linux", "x64"]
        );
        assert_eq!(
            matching_runs_on_labels(
                "runs-on:\n  - self-hosted\n  - 'Linux'\n  - x64 # architecture\nsteps:\n  - run: echo ok\n",
                &labels(),
            ),
            vec!["self-hosted", "Linux", "x64"]
        );
    }

    #[test]
    fn ignores_comments_substrings_and_expressions() {
        let workflow = "# runs-on: self-hosted\nruns-on: custom-self-hosted-pool\nother: self-hosted\n";
        assert!(matching_runs_on_labels(workflow, &labels()).is_empty());
        assert!(matching_runs_on_labels("runs-on: ${{ matrix.runner }}\n", &labels()).is_empty());
    }

    #[test]
    fn deduplicates_configured_labels_case_insensitively() {
        let configured = vec!["self-hosted".into(), "SELF-HOSTED".into()];
        assert_eq!(
            matching_runs_on_labels("runs-on: [self-hosted, self-hosted]\n", &configured),
            vec!["self-hosted"]
        );
    }
}
''',
)

old_github_match = '''            let file_matched = labels
                .iter()
                .any(|label| content.contains(&format!("runs-on: {}", label)));
            if file_matched {
                matching.push(format!(".github/workflows/{}", item.name));
                for label in labels {
                    if content.contains(&format!("runs-on: {}", label))
                        && !matched_labels.contains(label)
                    {
                        matched_labels.push(label.clone());
                    }
                }
            }
'''
new_github_match = '''            let file_matches = crate::workflow::matching_runs_on_labels(&content, labels);
            if !file_matches.is_empty() {
                matching.push(format!(".github/workflows/{}", item.name));
                for label in file_matches {
                    if !matched_labels.contains(&label) {
                        matched_labels.push(label);
                    }
                }
            }
'''
replace("crates/daemon/src/github/mod.rs", old_github_match, new_github_match)

old_local_match = '''            let file_matched = labels
                .iter()
                .any(|label| content.contains(&format!("runs-on: {}", label)));
            if file_matched {
                let rel = path
                    .strip_prefix(repo_root)
                    .unwrap_or(&path)
                    .to_string_lossy()
                    .to_string();
                matching_files.push(rel);
                for label in labels {
                    if content.contains(&format!("runs-on: {}", label))
                        && !matched_labels.contains(label)
                    {
                        matched_labels.push(label.clone());
                    }
                }
            }
'''
new_local_match = '''            let file_matches = crate::workflow::matching_runs_on_labels(&content, labels);
            if !file_matches.is_empty() {
                let rel = path
                    .strip_prefix(repo_root)
                    .unwrap_or(&path)
                    .to_string_lossy()
                    .to_string();
                matching_files.push(rel);
                for label in file_matches {
                    if !matched_labels.contains(&label) {
                        matched_labels.push(label);
                    }
                }
            }
'''
replace("crates/daemon/src/scanner/mod.rs", old_local_match, new_local_match)

old_check_match = '''            let file_matches = labels
                .iter()
                .any(|label| content.contains(&format!("runs-on: {}", label)));
            if file_matches {
                let rel = path
                    .strip_prefix(repo_root)
                    .unwrap_or(&path)
                    .to_string_lossy()
                    .to_string();
                matching_files.push(rel);
                for label in labels {
                    if content.contains(&format!("runs-on: {}", label))
                        && !matched_labels.contains(label)
                    {
                        matched_labels.push(label.clone());
                    }
                }
            }
'''
new_check_match = '''            let file_matches = crate::workflow::matching_runs_on_labels(&content, labels);
            if !file_matches.is_empty() {
                let rel = path
                    .strip_prefix(repo_root)
                    .unwrap_or(&path)
                    .to_string_lossy()
                    .to_string();
                matching_files.push(rel);
                for label in file_matches {
                    if !matched_labels.contains(&label) {
                        matched_labels.push(label);
                    }
                }
            }
'''
replace("crates/daemon/src/scanner/mod.rs", old_check_match, new_check_match)

replace(
    "crates/daemon/src/scanner/mod.rs",
    '''// ---------------------------------------------------------------------------
// Local scan
// ---------------------------------------------------------------------------

''',
    '''// ---------------------------------------------------------------------------
// Local scan
// ---------------------------------------------------------------------------

fn should_skip_directory(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "node_modules"
            | "target"
            | "vendor"
            | "dist"
            | "build"
            | "coverage"
            | ".next"
            | ".venv"
            | "venv"
            | "out"
    )
}

''',
)

skip_anchor = '''        // Skip hidden directories other than `.github`
        if name_str.starts_with('.') && name_str != ".github" {
            continue;
        }

'''
skip_replacement = '''        // Skip hidden and generated/vendor trees that cannot be workspace repos.
        if (name_str.starts_with('.') && name_str != ".github")
            || should_skip_directory(name_str.as_ref())
        {
            continue;
        }

'''
replace("crates/daemon/src/scanner/mod.rs", skip_anchor, skip_replacement, count=2)

scanner_test_anchor = '''    #[tokio::test]
    async fn test_local_scan_empty_labels_returns_empty() {
'''
scanner_test = r'''    #[tokio::test]
    async fn test_local_scan_supports_standard_runs_on_yaml_forms() {
        let tmp = TempDir::new().unwrap();
        let repo = tmp.path().join("project");
        std_fs::create_dir_all(&repo).unwrap();
        init_git_remote(&repo, "git@github.com:acme/project.git");
        create_workflow(
            &repo,
            "array.yml",
            "jobs:\n  build:\n    runs-on: [self-hosted, linux, x64]\n",
        );
        create_workflow(
            &repo,
            "block.yaml",
            "jobs:\n  test:\n    runs-on:\n      - self-hosted\n      - linux\n",
        );

        let labels = vec!["self-hosted".to_string(), "linux".to_string()];
        let repos = scan_local(tmp.path(), &labels).await.unwrap();
        assert_eq!(repos.len(), 1);
        assert_eq!(repos[0].workflow_files.len(), 2);
        assert_eq!(repos[0].matched_labels, vec!["linux", "self-hosted"]);
    }

    #[tokio::test]
    async fn test_local_scan_skips_generated_dependency_trees() {
        let tmp = TempDir::new().unwrap();
        let generated = tmp.path().join("node_modules/dependency");
        std_fs::create_dir_all(&generated).unwrap();
        init_git_remote(&generated, "git@github.com:vendor/dependency.git");
        create_workflow(&generated, "ci.yml", "runs-on: self-hosted\n");

        let labels = vec!["self-hosted".to_string()];
        let repos = scan_local(tmp.path(), &labels).await.unwrap();
        assert!(repos.is_empty());
    }

'''
replace("crates/daemon/src/scanner/mod.rs", scanner_test_anchor, scanner_test + scanner_test_anchor)

# ---------------------------------------------------------------------------
# Preserve local-only repository selections in the creation wizard.
# ---------------------------------------------------------------------------
replace(
    "apps/desktop/src/components/NewRunnerWizard.tsx",
    '''  preselectedRepo?: string;
}''',
    '''  preselectedRepo?: string;
  preselectedRepoDetails?: RepoInfo;
}''',
)
replace(
    "apps/desktop/src/components/NewRunnerWizard.tsx",
    '''  onCreateBatch,
  preselectedRepo,
}: NewRunnerWizardProps) {''',
    '''  onCreateBatch,
  preselectedRepo,
  preselectedRepoDetails,
}: NewRunnerWizardProps) {''',
)
replace(
    "apps/desktop/src/components/NewRunnerWizard.tsx",
    '''    const found = repos.find((r) => r.full_name === preselectedRepo) ?? null;
''',
    '''    const provided =
      preselectedRepoDetails?.full_name === preselectedRepo ? preselectedRepoDetails : null;
    const found = repos.find((r) => r.full_name === preselectedRepo) ?? provided;
''',
)
replace(
    "apps/desktop/src/components/NewRunnerWizard.tsx",
    '''  }, [preselectedRepo, repos, reposLoading, resolvedPreselectFor, selectedRepo]);''',
    '''  }, [
    preselectedRepo,
    preselectedRepoDetails,
    repos,
    reposLoading,
    resolvedPreselectFor,
    selectedRepo,
  ]);''',
)

replace(
    "apps/desktop/src/pages/Repositories.tsx",
    '''  const [wizardRepo, setWizardRepo] = useState<string | null>(null);''',
    '''  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardRepo, setWizardRepo] = useState<ReturnType<typeof allReposAtType> | null>(null);''',
)
# Replace the temporary inferred helper type with the actual imported type and remove the helper name.
replace(
    "apps/desktop/src/pages/Repositories.tsx",
    '''import type { Preferences, DiscoveredRepo } from "../api/types";''',
    '''import type { Preferences, DiscoveredRepo, RepoInfo } from "../api/types";''',
)
replace(
    "apps/desktop/src/pages/Repositories.tsx",
    '''  const [wizardRepo, setWizardRepo] = useState<ReturnType<typeof allReposAtType> | null>(null);''',
    '''  const [wizardRepo, setWizardRepo] = useState<RepoInfo | null>(null);''',
)
replace(
    "apps/desktop/src/pages/Repositories.tsx",
    '''            <button className="btn btn-primary" onClick={() => setWizardRepo("")}>
              + Add Runner
            </button>''',
    '''            <button
              className="btn btn-primary"
              onClick={() => {
                setWizardRepo(null);
                setWizardOpen(true);
              }}
            >
              + Add Runner
            </button>''',
)
replace(
    "apps/desktop/src/pages/Repositories.tsx",
    '''                    onClick={() =>
                      auth.authenticated ? setWizardRepo(repo.full_name) : navigate("/settings")
                    }''',
    '''                    onClick={() => {
                      if (auth.authenticated) {
                        setWizardRepo(repo);
                        setWizardOpen(true);
                      } else {
                        navigate("/settings");
                      }
                    }}''',
)
replace(
    "apps/desktop/src/pages/Repositories.tsx",
    '''      {wizardRepo !== null && (
        <NewRunnerWizard
          onClose={() => setWizardRepo(null)}
          onCreate={createRunner}
          onCreateBatch={createBatch}
          preselectedRepo={wizardRepo || undefined}
        />
      )}''',
    '''      {wizardOpen && (
        <NewRunnerWizard
          onClose={() => {
            setWizardOpen(false);
            setWizardRepo(null);
          }}
          onCreate={createRunner}
          onCreateBatch={createBatch}
          preselectedRepo={wizardRepo?.full_name}
          preselectedRepoDetails={wizardRepo ?? undefined}
        />
      )}''',
)

wizard_test_anchor = '''  it("falls back to repository selection when a preselected repository is stale", async () => {'''
wizard_test = '''  it("keeps a locally discovered repository that is absent from the remote list", async () => {
    vi.mocked(api.listRepos).mockResolvedValue([]);
    const localRepo = makeRepo({
      id: 0,
      full_name: "local/project",
      owner: "local",
      name: "project",
      html_url: "",
    });
    const { props } = await renderWizard({
      preselectedRepo: localRepo.full_name,
      preselectedRepoDetails: localRepo,
    });

    const nameInput = await screen.findByLabelText("Name");
    expect((nameInput as HTMLInputElement).value).toMatch(/^project-runner-/);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Launch Runner" }));

    await waitFor(() => expect(props.onCreate).toHaveBeenCalledTimes(1));
    expect(props.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ repo_full_name: "local/project" }),
    );
  });

'''
replace(
    "apps/desktop/src/components/NewRunnerWizard.test.tsx",
    wizard_test_anchor,
    wizard_test + wizard_test_anchor,
)

# ---------------------------------------------------------------------------
# Serialize preference updates and route external links through Tauri shell.
# ---------------------------------------------------------------------------
replace(
    "apps/desktop/src/pages/Settings.tsx",
    '''import type { DeviceFlowResponse, Preferences } from "../api/types";''',
    '''import type { DeviceFlowResponse, Preferences } from "../api/types";
import { openExternal } from "../utils/openExternal";''',
)

settings_refs_anchor = '''  const [preferences, setPreferences] = useState<Preferences>({
    start_runners_on_launch: false,
    notify_status_changes: true,
    notify_job_completions: true,
    scan_labels: [],
    workspace_path: null,
    auto_scan: false,
  });

'''
settings_refs = settings_refs_anchor + '''  const desiredPreferencesRef = useRef(preferences);
  const persistedPreferencesRef = useRef(preferences);
  const preferenceVersionRef = useRef(0);
  const preferenceSavePromiseRef = useRef<Promise<void> | null>(null);
  const settingsMountedRef = useRef(true);

  useEffect(() => {
    settingsMountedRef.current = true;
    return () => {
      settingsMountedRef.current = false;
    };
  }, []);

'''
replace("apps/desktop/src/pages/Settings.tsx", settings_refs_anchor, settings_refs)

replace(
    "apps/desktop/src/pages/Settings.tsx",
    '''      .then((saved) => {
        setPreferences(saved);
        setWorkspaceInput(saved.workspace_path ?? "");
      })''',
    '''      .then((saved) => {
        desiredPreferencesRef.current = saved;
        persistedPreferencesRef.current = saved;
        setPreferences(saved);
        setWorkspaceInput(saved.workspace_path ?? "");
      })''',
)

content = read("apps/desktop/src/pages/Settings.tsx")
pattern = re.compile(
    r'''  async function persistPreferences\(updated: Preferences\) \{.*?  function updatePreferences\(updates: Partial<Preferences>\) \{\n    void persistPreferences\(\{ \.\.\.preferences, \.\.\.updates \}\);\n  \}\n''',
    re.S,
)
replacement = '''  const flushPreferences = useCallback((): Promise<void> => {
    const existing = preferenceSavePromiseRef.current;
    if (existing) return existing;

    const promise = (async () => {
      if (settingsMountedRef.current) {
        setPreferencesSaving(true);
        setPreferencesError(null);
      }
      try {
        while (
          settingsMountedRef.current &&
          desiredPreferencesRef.current !== persistedPreferencesRef.current
        ) {
          const version = preferenceVersionRef.current;
          const snapshot = desiredPreferencesRef.current;
          const saved = await api.updatePreferences(snapshot);
          persistedPreferencesRef.current = saved;

          if (version === preferenceVersionRef.current) {
            desiredPreferencesRef.current = saved;
            setPreferences(saved);
            setWorkspaceInput(saved.workspace_path ?? "");
          }
        }
      } catch (error) {
        preferenceVersionRef.current += 1;
        desiredPreferencesRef.current = persistedPreferencesRef.current;
        if (settingsMountedRef.current) {
          setPreferences(persistedPreferencesRef.current);
          setWorkspaceInput(persistedPreferencesRef.current.workspace_path ?? "");
          setPreferencesError(String(error));
        }
      } finally {
        if (settingsMountedRef.current) setPreferencesSaving(false);
      }
    })();

    preferenceSavePromiseRef.current = promise;
    void promise.finally(() => {
      if (preferenceSavePromiseRef.current === promise) {
        preferenceSavePromiseRef.current = null;
      }
    });
    return promise;
  }, []);

  function updatePreference(key: keyof Preferences, value: boolean) {
    updatePreferences({ [key]: value });
  }

  function updatePreferences(updates: Partial<Preferences>) {
    if (preferencesLoading) return;
    const updated = { ...desiredPreferencesRef.current, ...updates };
    desiredPreferencesRef.current = updated;
    preferenceVersionRef.current += 1;
    setPreferences(updated);
    void flushPreferences();
  }
'''
content, substitutions = pattern.subn(replacement, content, count=1)
if substitutions != 1:
    raise RuntimeError(f"Settings persistence block replacement count: {substitutions}")
write("apps/desktop/src/pages/Settings.tsx", content)

replace(
    "apps/desktop/src/pages/Settings.tsx",
    '''    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      style={{''',
    '''    <a
      href={href}
      onClick={(event) => {
        event.preventDefault();
        void openExternal(href);
      }}
      style={{''',
)

# Settings tests: default load, serialized writes, and packaged-app links.
replace(
    "apps/desktop/src/pages/Settings.test.tsx",
    '''  invoke: vi.fn(),
}));''',
    '''  invoke: vi.fn(),
  openExternal: vi.fn(),
}));''',
)
replace(
    "apps/desktop/src/pages/Settings.test.tsx",
    '''vi.mock("@tauri-apps/api/app", () => ({ getVersion: vi.fn().mockResolvedValue("0.9.1") }));''',
    '''vi.mock("@tauri-apps/api/app", () => ({ getVersion: vi.fn().mockResolvedValue("0.9.1") }));
vi.mock("../utils/openExternal", () => ({ openExternal: mocks.openExternal }));''',
)
replace(
    "apps/desktop/src/pages/Settings.test.tsx",
    '''    mocks.invoke.mockResolvedValue(false);
    mocks.updatePreferences.mockImplementation(async (value: Preferences) => value);''',
    '''    mocks.invoke.mockResolvedValue(false);
    mocks.getPreferences.mockResolvedValue(preferences);
    mocks.updatePreferences.mockImplementation(async (value: Preferences) => value);''',
)
settings_test_anchor = '''  it("keeps preference controls disabled until saved preferences are loaded", async () => {'''
settings_tests = '''  it("serializes rapid preference changes without losing either update", async () => {
    let resolveFirst: ((value: Preferences) => void) | undefined;
    mocks.updatePreferences
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
      )
      .mockImplementation(async (value: Preferences) => value);

    render(<Settings />);
    const restore = await screen.findByRole("switch", { name: "Restore runners on launch" });
    const completions = screen.getByRole("switch", { name: "Job completions" });
    await waitFor(() => expect(restore).not.toBeDisabled());

    act(() => {
      restore.click();
      completions.click();
    });
    expect(mocks.updatePreferences).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst?.({ ...preferences, start_runners_on_launch: true });
      await Promise.resolve();
    });

    await waitFor(() => expect(mocks.updatePreferences).toHaveBeenCalledTimes(2));
    expect(mocks.updatePreferences).toHaveBeenLastCalledWith(
      expect.objectContaining({
        start_runners_on_launch: true,
        notify_job_completions: false,
      }),
    );
  });

  it("opens About links through the Tauri shell bridge", async () => {
    render(<Settings />);
    const repository = await screen.findByRole("link", { name: "Repository" });
    fireEvent.click(repository);
    expect(mocks.openExternal).toHaveBeenCalledWith("https://github.com/lgg/homerun");
  });

'''
replace("apps/desktop/src/pages/Settings.test.tsx", settings_test_anchor, settings_tests + settings_test_anchor)

# ---------------------------------------------------------------------------
# Non-overlapping preferences polling and accurate standalone-window status.
# ---------------------------------------------------------------------------
old_layout_poll = '''  useEffect(() => {
    api
      .getPreferences()
      .then(setNotifPrefs)
      .catch(() => {});
    const interval = setInterval(() => {
      api
        .getPreferences()
        .then(setNotifPrefs)
        .catch(() => {});
    }, 2000);
    return () => clearInterval(interval);
  }, []);
'''
new_layout_poll = '''  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const pollPreferences = async () => {
      try {
        const preferences = await api.getPreferences();
        if (!cancelled) setNotifPrefs(preferences);
      } catch {
        // Keep the last known notification preferences while the daemon transitions.
      } finally {
        if (!cancelled) {
          timer = window.setTimeout(() => void pollPreferences(), 2000);
        }
      }
    };

    void pollPreferences();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);
'''
replace("apps/desktop/src/components/Layout.tsx", old_layout_poll, new_layout_poll)

replace(
    "apps/desktop/src/pages/MiniView.tsx",
    '''  const { runners, error } = useRunners();''',
    '''  const { runners, loading, error } = useRunners();''',
)
replace(
    "apps/desktop/src/pages/MiniView.tsx",
    '''  const daemonOk = error === null;''',
    '''  const daemonOk = !loading && error === null;''',
)
replace(
    "apps/desktop/src/pages/TrayPanel.tsx",
    '''  const { runners, error } = useRunners();
  const daemonOk = error === null;''',
    '''  const { runners, loading, error } = useRunners();
  const daemonOk = !loading && error === null;''',
)

# Daemon action copy and timeout lifecycle.
replace(
    "apps/desktop/src/pages/Daemon.tsx",
    '''      const label = action.charAt(0).toUpperCase() + action.slice(1);
      setActionResult({ type: "success", message: `Daemon ${label.toLowerCase()}ed successfully` });''',
    '''      const completedAction = {
        start: "started",
        stop: "stopped",
        restart: "restarted",
      }[action];
      setActionResult({ type: "success", message: `Daemon ${completedAction} successfully` });''',
)
replace(
    "apps/desktop/src/pages/Daemon.tsx",
    '''  const daemon = metrics?.daemon;

  useEffect(() => {''',
    '''  const daemon = metrics?.daemon;

  useEffect(() => {
    return () => {
      if (resultTimerRef.current) clearTimeout(resultTimerRef.current);
    };
  }, []);

  useEffect(() => {''',
)

# ---------------------------------------------------------------------------
# Locale-safe Windows autostart removal.
# ---------------------------------------------------------------------------
service_content = read("crates/daemon/src/platform/service.rs")
old_windows_uninstall = '''    pub fn uninstall_daemon_service() -> Result<()> {
        let output = std::process::Command::new("reg")
            .args([
                "delete",
                &format!("HKCU\\\\{}", REG_KEY),
                "/v",
                REG_VALUE,
                "/f",
            ])
            .output()
            .context("Failed to run reg delete")?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            // Not an error if the value doesn't exist
            if !stderr.contains("unable to find") {
                anyhow::bail!("reg delete failed: {}", stderr.trim());
            }
        }

        tracing::info!("Daemon removed from Registry Run key");
        Ok(())
    }
'''
new_windows_uninstall = '''    pub fn uninstall_daemon_service() -> Result<()> {
        // Query first instead of parsing localized `reg delete` error text.
        if !is_daemon_installed() {
            tracing::info!("Daemon Registry Run entry is not installed");
            return Ok(());
        }

        let output = std::process::Command::new("reg")
            .args([
                "delete",
                &format!("HKCU\\\\{}", REG_KEY),
                "/v",
                REG_VALUE,
                "/f",
            ])
            .output()
            .context("Failed to run reg delete")?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            anyhow::bail!("reg delete failed: {}", stderr.trim());
        }

        tracing::info!("Daemon removed from Registry Run key");
        Ok(())
    }
'''
if old_windows_uninstall not in service_content:
    raise RuntimeError("Windows uninstall block was not found")
write(
    "crates/daemon/src/platform/service.rs",
    service_content.replace(old_windows_uninstall, new_windows_uninstall, 1),
)

# ---------------------------------------------------------------------------
# Always restore the terminal if the TUI exits through an error path.
# ---------------------------------------------------------------------------
replace(
    "crates/tui/src/main.rs",
    '''use crossterm::{
    event::KeyEventKind,
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};''',
    '''use crossterm::{
    cursor::Show,
    event::KeyEventKind,
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};''',
)
replace(
    "crates/tui/src/main.rs",
    '''use homerun::ui;

#[derive(Parser)]''',
    '''use homerun::ui;

struct TerminalRestoreGuard;

impl Drop for TerminalRestoreGuard {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let mut stdout = io::stdout();
        let _ = execute!(stdout, LeaveAlternateScreen, Show);
    }
}

#[derive(Parser)]''',
)
replace(
    "crates/tui/src/main.rs",
    '''    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);''',
    '''    execute!(stdout, EnterAlternateScreen)?;
    let terminal_restore = TerminalRestoreGuard;
    let backend = CrosstermBackend::new(stdout);''',
)
replace(
    "crates/tui/src/main.rs",
    '''    terminal.show_cursor()?;

    // Force exit — spawn_blocking tasks can keep the tokio runtime alive
    std::process::exit(0);''',
    '''    terminal.show_cursor()?;
    std::mem::forget(terminal_restore);

    // Force exit — spawn_blocking tasks can keep the tokio runtime alive
    std::process::exit(0);''',
)

# Remove the one-shot patch machinery in the same product commit.
(ROOT / ".github/audit/apply_declared_features.py").unlink()
(ROOT / ".github/workflows/declared-features-audit-apply.yml").unlink()
