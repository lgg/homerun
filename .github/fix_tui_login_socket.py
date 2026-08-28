from pathlib import Path

path = Path("crates/tui/src/main.rs")
data = path.read_text()
old = '''    tokio::spawn(async move {
        let client = DaemonClient::default_socket();
        loop {
            match client.poll_device_flow(&device_code, interval).await {
'''
new = '''    tokio::spawn(async move {
        let client = match DaemonClient::default_socket() {
            Ok(client) => client,
            Err(error) => {
                let _ = event_tx.send(AppEvent::LoginCompleted(Err(error.to_string())));
                return;
            }
        };
        loop {
            match client.poll_device_flow(&device_code, interval).await {
'''
if data.count(old) != 1:
    raise SystemExit(f"expected exactly one login polling socket constructor, found {data.count(old)}")
path.write_text(data.replace(old, new, 1))
