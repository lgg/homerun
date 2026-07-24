from pathlib import Path

path = Path('.github/scripts/apply-post-merge-transaction-audit.py')
text = path.read_text(encoding='utf-8')

old_first = '''replace_once(
    RUNNER,
    """        if let Err(error) = self.save_to_disk().await {
            let mut desired_running = self.desired_running.write().await;""",
    """        if let Err(error) = self.save_to_disk_locked().await {
            let mut desired_running = self.desired_running.write().await;""",
)
'''
new_first = '''replace_count(
    RUNNER,
    """        if let Err(error) = self.save_to_disk().await {
            let mut desired_running = self.desired_running.write().await;""",
    """        if let Err(error) = self.save_to_disk_locked().await {
            let mut desired_running = self.desired_running.write().await;""",
    2,
)
'''
if text.count(old_first) != 2:
    raise RuntimeError(f'expected two duplicate persistence replacements, found {text.count(old_first)}')
text = text.replace(old_first, new_first, 1)
text = text.replace(old_first, '', 1)
path.write_text(text, encoding='utf-8')
