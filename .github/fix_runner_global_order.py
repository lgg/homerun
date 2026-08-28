from pathlib import Path
import re

path = Path("apps/desktop/src/components/RunnerTable.tsx")
data = path.read_text()

marker = "interface RunnerTableProps {\n  runners: RunnerInfo[];"
if marker not in data:
    raise SystemExit("props marker missing")
data = data.replace(
    marker,
    """type RunnerListEntry =
  | { kind: \"group\"; groupKey: string; groupRunners: RunnerInfo[] }
  | { kind: \"solo\"; runner: RunnerInfo };

interface RunnerTableProps {
  runners: RunnerInfo[];""",
    1,
)

pattern = re.compile(
    r"  const \{ groups, soloRunners \} = useMemo\(\(\) => \{.*?\n  \}, \[runners, sortByActivity\]\);",
    re.S,
)
replacement = """  const displayEntries = useMemo<RunnerListEntry[]>(() => {
    const byName = (a: RunnerInfo, b: RunnerInfo) =>
      a.config.name.localeCompare(b.config.name, undefined, { numeric: true });
    const orderRunners = (items: RunnerInfo[]) =>
      items.sort(sortByActivity ? compareRunnersByActivity : byName);

    // Group by name prefix + repo (merges runners from separate batch creates)
    const mergedMap = new Map<string, RunnerInfo[]>();
    const solo: RunnerInfo[] = [];
    for (const runner of runners) {
      if (runner.config.group_id) {
        const prefix = runner.config.name.replace(/-\\d+$/, \"\");
        const repo = `${runner.config.repo_owner}/${runner.config.repo_name}`;
        const key = `${prefix}::${repo}`;
        const existing = mergedMap.get(key) ?? [];
        existing.push(runner);
        mergedMap.set(key, existing);
      } else {
        solo.push(runner);
      }
    }

    for (const group of mergedMap.values()) orderRunners(group);
    orderRunners(solo);

    const entries: RunnerListEntry[] = [
      ...Array.from(mergedMap.entries()).map(([groupKey, groupRunners]) => ({
        kind: \"group\" as const,
        groupKey,
        groupRunners,
      })),
      ...solo.map((runner) => ({ kind: \"solo\" as const, runner })),
    ];

    if (sortByActivity) {
      entries.sort((a, b) => {
        const aRunner = a.kind === \"group\" ? a.groupRunners[0] : a.runner;
        const bRunner = b.kind === \"group\" ? b.groupRunners[0] : b.runner;
        const byActivity = compareRunnersByActivity(aRunner, bRunner);
        if (byActivity !== 0) return byActivity;
        const aKey = a.kind === \"group\" ? a.groupKey : a.runner.config.name;
        const bKey = b.kind === \"group\" ? b.groupKey : b.runner.config.name;
        return aKey.localeCompare(bKey, undefined, { numeric: true });
      });
    }

    return entries;
  }, [runners, sortByActivity]);"""
data, count = pattern.subn(lambda _: replacement, data, count=1)
if count != 1:
    raise SystemExit(f"memo replacement count={count}")

start_marker = "      {/* Groups */}"
start = data.index(start_marker)
end_marker = """      {soloRunners.map((runner) => (
        <RunnerRow
          key={runner.config.id}
          runner={runner}
          cpuValue={metrics?.get(runner.config.id)}
          loading={pendingActions?.has(runner.config.id)}
          readOnly={readOnly}
          onStart={onStart}
          onStop={onStop}
          onRestart={onRestart}
          onDelete={onDelete}
          onClick={() => navigate(`/runners/${runner.config.id}`)}
        />
      ))}"""
end_start = data.index(end_marker, start)
end = end_start + len(end_marker)
render = """      {displayEntries.map((entry) => {
        if (entry.kind === \"solo\") {
          const runner = entry.runner;
          return (
            <RunnerRow
              key={runner.config.id}
              runner={runner}
              cpuValue={metrics?.get(runner.config.id)}
              loading={pendingActions?.has(runner.config.id)}
              readOnly={readOnly}
              onStart={onStart}
              onStop={onStop}
              onRestart={onRestart}
              onDelete={onDelete}
              onClick={() => navigate(`/runners/${runner.config.id}`)}
            />
          );
        }

        const { groupKey, groupRunners } = entry;
        const isExpanded = effectiveExpanded.has(groupKey);
        const groupIds = [
          ...new Set(groupRunners.map((r) => r.config.group_id).filter(Boolean)),
        ] as string[];
        const firstGroupId = groupIds[0] ?? groupKey;
        const isLoading =
          pendingActions?.has(groupKey) || groupIds.some((gid) => pendingActions?.has(gid));
        return (
          <Fragment key={`group-${groupKey}`}>
            <RunnerGroupRow
              groupId={firstGroupId}
              groupIds={groupIds}
              runners={groupRunners}
              expanded={isExpanded}
              onToggle={() => toggleGroup(groupKey)}
              onStartGroup={onStartGroup}
              onStopGroup={onStopGroup}
              onRestartGroup={onRestartGroup}
              onDeleteGroup={onDeleteGroup}
              onScaleGroup={onScaleGroup}
              loading={isLoading}
              readOnly={readOnly}
            />
            {isExpanded &&
              groupRunners.map((runner) => {
                const rowLoading =
                  pendingActions?.has(runner.config.id) ||
                  groupIds.some((gid) => pendingActions?.has(gid));
                return (
                  <RunnerRow
                    key={runner.config.id}
                    runner={runner}
                    cpuValue={metrics?.get(runner.config.id)}
                    loading={rowLoading}
                    readOnly={readOnly}
                    indented
                    inGroup
                    onStart={onStart}
                    onStop={onStop}
                    onRestart={onRestart}
                    onDelete={onDelete}
                    onClick={() => navigate(`/runners/${runner.config.id}`)}
                  />
                );
              })}
          </Fragment>
        );
      })}"""
data = data[:start] + render + data[end:]
path.write_text(data)
