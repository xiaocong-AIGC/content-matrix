use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// All auto-started sidecars (backend + orchestrator + auto-provision), so we
/// can kill them when the app quits instead of orphaning them.
struct Sidecars(Mutex<Vec<CommandChild>>);

/// Windows Job Object with KILL_ON_JOB_CLOSE. Every sidecar pid is assigned to
/// it; when app.exe dies for ANY reason (clean exit, crash, taskkill), the OS
/// closes this handle → the job closes → all sidecars are terminated. This is
/// what guarantees "关闭桌面App = 所有服务都关闭" even on a crash. Kept in app
/// state so the handle lives exactly as long as app.exe.
#[cfg(windows)]
mod jobkill {
    use std::mem::{size_of, zeroed};
    use std::ptr::null_mut;
    use winapi::um::handleapi::CloseHandle;
    use winapi::um::jobapi2::{
        AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject,
    };
    use winapi::um::processthreadsapi::OpenProcess;
    use winapi::um::winnt::{
        JobObjectExtendedLimitInformation, HANDLE, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
    };

    pub struct Job(pub HANDLE);
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    pub fn create() -> Option<Job> {
        unsafe {
            let job = CreateJobObjectW(null_mut(), null_mut());
            if job.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &mut info as *mut _ as *mut _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            Some(Job(job))
        }
    }

    pub fn assign(job: &Job, pid: u32) {
        unsafe {
            let h = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid);
            if !h.is_null() {
                AssignProcessToJobObject(job.0, h);
                CloseHandle(h);
            }
        }
    }
}

#[cfg(windows)]
struct JobState(Mutex<Option<jobkill::Job>>);

/// Kill any leftover sidecars from a PREVIOUS crashed run BEFORE we spawn fresh
/// ones — so a launch always starts clean + the new processes are the ones bound
/// to the job (belt-and-suspenders with the job's kill-on-close). Never touches
/// cloudflared (the tunnel is an independent always-on service by design).
#[cfg(windows)]
fn cleanup_orphans() {
    use std::os::windows::process::CommandExt;
    for img in ["orchestrator.exe", "autoprovision.exe", "backend.exe"] {
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/IM", img])
            .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
            .output();
    }
    std::thread::sleep(Duration::from_millis(700));
}
#[cfg(not(windows))]
fn cleanup_orphans() {}

/// Pre-start the shared adb server from app.exe BEFORE the job/sidecars exist, so
/// it is NOT a descendant of anything in the kill-on-close job. Otherwise the first
/// `adb` our orchestrator runs would auto-start the server INSIDE the job → closing
/// our App would kill the shared adb server and break the user's escrcpy / other adb
/// tools. Started here (child of app.exe, not in the job) it survives our App close.
#[cfg(windows)]
fn ensure_adb_server(app: &AppHandle) {
    use std::os::windows::process::CommandExt;
    use std::process::Stdio;
    if let Some(dir) = adb_dir(app) {
        let adb = dir.join("adb.exe");
        // Reuses an already-running server (e.g. escrcpy's) if present; else starts one.
        // CRITICAL: null stdio + spawn/wait, NOT `.output()`. When no server is running
        // yet (a fresh migration PC), `adb start-server` forks a daemon that INHERITS
        // the parent's stdout/stderr handles; `.output()` blocks until those pipes hit
        // EOF, which never happens while the daemon lives → setup() hangs forever and
        // the App boots with NO backend/tunnel/orchestrator (only the window shows).
        // Null stdio detaches the pipes so wait() returns as soon as the adb CLI exits.
        if let Ok(mut child) = std::process::Command::new(adb)
            .arg("start-server")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
            .spawn()
        {
            let _ = child.wait();
        }
    }
}
#[cfg(not(windows))]
fn ensure_adb_server(_app: &AppHandle) {}

/// App-managed public tunnel (cloudflared) — starts on launch, dies with the App
/// (in the job). Portable: reads credentials + tunnel id from `<data>/tunnel/`
/// (ships in the migration package), writes a config.yml with absolute paths
/// resolved on THIS machine, then runs it. A fresh customer install has no
/// `tunnel/` folder → skipped (no owner tunnel leaked to customers).
#[cfg(windows)]
fn ensure_tunnel(app: &AppHandle) {
    use std::os::windows::process::CommandExt;
    let dir = PathBuf::from(backend_home()).join("tunnel");
    let creds = dir.join("credentials.json");
    let id_file = dir.join("tunnel_id.txt");
    if !creds.exists() || !id_file.exists() {
        return; // no tunnel shipped with this install
    }
    let tunnel_id = std::fs::read_to_string(&id_file).unwrap_or_default();
    let tunnel_id = tunnel_id.trim();
    if tunnel_id.is_empty() {
        return;
    }
    // cloudflared: prefer the copy shipped next to the tunnel data, then D:\devtools.
    let cf = {
        let a = dir.join("cloudflared.exe");
        if a.exists() {
            a
        } else {
            PathBuf::from(r"D:\devtools\cloudflared.exe")
        }
    };
    if !cf.exists() {
        return;
    }
    // Write config.yml with THIS machine's absolute paths (portable across PCs).
    let cfg_path = dir.join("config.yml");
    let cfg = format!(
        "tunnel: {id}\ncredentials-file: {creds}\ningress:\n  - hostname: matrix.ouyipu.xyz\n    service: http://127.0.0.1:8010\n  - service: http_status:404\n",
        id = tunnel_id,
        creds = creds.to_string_lossy(),
    );
    let _ = std::fs::write(&cfg_path, cfg);
    // Kill a stale cloudflared (e.g. a leftover one) so we don't double-connect.
    let _ = std::process::Command::new("taskkill")
        .args(["/F", "/IM", "cloudflared.exe"])
        .creation_flags(0x0800_0000)
        .output();
    match std::process::Command::new(&cf)
        .current_dir(&dir)
        .args(["--config", &cfg_path.to_string_lossy(), "tunnel", "run"])
        .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
        .spawn()
    {
        Ok(child) => {
            assign_to_job(app, child.id()); // dies with the App
            log::info!("tunnel (cloudflared) started pid {}", child.id());
        }
        Err(e) => log::error!("failed to start tunnel: {e}"),
    }
}
#[cfg(not(windows))]
fn ensure_tunnel(_app: &AppHandle) {}

/// Bind a freshly-spawned sidecar pid to the kill-on-close job.
#[cfg(windows)]
fn assign_to_job(app: &AppHandle, pid: u32) {
    if let Some(js) = app.try_state::<JobState>() {
        if let Ok(guard) = js.0.lock() {
            if let Some(job) = guard.as_ref() {
                jobkill::assign(job, pid);
            }
        }
    }
}
#[cfg(not(windows))]
fn assign_to_job(_app: &AppHandle, _pid: u32) {}

/// Data dir the frozen backend runs in (reads .env, writes data/agent.db).
/// Resolution order (backward-compatible with the dev machine, portable for
/// distribution): MATRIX_HOME env → the dev repo path IF it exists (owner's
/// machine keeps its data) → %APPDATA%\发布室 (fresh install on any PC,
/// auto-created — no hardcoded D:\ dependency).
fn backend_home() -> String {
    if let Ok(h) = std::env::var("MATRIX_HOME") {
        if !h.trim().is_empty() {
            return h;
        }
    }
    let dev = r"D:\抖音自动发布agent\backend";
    if Path::new(dev).is_dir() {
        return dev.to_string();
    }
    // 迁移包便携模式：app.exe 位于「…\程序\」，真实数据在同级的「…\数据」。按 exe 自身
    // 位置定位，这样【直接双击 app.exe 也能用上真实数据】——不再依赖 bat 设的 MATRIX_HOME
    // （用户手动开 App 时没有它，就会退回到空的 %APPDATA%\发布室、且没有 .env → CORS 拦截
    // → “连不上后端”）。兼容 exe 直接放在包根目录（数据为其同级）两种布局。
    if let Ok(exe) = std::env::current_exe() {
        if let Some(d) = exe.parent() {
            for cand in [d.join("..").join("数据"), d.join("数据")] {
                if cand.join(".env").exists() || cand.join("data").is_dir() {
                    let p = std::fs::canonicalize(&cand)
                        .map(|a| a.to_string_lossy().into_owned())
                        .unwrap_or_else(|_| cand.to_string_lossy().into_owned());
                    // 去掉 Windows canonicalize 的 \\?\ 前缀，避免下游拼路径出问题。
                    return p.strip_prefix(r"\\?\").map(str::to_string).unwrap_or(p);
                }
            }
        }
    }
    let appdata = std::env::var("APPDATA").unwrap_or_else(|_| ".".to_string());
    let home = PathBuf::from(appdata).join("发布室");
    let _ = std::fs::create_dir_all(home.join("data"));
    let _ = std::fs::create_dir_all(home.join("storage"));
    home.to_string_lossy().into_owned()
}

/// Directory containing adb.exe — orchestrator/auto-provision call `adb` by name.
/// Prefer the bundled copy (resources/platform-tools), then ADB_DIR, then this
/// machine's SDK path.
fn adb_dir(app: &AppHandle) -> Option<PathBuf> {
    if let Ok(rd) = app.path().resource_dir() {
        let p = rd.join("platform-tools");
        if p.join("adb.exe").exists() {
            return Some(p);
        }
    }
    if let Ok(d) = std::env::var("ADB_DIR") {
        if Path::new(&d).join("adb.exe").exists() {
            return Some(PathBuf::from(d));
        }
    }
    let def = PathBuf::from(r"D:\Android\Sdk\platform-tools");
    if def.join("adb.exe").exists() {
        return Some(def);
    }
    None
}

/// The agent APK auto-provision installs onto fresh phones.
fn apk_path(app: &AppHandle) -> Option<String> {
    if let Ok(rd) = app.path().resource_dir() {
        let p = rd.join("platform-tools").join("app-debug.apk");
        if p.exists() {
            return Some(p.to_string_lossy().into_owned());
        }
    }
    std::env::var("MATRIX_APK").ok()
}

fn backend_already_up() -> bool {
    TcpStream::connect_timeout(&"127.0.0.1:8010".parse().unwrap(), Duration::from_millis(400))
        .is_ok()
}

/// PATH with the adb dir prepended, so the spawned tools resolve `adb`.
fn path_with_adb(app: &AppHandle) -> String {
    let cur = std::env::var("PATH").unwrap_or_default();
    match adb_dir(app) {
        Some(d) => format!("{};{}", d.to_string_lossy(), cur),
        None => cur,
    }
}

/// Spawn one sidecar, drain its output to the log, and remember the child.
fn spawn_sidecar(app: &AppHandle, name: &str, args: Vec<String>, envs: Vec<(String, String)>) {
    let cmd = match app.shell().sidecar(name) {
        Ok(c) => c,
        Err(e) => {
            log::error!("cannot resolve sidecar {name}: {e}");
            return;
        }
    };
    let mut cmd = cmd.args(args);
    for (k, v) in envs {
        cmd = cmd.env(k, v);
    }
    match cmd.spawn() {
        Ok((mut rx, child)) => {
            log::info!("sidecar {name} started (pid {})", child.pid());
            assign_to_job(app, child.pid());
            app.state::<Sidecars>().0.lock().unwrap().push(child);
            let tag = name.to_string();
            tauri::async_runtime::spawn(async move {
                while let Some(ev) = rx.recv().await {
                    match ev {
                        CommandEvent::Stdout(b) | CommandEvent::Stderr(b) => {
                            log::info!("[{tag}] {}", String::from_utf8_lossy(&b).trim_end());
                        }
                        CommandEvent::Terminated(p) => {
                            log::warn!("sidecar {tag} exited: {:?}", p);
                            break;
                        }
                        _ => {}
                    }
                }
            });
        }
        Err(e) => log::error!("failed to spawn sidecar {name}: {e}"),
    }
}

/// Backend needs its cwd at the data dir; spawn it separately with current_dir.
fn start_backend(app: &AppHandle) {
    if backend_already_up() {
        log::info!("backend already serving :8010 — not spawning sidecar");
        return;
    }
    let cmd = match app.shell().sidecar("backend") {
        Ok(c) => c
            .current_dir(backend_home())
            .env("MATRIX_HOST", "127.0.0.1")
            .env("MATRIX_PORT", "8010"),
        Err(e) => {
            log::error!("cannot resolve backend sidecar: {e}");
            return;
        }
    };
    match cmd.spawn() {
        Ok((mut rx, child)) => {
            log::info!("backend sidecar started (pid {})", child.pid());
            assign_to_job(app, child.pid());
            app.state::<Sidecars>().0.lock().unwrap().push(child);
            tauri::async_runtime::spawn(async move {
                while let Some(ev) = rx.recv().await {
                    if let CommandEvent::Stdout(b) | CommandEvent::Stderr(b) = ev {
                        log::info!("[backend] {}", String::from_utf8_lossy(&b).trim_end());
                    }
                }
            });
        }
        Err(e) => log::error!("failed to spawn backend sidecar: {e}"),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Sidecars(Mutex::new(Vec::new())))
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            // Fresh start: reap any orphaned sidecars from a previous crashed run,
            // then create the kill-on-close job the new ones will be bound to.
            cleanup_orphans();
            #[cfg(windows)]
            app.manage(JobState(Mutex::new(jobkill::create())));
            let handle = app.handle();
            // Start the SHARED adb server from app.exe (outside the job) so closing
            // our App never kills it → the user's escrcpy / other adb tools keep working.
            ensure_adb_server(handle);
            // App-managed public tunnel — starts here, dies with the App (in the job).
            ensure_tunnel(handle);
            start_backend(handle);
            // orchestrator + auto-provision (backend handled above with its cwd).
            let path = path_with_adb(handle);
            // Cloud-phone adb endpoints to auto-(re)connect — network adb drops on
            // every adb-server restart. Override with MATRIX_ADB_ENDPOINTS.
            let endpoints = std::env::var("MATRIX_ADB_ENDPOINTS")
                .unwrap_or_else(|_| "192.168.1.200:6001-6037".to_string());
            spawn_sidecar(
                handle,
                "orchestrator",
                vec![],
                vec![
                    ("PATH".into(), path.clone()),
                    ("MATRIX_ADB_ENDPOINTS".into(), endpoints),
                ],
            );
            let mut ap_args = vec![
                "--server".to_string(),
                "http://127.0.0.1:8010/api/v1".to_string(),
            ];
            if let Some(apk) = apk_path(handle) {
                ap_args.push("--apk".into());
                ap_args.push(apk);
            }
            spawn_sidecar(handle, "autoprovision", ap_args, vec![("PATH".into(), path)]);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Ok(mut guard) = app.state::<Sidecars>().0.lock() {
                    for child in guard.drain(..) {
                        let _ = child.kill();
                    }
                }
            }
        });
}

// build verified on relocated D: toolchain (rustup/cargo/mingw under D:\devtools)
