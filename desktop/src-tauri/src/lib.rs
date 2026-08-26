use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

const SESSION_HOST: &str = "127.0.0.1";
const SESSION_PORT: u16 = 8766;

struct SidecarState {
    child: Mutex<Option<tauri_plugin_shell::process::CommandChild>>,
}

fn session_ready() -> bool {
    let Ok(mut stream) = TcpStream::connect((SESSION_HOST, SESSION_PORT)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(400)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(400)));
    let request = format!(
        "GET /api/status HTTP/1.1\r\nHost: {SESSION_HOST}:{SESSION_PORT}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0_u8; 96];
    let n = stream.read(&mut buf).unwrap_or(0);
    let head = String::from_utf8_lossy(&buf[..n]);
    head.contains(" 200 ")
}

fn wait_for_session(timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if session_ready() {
            return true;
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}

fn spawn_sidecar(app: &AppHandle) -> Result<(), String> {
    if session_ready() {
        eprintln!(
            "reusing existing Heuriva Session on {SESSION_HOST}:{SESSION_PORT}"
        );
        return Ok(());
    }

    let sidecar = app
        .shell()
        .sidecar("heuriva-sidecar")
        .map_err(|err| err.to_string())?;
    let (mut rx, child) = sidecar
        .args([
            "serve",
            "--host",
            SESSION_HOST,
            "--port",
            &SESSION_PORT.to_string(),
        ])
        .spawn()
        .map_err(|err| err.to_string())?;

    if let Some(state) = app.try_state::<SidecarState>() {
        *state.child.lock().expect("sidecar mutex") = Some(child);
    }

    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    eprintln!("[heuriva-sidecar] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(payload) => {
                    eprintln!("[heuriva-sidecar] exited: {payload:?}");
                    break;
                }
                _ => {}
            }
        }
    });
    Ok(())
}

fn open_session(app: &AppHandle) {
    let url = format!("http://{SESSION_HOST}:{SESSION_PORT}/");
    if let Some(window) = app.get_webview_window("main") {
        let script = format!("window.location.replace({url:?})");
        if let Err(err) = window.eval(&script) {
            eprintln!("failed to navigate Session window: {err}");
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState {
            child: Mutex::new(None),
        })
        .setup(|app| {
            if let Err(err) = spawn_sidecar(app.handle()) {
                eprintln!("failed to spawn Heuriva sidecar: {err}");
                return Err(Box::new(std::io::Error::other(err)) as Box<dyn std::error::Error>);
            }
            let handle = app.handle().clone();
            thread::spawn(move || {
                if wait_for_session(Duration::from_secs(60)) {
                    open_session(&handle);
                } else {
                    eprintln!("Heuriva Session sidecar did not become ready in time");
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Heuriva")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<SidecarState>() {
                    if let Ok(mut guard) = state.child.lock() {
                        if let Some(child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        });
}
