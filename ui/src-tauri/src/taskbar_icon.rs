//! Windows のタスクバーにも、ウィンドウと同じアイコンを明示する。

use std::ffi::c_void;

use tauri::Manager;

#[link(name = "user32")]
unsafe extern "system" {
    fn SendMessageW(hwnd: *mut c_void, message: u32, wparam: usize, lparam: isize) -> isize;
}

const WM_GETICON: u32 = 0x007f;
const WM_SETICON: u32 = 0x0080;
const ICON_SMALL: usize = 0;
const ICON_BIG: usize = 1;

pub fn apply(app: &tauri::App) -> tauri::Result<()> {
    for window in app.webview_windows().values() {
        let hwnd = window.hwnd()?.0;
        // Tauri/Tao は bundle.icon を ICON_SMALL に設定するが、ICON_BIG は未設定。
        // 未設定のタスクバー画像は Shell の古いキャッシュへフォールバックし得る。
        // 同じ HICON を借用する。所有・解放は引き続き Tao に任せ、二重解放しない。
        // setup はウィンドウ作成後、メインスレッド上で呼ばれる。
        unsafe {
            let icon = SendMessageW(hwnd, WM_GETICON, ICON_SMALL, 0);
            if icon == 0 {
                return Err(std::io::Error::other("PICORA-2D window icon is missing").into());
            }
            SendMessageW(hwnd, WM_SETICON, ICON_BIG, icon);
        }
    }
    Ok(())
}
