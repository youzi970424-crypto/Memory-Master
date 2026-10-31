#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod launcher_position;

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, Size, WebviewUrl, WebviewWindowBuilder};
use tauri::window::Color;
use tauri_plugin_global_shortcut::{Builder as GlobalShortcutBuilder, ShortcutState};

const MAIN_LABEL: &str = "main";
const QUICK_ADD_LABEL: &str = "quick-add";
const API_PORT: &str = "8765";
const QUICK_ADD_WIDTH: f64 = 360.0;
const QUICK_ADD_HEIGHT: f64 = 489.0;

const LAUNCHER_LABEL: &str = "quick-launcher";

#[derive(Clone, serde::Serialize, serde::Deserialize)]
struct QuickPreferences {
    visible: bool,
    hide_notice_seen: bool,
    position: Option<(i32, i32)>,
}

struct QuickState(Mutex<QuickPreferences>);
struct LauncherDrag(Mutex<Option<(f64, f64)>>);

fn preferences_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir.join("quick-entry.json"))
}

fn save_preferences(app: &AppHandle, prefs: &QuickPreferences) -> Result<(), String> {
    let path = preferences_path(app)?;
    let temporary = path.with_extension("tmp");
    std::fs::write(&temporary, serde_json::to_vec(prefs).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    std::fs::rename(temporary, path).map_err(|e| e.to_string())
}

#[tauri::command]
fn quick_preferences(app: AppHandle) -> QuickPreferences {
    app.state::<QuickState>().0.lock().unwrap().clone()
}

#[tauri::command]
fn set_quick_entry_visible(app: AppHandle, visible: bool, confirmed: Option<bool>) -> Result<(), String> {
    if !visible && !confirmed.unwrap_or(false) {
        quick_entry_notice(app.clone(), true)?;
        if let Some(window) = app.get_webview_window(LAUNCHER_LABEL) {
            window.show().map_err(|e| e.to_string())?;
            window.set_focus().map_err(|e| e.to_string())?;
            window.emit("quick-hide-notice", ()).map_err(|e| e.to_string())?;
        }
        return Ok(());
    }
    let state = app.state::<QuickState>();
    let mut prefs = state.0.lock().unwrap();
    let mut next = prefs.clone();
    next.visible = visible;
    if !visible { next.hide_notice_seen = true; }
    save_preferences(&app, &next)?;
    *prefs = next.clone();
    drop(prefs);
    if let Some(window) = app.get_webview_window(LAUNCHER_LABEL) {
        if visible { window.show() } else { window.hide() }.map_err(|e| e.to_string())?;
    }
    app.emit("quick-preferences-changed", next).map_err(|e| e.to_string())
}

// Clamp restored windows to a current screen's usable area (including changed displays).
fn position_at_edge(window: &tauri::WebviewWindow, saved: Option<(i32, i32)>) -> tauri::Result<()> {
    let monitors = window.available_monitors()?;
    let monitor = saved.and_then(|(x, y)| monitors.iter().find(|m| {
        let a = m.work_area();
        x >= a.position.x && y >= a.position.y
            && x < a.position.x + a.size.width as i32
            && y < a.position.y + a.size.height as i32
    })).cloned().or(window.primary_monitor()?);
    if let Some(monitor) = monitor {
        let a = monitor.work_area();
        let size = window.outer_size()?;
        let max_x = (a.position.x + a.size.width as i32 - size.width as i32).max(a.position.x);
        let max_y = (a.position.y + a.size.height as i32 - size.height as i32).max(a.position.y);
        let (x, y) = saved.unwrap_or((max_x - 16, a.position.y + (a.size.height as i32 - size.height as i32) / 2));
        window.set_position(PhysicalPosition::new(x.clamp(a.position.x, max_x), y.clamp(a.position.y, max_y)))?;
    }
    Ok(())
}

// macOS reports cursor, window and monitor pixels at different scale factors.
// Normalize launcher geometry to desktop points there; Windows uses desktop pixels.
fn launcher_coordinate_scale(scale: f64) -> f64 {
    if cfg!(target_os = "macos") { scale } else { 1.0 }
}

fn launcher_cursor(window: &tauri::WebviewWindow) -> tauri::Result<(f64, f64)> {
    let cursor = window.cursor_position()?;
    let scale = launcher_coordinate_scale(window.primary_monitor()?.map(|m| m.scale_factor()).unwrap_or(1.0));
    Ok((cursor.x / scale, cursor.y / scale))
}

fn set_launcher_position(window: &tauri::WebviewWindow, position: (i32, i32)) -> tauri::Result<()> {
    if cfg!(target_os = "macos") {
        window.set_position(tauri::LogicalPosition::new(position.0, position.1))
    } else {
        window.set_position(PhysicalPosition::new(position.0, position.1))
    }
}

fn snap_launcher(window: &tauri::WebviewWindow, position: Option<(i32, i32)>, old_size: (u32, u32), new_size: (f64, f64)) -> tauri::Result<(i32, i32)> {
    let monitors = window.available_monitors()?;
    let monitor = position.and_then(|(x, y)| monitors.iter().find(|m| {
        let scale = launcher_coordinate_scale(m.scale_factor());
        let p = m.position();
        let size = m.size();
        let center_x = (x + old_size.0 as i32 / 2) as f64;
        let center_y = (y + old_size.1 as i32 / 2) as f64;
        center_x >= p.x as f64 / scale && center_y >= p.y as f64 / scale
            && center_x < (p.x as f64 + size.width as f64) / scale
            && center_y < (p.y as f64 + size.height as f64) / scale
    })).cloned().or(window.current_monitor()?).or(window.primary_monitor()?);
    if let Some(monitor) = monitor {
        let a = monitor.work_area();
        let scale = launcher_coordinate_scale(monitor.scale_factor());
        let area = ((a.position.x as f64 / scale).round() as i32, (a.position.y as f64 / scale).round() as i32,
            (a.size.width as f64 / scale).round() as u32, (a.size.height as f64 / scale).round() as u32);
        let size_scale = monitor.scale_factor() / scale;
        let size = ((new_size.0 * size_scale).round() as u32, (new_size.1 * size_scale).round() as u32);
        let position = position.unwrap_or((area.0 + area.2 as i32 - old_size.0 as i32,
            area.1 + (area.3 as i32 - old_size.1 as i32) / 2));
        let snapped = launcher_position::snap_position(position, old_size, size, area);
        window.set_size(LogicalSize::new(new_size.0, new_size.1))?;
        set_launcher_position(window, snapped)?;
        return Ok(snapped);
    }
    Ok(position.unwrap_or((0, 0)))
}

fn resize_launcher(app: &AppHandle, width: f64, height: f64) -> Result<bool, String> {
    if let Some(window) = app.get_webview_window(LAUNCHER_LABEL) {
        let saved = app.state::<QuickState>().0.lock().unwrap().position;
        let scale = window.scale_factor().map_err(|e| e.to_string())?;
        let side = (56.0 * scale / launcher_coordinate_scale(scale)).round() as u32;
        let position = snap_launcher(&window, saved, (side, side), (width, height)).map_err(|e| e.to_string())?;
        return Ok(saved.map(|p| position.0 < p.0).unwrap_or(false));
    }
    Ok(false)
}

#[tauri::command]
fn quick_entry_notice(app: AppHandle, expanded: bool) -> Result<(), String> {
    resize_launcher(&app, if expanded { 320.0 } else { 56.0 }, if expanded { 148.0 } else { 56.0 }).map(|_| ())
}

#[tauri::command]
fn quick_entry_pointer_inside(app: AppHandle) -> Result<bool, String> {
    let window = app.get_webview_window(LAUNCHER_LABEL).ok_or("快捷入口不存在")?;
    if !window.is_visible().map_err(|e| e.to_string())? { return Ok(false); }
    let (x, y) = launcher_cursor(&window).map_err(|e| e.to_string())?;
    let scale = launcher_coordinate_scale(window.scale_factor().map_err(|e| e.to_string())?);
    let position = window.outer_position().map_err(|e| e.to_string())?;
    let size = window.outer_size().map_err(|e| e.to_string())?;
    let left = position.x as f64 / scale;
    let top = position.y as f64 / scale;
    Ok(x >= left && y >= top && x < left + size.width as f64 / scale && y < top + size.height as f64 / scale)
}

#[tauri::command]
fn quick_entry_hover(app: AppHandle, expanded: bool) -> Result<bool, String> {
    if app.state::<LauncherDrag>().0.lock().unwrap().is_some() { return Ok(false); }
    resize_launcher(&app, if expanded { 164.0 } else { 56.0 }, 56.0)
}

// WebView pointer-up can be lost when a captured drag crosses displays.
#[cfg(target_os = "macos")]
fn launcher_mouse_down() -> bool {
    #[link(name = "ApplicationServices", kind = "framework")]
    extern "C" { fn CGEventSourceButtonState(state: i32, button: u32) -> bool; }
    unsafe { CGEventSourceButtonState(0, 0) }
}

#[cfg(target_os = "windows")]
fn launcher_mouse_down() -> bool {
    #[link(name = "user32")]
    extern "system" { fn GetAsyncKeyState(key: i32) -> i16; }
    unsafe { GetAsyncKeyState(1) < 0 }
}

#[tauri::command]
fn quick_entry_drag(app: AppHandle, phase: String) -> Result<(), String> {
    let window = app.get_webview_window(LAUNCHER_LABEL).ok_or("快捷入口不存在")?;
    let state = app.state::<LauncherDrag>();
    let mut drag = state.0.lock().unwrap();
    match phase.as_str() {
        "start" => {
            let cursor = launcher_cursor(&window).map_err(|e| e.to_string())?;
            let position = window.outer_position().map_err(|e| e.to_string())?;
            let scale = launcher_coordinate_scale(window.scale_factor().map_err(|e| e.to_string())?);
            *drag = Some((cursor.0 - position.x as f64 / scale, cursor.1 - position.y as f64 / scale));
            #[cfg(any(target_os = "macos", target_os = "windows"))]
            {
                let handle = app.clone();
                std::thread::spawn(move || loop {
                    std::thread::sleep(std::time::Duration::from_millis(100));
                    if handle.state::<LauncherDrag>().0.lock().unwrap().is_none() { break; }
                    if !launcher_mouse_down() {
                        if let Err(error) = quick_entry_drag(handle.clone(), "end".into()) { eprintln!("吸附快捷入口失败: {error}"); }
                        break;
                    }
                });
            }
        }
        "move" => {
            if let Some((dx, dy)) = *drag {
                let cursor = launcher_cursor(&window).map_err(|e| e.to_string())?;
                set_launcher_position(&window, ((cursor.0 - dx).round() as i32, (cursor.1 - dy).round() as i32)).map_err(|e| e.to_string())?;
            }
        }
        "end" => {
            let Some((dx, dy)) = drag.take() else { return Ok(()); };
            let cursor = launcher_cursor(&window).map_err(|e| e.to_string())?;
            let scale = launcher_coordinate_scale(window.scale_factor().map_err(|e| e.to_string())?);
            let size = window.outer_size().map_err(|e| e.to_string())?;
            let old_size = ((size.width as f64 / scale).round() as u32, (size.height as f64 / scale).round() as u32);
            let p = ((cursor.0 - dx).round() as i32, (cursor.1 - dy).round() as i32);
            let position = snap_launcher(&window, Some(p), old_size, (56.0, 56.0)).map_err(|e| e.to_string())?;
            let state = app.state::<QuickState>();
            let mut prefs = state.0.lock().unwrap();
            prefs.position = Some(position);
            save_preferences(&app, &prefs)?;
            window.emit("quick-drag-ended", ()).map_err(|e| e.to_string())?;
        }
        _ => { *drag = None; }
    }
    Ok(())
}

fn ensure_launcher(app: &AppHandle) -> tauri::Result<()> {
    let prefs = quick_preferences(app.clone());
    let window = WebviewWindowBuilder::new(app, LAUNCHER_LABEL, WebviewUrl::App("index.html?launcher".into()))
        .title("快速记忆").inner_size(56.0, 56.0).resizable(false).accept_first_mouse(true)
        .decorations(false).transparent(true).shadow(false).always_on_top(true)
        .skip_taskbar(true).visible(false).focused(false).build()?;
    let size = window.outer_size()?;
    let scale = launcher_coordinate_scale(window.scale_factor()?);
    let position = snap_launcher(&window, prefs.position, ((size.width as f64 / scale).round() as u32, (size.height as f64 / scale).round() as u32), (56.0, 56.0))?;
    {
        let state = app.state::<QuickState>();
        let mut prefs = state.0.lock().unwrap();
        prefs.position = Some(position);
        if let Err(error) = save_preferences(app, &prefs) { eprintln!("保存悬浮位置失败: {error}"); }
    }
    if prefs.visible { window.show()?; }
    Ok(())
}

struct ApiProcess(Mutex<Option<Child>>);

fn workspace_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .canonicalize()
        .expect("failed to locate workspace root")
}

fn python_command() -> &'static str {
    if cfg!(target_os = "windows") {
        "python"
    } else {
        "python3"
    }
}

fn start_api_server(app: &AppHandle) -> Result<Child, String> {
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    std::fs::create_dir_all(&app_data_dir).map_err(|error| error.to_string())?;

    let (script_path, prompt_path) = if cfg!(debug_assertions) {
        let root = workspace_root();
        (
            root.join("services/api/server.py"),
            root.join("prompts/system_prompt.md"),
        )
    } else {
        let resource_dir = app
            .path()
            .resource_dir()
            .map_err(|error| error.to_string())?;
        (
            resource_dir.join("services/api/server.py"),
            resource_dir.join("prompts/system_prompt.md"),
        )
    };

    let python = if cfg!(target_os = "windows") && !cfg!(debug_assertions) {
        app.path()
            .resource_dir()
            .map_err(|error| error.to_string())?
            .join("runtime/python/python.exe")
    } else if cfg!(target_os = "macos") {
        std::env::var_os("MEMORY_MASTER_PYTHON")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .map(PathBuf::from)
                    .map(|home| home.join(".local/bin/python3.12"))
                    .filter(|path| path.is_file())
            })
            .unwrap_or_else(|| PathBuf::from("python3.12"))
    } else {
        PathBuf::from(python_command())
    };
    let log = std::fs::File::create(app_data_dir.join("api.log"))
        .map_err(|error| error.to_string())?;
    let mut command = Command::new(python);
    command
        .arg(script_path)
        .env("MEMORY_MASTER_API_PORT", API_PORT)
        .env("MEMORY_MASTER_DB", app_data_dir.join("memory_master.db"))
        .env("MEMORY_MASTER_SYSTEM_PROMPT", prompt_path)
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone().map_err(|error| error.to_string())?))
        .stderr(Stdio::from(log));
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    command.spawn().map_err(|error| format!("failed to start local API: {error}"))
}

fn stop_api_server(app: &AppHandle) {
    if let Some(state) = app.try_state::<ApiProcess>() {
        if let Ok(mut child) = state.0.lock() {
            if let Some(process) = child.as_mut() {
                let _ = process.kill();
                let _ = process.wait();
            }
            *child = None;
        }
    }
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MAIN_LABEL) {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn ensure_quick_add_window(app: &AppHandle) -> tauri::Result<tauri::WebviewWindow> {
    if let Some(window) = app.get_webview_window(QUICK_ADD_LABEL) {
        return Ok(window);
    }

    WebviewWindowBuilder::new(app, QUICK_ADD_LABEL, WebviewUrl::App("index.html".into()))
        .title("快速记忆")
        .inner_size(QUICK_ADD_WIDTH, QUICK_ADD_HEIGHT)
        .min_inner_size(QUICK_ADD_WIDTH, QUICK_ADD_HEIGHT)
        .resizable(false)
        .maximizable(false)
        .minimizable(false)
        .decorations(false)
        .background_color(Color(0, 0, 0, 0))
        .always_on_top(true)
        .skip_taskbar(true)
        .visible(false)
        .initialization_script("window.__MEMORY_MASTER_QUICK_ADD__ = true;")
        .build()
}

#[tauri::command]
fn show_quick_add_window(app: AppHandle) -> Result<(), String> {
    let window = ensure_quick_add_window(&app).map_err(|error| error.to_string())?;
    let size = Size::Logical(LogicalSize::new(QUICK_ADD_WIDTH, QUICK_ADD_HEIGHT));
    window
        .set_min_size(Some(size))
        .map_err(|error| error.to_string())?;
    window.set_size(size).map_err(|error| error.to_string())?;
    position_at_edge(&window, None).map_err(|error| error.to_string())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn hide_quick_add_window(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(QUICK_ADD_LABEL) {
        window.hide().map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn start_quick_add_drag(app: AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window(QUICK_ADD_LABEL)
        .ok_or_else(|| "quick-add window not found".to_string())?;
    window.start_dragging().map_err(|error| error.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(
            GlobalShortcutBuilder::new()
                .with_shortcut("CmdOrCtrl+Shift+M")
                .expect("failed to register quick add shortcut")
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        let _ = show_quick_add_window(app.clone());
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            show_quick_add_window,
            hide_quick_add_window,
            start_quick_add_drag,
            quick_preferences,
            set_quick_entry_visible,
            quick_entry_notice,
            quick_entry_hover,
            quick_entry_pointer_inside,
            quick_entry_drag
        ])
        .setup(|app| {
            let prefs = preferences_path(app.handle()).ok()
                .and_then(|path| std::fs::read(path).ok())
                .and_then(|bytes| serde_json::from_slice::<QuickPreferences>(&bytes).ok())
                .unwrap_or(QuickPreferences { visible: true, hide_notice_seen: false, position: None });
            app.manage(QuickState(Mutex::new(prefs)));
            app.manage(LauncherDrag(Mutex::new(None)));
            ensure_launcher(app.handle())?;
            if !cfg!(debug_assertions) {
                let api_process = start_api_server(&app.handle())?;
                app.manage(ApiProcess(Mutex::new(Some(api_process))));
            } else {
                app.manage(ApiProcess(Mutex::new(None)));
            }
            let _ = ensure_quick_add_window(&app.handle());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    let handle = app.handle().clone();
    app.run(move |_app_handle, event| {
        match event {
            tauri::RunEvent::Exit => stop_api_server(&handle),
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Reopen { .. } => show_main_window(&handle),
            _ => {}
        }
    });
}
