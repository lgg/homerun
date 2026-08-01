export function isLaunchableRepository(fullName: string): boolean {
  const parts = fullName.split("/");
  return (
    parts.length === 2 &&
    parts.every((part) => part.length > 0 && part === part.trim())
  );
}
