from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path}, found {count}: {old[:100]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "apps/desktop/src/components/NewRunnerWizard.tsx",
    """    if (resolvedPreselectFor === preselectedRepo || reposLoading) return;

    const found = repos.find((r) => r.full_name === preselectedRepo) ?? null;
    setSelectedRepo(found);
    setResolvedPreselectFor(preselectedRepo);

    if (found) {
      setName(generateName(found.name));
      setStep(1);
    } else {
      // A stale/deleted repository must not leave the wizard stuck on an
      // unresolvable \"Loading repository...\" screen.
      setStep(0);
    }
  }, [preselectedRepo, repos, reposLoading, resolvedPreselectFor]);""",
    """    if (reposLoading) return;

    const found = repos.find((r) => r.full_name === preselectedRepo) ?? null;
    if (resolvedPreselectFor === preselectedRepo) {
      if (selectedRepo?.full_name === preselectedRepo) {
        if (found) return;
        // The repository disappeared after it had been selected.
        setSelectedRepo(null);
        setName(\"\");
        setStep(0);
        return;
      }
      // Do not override a repository the user selected manually after a stale
      // preselection, but retry an unresolved preselection when polling finds it.
      if (selectedRepo || !found) return;
    }

    setSelectedRepo(found);
    setResolvedPreselectFor(preselectedRepo);

    if (found) {
      setName(generateName(found.name));
      setStep(1);
    } else {
      // A stale/deleted repository must not leave the wizard stuck on an
      // unresolvable \"Loading repository...\" screen.
      setName(\"\");
      setStep(0);
    }
  }, [preselectedRepo, repos, reposLoading, resolvedPreselectFor, selectedRepo]);""",
)

replace_once(
    "apps/desktop/src/components/NewRunnerWizard.test.tsx",
    """  it(\"falls back to repository selection when a preselected repository is stale\", async () => {
    await renderWizard({ preselectedRepo: \"org/removed\" });

    expect(await screen.findByPlaceholderText(\"Search repositories...\")).toBeInTheDocument();
    expect(screen.queryByText(\"Loading repository...\")).not.toBeInTheDocument();
  });

  it(\"Cancel button calls onClose\", async () => {""",
    """  it(\"falls back to repository selection when a preselected repository is stale\", async () => {
    await renderWizard({ preselectedRepo: \"org/removed\" });

    expect(await screen.findByPlaceholderText(\"Search repositories...\")).toBeInTheDocument();
    expect(screen.queryByText(\"Loading repository...\")).not.toBeInTheDocument();
  });

  it(\"resolves a stale preselection when repository polling later finds it\", async () => {
    vi.useFakeTimers();
    let unmount: (() => void) | undefined;
    try {
      vi.mocked(api.listRepos)
        .mockResolvedValueOnce(mockRepos.filter((repo) => repo.full_name !== \"org/frontend\"))
        .mockResolvedValue(mockRepos);

      const rendered = await renderWizard({ preselectedRepo: \"org/frontend\" });
      unmount = rendered.unmount;
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.getByPlaceholderText(\"Search repositories...\")).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });

      const nameInput = screen.getByLabelText(\"Name\") as HTMLInputElement;
      expect(nameInput.value).toMatch(/^frontend-runner-/);
    } finally {
      unmount?.();
      vi.useRealTimers();
    }
  });

  it(\"Cancel button calls onClose\", async () => {""",
)
