"""Windows の実ウィンドウアイコンを、source ICO の同サイズ画像と比較する。

使用例（backend フォルダーから uv run で実行）:
    uv run python ../scripts/check_window_icon.py --launch --exe <app.exe>
    uv run python ../scripts/check_window_icon.py --pid <既存PID>

WM_GETICON / GetClassLongPtr は読み取りのみ。自分が --launch で起動した
PID にだけ WM_CLOSE を送る。--force-close を明示した場合だけ、WM_CLOSE の終了待ち後に
同じ自己起動 PID を taskkill /T /F で終了する。既存 PID、Explorer、Shell のキャッシュは変更しない。
DrawIconEx の 32-bit DIB の alpha は形式によって未定義なので、黒・白の両背景で
合成した RGB を比較する（透明部分の見え方も判定）。PNG 保存は標準ライブラリのみ。

WinAPI の根拠:
https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-geticon
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclasslongptrw
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-loadimagew
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-drawiconex
https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-createdibsection
"""
from __future__ import annotations

import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "ui" / "src-tauri" / "target" / "release" / "picora-2d.exe"
DEFAULT_ICON = ROOT / "ui" / "src-tauri" / "icons" / "icon.ico"
WM_GETICON, WM_CLOSE = 0x007F, 0x0010
ICON_SMALL, ICON_BIG, ICON_SMALL2 = 0, 1, 2
GCLP_HICON, GCLP_HICONSM = -14, -34


class BITMAP(c.Structure):
    _fields_ = [("bmType", w.LONG), ("bmWidth", w.LONG), ("bmHeight", w.LONG),
                ("bmWidthBytes", w.LONG), ("bmPlanes", w.WORD), ("bmBitsPixel", w.WORD), ("bmBits", c.c_void_p)]


class ICONINFO(c.Structure):
    _fields_ = [("fIcon", w.BOOL), ("xHotspot", w.DWORD), ("yHotspot", w.DWORD),
                ("hbmMask", w.HANDLE), ("hbmColor", w.HANDLE)]


class BITMAPINFOHEADER(c.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
                ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
                ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG),
                ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]


class BITMAPINFO(c.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 1)]


class SHFILEINFO(c.Structure):
    _fields_ = [("hIcon", w.HANDLE), ("iIcon", c.c_int), ("dwAttributes", w.DWORD),
                ("szDisplayName", w.WCHAR * 260), ("szTypeName", w.WCHAR * 80)]


def _bind(dll, name, arguments, result):
    function = getattr(dll, name)
    function.argtypes, function.restype = arguments, result
    return function


class WindowsIcons:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("この検証は Windows で実行してください")
        self.user = c.WinDLL("user32", use_last_error=True)
        self.gdi = c.WinDLL("gdi32", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.shell = c.WinDLL("shell32", use_last_error=True)
        self.ole = c.WinDLL("ole32", use_last_error=True)
        self.file_info = _bind(self.shell, "SHGetFileInfoW", [w.LPCWSTR, w.DWORD, c.POINTER(SHFILEINFO), w.UINT, w.UINT], c.c_size_t)
        self.co_initialize = _bind(self.ole, "CoInitializeEx", [c.c_void_p, w.DWORD], c.c_long)
        self.co_uninitialize = _bind(self.ole, "CoUninitialize", [], None)
        self.callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.enum_windows = _bind(self.user, "EnumWindows", [self.callback_type, w.LPARAM], w.BOOL)
        self.window_pid = _bind(self.user, "GetWindowThreadProcessId", [w.HWND, c.POINTER(w.DWORD)], w.DWORD)
        self.visible = _bind(self.user, "IsWindowVisible", [w.HWND], w.BOOL)
        self.get_window = _bind(self.user, "GetWindow", [w.HWND, w.UINT], w.HWND)
        self.text_length = _bind(self.user, "GetWindowTextLengthW", [w.HWND], c.c_int)
        self.text = _bind(self.user, "GetWindowTextW", [w.HWND, w.LPWSTR, c.c_int], c.c_int)
        self.class_name = _bind(self.user, "GetClassNameW", [w.HWND, w.LPWSTR, c.c_int], c.c_int)
        self.message = _bind(self.user, "SendMessageTimeoutW", [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, c.POINTER(c.c_size_t)], c.c_ssize_t)
        self.class_long = _bind(self.user, "GetClassLongPtrW" if c.sizeof(c.c_void_p) == 8 else "GetClassLongW", [w.HWND, c.c_int], c.c_size_t)
        self.load = _bind(self.user, "LoadImageW", [w.HINSTANCE, w.LPCWSTR, w.UINT, c.c_int, c.c_int, w.UINT], w.HANDLE)
        self.copy_icon = _bind(self.user, "CopyIcon", [w.HANDLE], w.HANDLE)
        self.destroy_icon = _bind(self.user, "DestroyIcon", [w.HANDLE], w.BOOL)
        self.icon_info = _bind(self.user, "GetIconInfo", [w.HANDLE, c.POINTER(ICONINFO)], w.BOOL)
        self.draw = _bind(self.user, "DrawIconEx", [w.HDC, c.c_int, c.c_int, w.HANDLE, c.c_int, c.c_int, w.UINT, w.HANDLE, w.UINT], w.BOOL)
        self.post = _bind(self.user, "PostMessageW", [w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL)
        self.get_object = _bind(self.gdi, "GetObjectW", [w.HANDLE, c.c_int, c.c_void_p], c.c_int)
        self.create_dc = _bind(self.gdi, "CreateCompatibleDC", [w.HDC], w.HDC)
        self.delete_dc = _bind(self.gdi, "DeleteDC", [w.HDC], w.BOOL)
        self.create_dib = _bind(self.gdi, "CreateDIBSection", [w.HDC, c.POINTER(BITMAPINFO), w.UINT, c.POINTER(c.c_void_p), w.HANDLE, w.DWORD], w.HANDLE)
        self.select = _bind(self.gdi, "SelectObject", [w.HDC, w.HANDLE], w.HANDLE)
        self.delete_object = _bind(self.gdi, "DeleteObject", [w.HANDLE], w.BOOL)
        self.flush = _bind(self.gdi, "GdiFlush", [], w.BOOL)
        self.open_process = _bind(self.kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)
        self.query_image = _bind(self.kernel, "QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)], w.BOOL)
        self.close_handle = _bind(self.kernel, "CloseHandle", [w.HANDLE], w.BOOL)

    def process_image(self, pid: int) -> str | None:
        handle = self.open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            size = w.DWORD(32768)
            buffer = c.create_unicode_buffer(size.value)
            return buffer.value if self.query_image(handle, 0, buffer, c.byref(size)) else None
        finally:
            self.close_handle(handle)

    def windows(self, pid: int, *, title: str = "") -> list[dict]:
        found = []

        @self.callback_type
        def callback(hwnd, _):
            owner_pid = w.DWORD()
            self.window_pid(hwnd, c.byref(owner_pid))
            if owner_pid.value != pid or not self.visible(hwnd) or self.get_window(hwnd, 4):  # GW_OWNER
                return True
            buffer = c.create_unicode_buffer(self.text_length(hwnd) + 1)
            self.text(hwnd, buffer, len(buffer))
            if title and title not in buffer.value:
                return True
            class_buffer = c.create_unicode_buffer(256)
            self.class_name(hwnd, class_buffer, len(class_buffer))
            found.append({"hwnd": int(hwnd), "pid": owner_pid.value, "title": buffer.value, "class": class_buffer.value})
            return True

        if not self.enum_windows(callback, 0):
            raise c.WinError(c.get_last_error())
        return found

    def get_icon(self, hwnd: int, kind: str) -> tuple[int, str]:
        """WM_GETICON → class icon。small では最後に ICON_SMALL2 を試す。"""
        result = c.c_size_t()
        flag = ICON_BIG if kind == "large" else ICON_SMALL
        if not self.message(hwnd, WM_GETICON, flag, 0, 0x0002 | 0x0020, 1000, c.byref(result)):
            raise RuntimeError(f"WM_GETICON({kind}) が失敗またはタイムアウトしました (hwnd={hwnd}, error={c.get_last_error()})")
        if result.value:
            return result.value, f"WM_GETICON/{kind}"
        icon = self.class_long(hwnd, GCLP_HICON if kind == "large" else GCLP_HICONSM)
        if icon:
            return int(icon), "GCLP_HICON" if kind == "large" else "GCLP_HICONSM"
        if kind == "small":
            if self.message(hwnd, WM_GETICON, ICON_SMALL2, 0, 0x0002 | 0x0020, 1000, c.byref(result)) and result.value:
                return result.value, "WM_GETICON/ICON_SMALL2"
        raise RuntimeError(f"{kind} の実行中アイコンが取得できません (hwnd={hwnd})")

    def raw_icon_handles(self, hwnd: int) -> dict:
        handles, errors = {}, []
        for label, flag in (("WM_GETICON_BIG", ICON_BIG), ("WM_GETICON_SMALL", ICON_SMALL), ("WM_GETICON_SMALL2", ICON_SMALL2)):
            result = c.c_size_t()
            c.set_last_error(0)
            ok = self.message(hwnd, WM_GETICON, flag, 0, 0x0002 | 0x0020, 1000, c.byref(result))
            handles[label] = result.value if ok else 0
            if not ok:
                errors.append({"query": label, "error": c.get_last_error(), "note": "query failed or timed out"})
        handles["GCLP_HICON"] = int(self.class_long(hwnd, GCLP_HICON))
        handles["GCLP_HICONSM"] = int(self.class_long(hwnd, GCLP_HICONSM))
        return {"raw_icon_handles": handles, "icon_query_errors": errors,
                "large_icon_missing": handles["WM_GETICON_BIG"] == 0 and handles["GCLP_HICON"] == 0}

    def size(self, icon: int) -> tuple[int, int]:
        info = ICONINFO()
        if not self.icon_info(icon, c.byref(info)):
            raise c.WinError(c.get_last_error())
        try:
            bitmap = BITMAP()
            if not self.get_object(info.hbmColor or info.hbmMask, c.sizeof(bitmap), c.byref(bitmap)):
                raise c.WinError(c.get_last_error())
            return int(bitmap.bmWidth), int(bitmap.bmHeight if info.hbmColor else bitmap.bmHeight // 2)
        finally:
            # GetIconInfo が生成した bitmap のみ解放する。借用した HICON は解放しない。
            if info.hbmColor:
                self.delete_object(info.hbmColor)
            if info.hbmMask:
                self.delete_object(info.hbmMask)

    def source_icon(self, path: Path, width: int, height: int) -> int:
        icon = self.load(None, str(path), 1, width, height, 0x0010)  # IMAGE_ICON / LR_LOADFROMFILE、LR_SHARED は不使用
        if not icon:
            raise c.WinError(c.get_last_error())
        return int(icon)

    def render(self, icon: int, width: int, height: int, background: int) -> bytes:
        if not (0 < width <= 1024 and 0 < height <= 1024):
            raise ValueError(f"不正なアイコンサイズ: {width}x{height}")
        dc = self.create_dc(None)
        if not dc:
            raise c.WinError(c.get_last_error())
        bitmap = old = None
        try:
            info = BITMAPINFO()
            info.bmiHeader = BITMAPINFOHEADER(c.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
            bits = c.c_void_p()
            bitmap = self.create_dib(dc, c.byref(info), 0, c.byref(bits), None, 0)
            if not bitmap or not bits.value:
                raise c.WinError(c.get_last_error())
            old = self.select(dc, bitmap)
            if not old or old == c.c_void_p(-1).value:
                raise RuntimeError("DIB をメモリ DC へ選択できません")
            bgra = bytes((background, background, background, 255)) * (width * height)
            c.memmove(bits, bgra, len(bgra))
            if not self.draw(dc, 0, 0, icon, width, height, 0, None, 0x0003 | 0x0010):  # DI_NORMAL | DI_NOMIRROR
                raise c.WinError(c.get_last_error())
            if not self.flush():
                raise c.WinError(c.get_last_error())
            data = c.string_at(bits, len(bgra))
            rgb = bytearray(width * height * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = data[2::4], data[1::4], data[0::4]
            return bytes(rgb)
        finally:
            if old and old != c.c_void_p(-1).value:
                self.select(dc, old)
            if bitmap:
                self.delete_object(bitmap)
            self.delete_dc(dc)


def write_png(path: Path, rgb: bytes, width: int, height: int) -> None:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    scanlines = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    image = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    path.write_bytes(image + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))


def compare_icon(api: WindowsIcons, window: dict, kind: str, source: Path, out_dir: Path | None, tolerance: int,
                 supplied: tuple[int, str] | None = None, reference: int | None = None) -> dict:
    borrowed, origin = supplied or api.get_icon(window["hwnd"], kind)
    runtime = api.copy_icon(borrowed)  # 以後は所有コピーを使用し、相手のアイコンを変更しない。
    if not runtime:
        raise c.WinError(c.get_last_error())
    expected = None
    try:
        width, height = api.size(runtime)
        expected = api.copy_icon(reference) if reference else api.source_icon(source, width, height)
        if not expected:
            raise c.WinError(c.get_last_error())
        checks = []
        for name, background in (("black", 0), ("white", 255)):
            actual_rgb = api.render(runtime, width, height, background)
            source_rgb = api.render(expected, width, height, background)
            differences = [abs(a - b) for a, b in zip(actual_rgb, source_rgb)]
            changed = sum(any(value > tolerance for value in differences[offset:offset + 3]) for offset in range(0, len(differences), 3))
            check = {"background": name, "matching": changed == 0, "different_pixels": changed,
                     "max_channel_difference": max(differences, default=0),
                     "runtime_sha256": hashlib.sha256(actual_rgb).hexdigest(), "source_sha256": hashlib.sha256(source_rgb).hexdigest()}
            if out_dir:
                prefix = f"{window['hwnd']}-{kind}-{width}x{height}-{name}"
                actual_path, source_path = out_dir / f"{prefix}-runtime.png", out_dir / f"{prefix}-source.png"
                write_png(actual_path, actual_rgb, width, height)
                write_png(source_path, source_rgb, width, height)
                check.update(runtime_png=str(actual_path), source_png=str(source_path))
            checks.append(check)
        return {"kind": kind, "handle_source": origin, "handle": borrowed, "width": width, "height": height,
                "matching": all(check["matching"] for check in checks), "comparisons": checks}
    finally:
        if expected:
            api.destroy_icon(expected)
        api.destroy_icon(runtime)


def check_shortcut(api: WindowsIcons, shortcut: Path, source: Path, out_dir: Path | None, tolerance: int) -> dict:
    """Shellが返すショートカット画像を、共通ICO＋同じリンク矢印と比較する。"""
    status = api.co_initialize(None, 2)  # COINIT_APARTMENTTHREADED
    if status < 0:
        raise RuntimeError(f"CoInitializeEx failed: {status}")
    try:
        info = SHFILEINFO()
        if not api.file_info(str(shortcut), 0, c.byref(info), c.sizeof(info), 0x1000):  # SHGFI_ICONLOCATION
            raise RuntimeError("Shortcut icon location could not be read")
        report = {"shortcut_file": str(shortcut), "icon_file": info.szDisplayName, "icon_index": info.iIcon, "icons": []}
        for kind, flags in (("large", 0x100), ("small", 0x101)):  # SHGFI_ICON, SHGFI_SMALLICON
            icon_info = SHFILEINFO()
            if not api.file_info(str(shortcut), 0, c.byref(icon_info), c.sizeof(icon_info), flags) or not icon_info.hIcon:
                raise RuntimeError(f"Shortcut {kind} icon could not be read")
            reference = SHFILEINFO()
            try:
                # Shell adds the arrow to .lnk icons. Apply that same overlay
                # to the reference ICO; no image region is masked or ignored.
                if not api.file_info(str(source), 0, c.byref(reference), c.sizeof(reference), flags | 0x8000):
                    raise RuntimeError("Reference ICO with link overlay could not be read")
                report["icons"].append(compare_icon(api, {"hwnd": "shortcut"}, kind, source, out_dir, tolerance,
                                                    (int(icon_info.hIcon), "SHGetFileInfo/shortcut"), int(reference.hIcon)))
            finally:
                if reference.hIcon:
                    api.destroy_icon(reference.hIcon)
                api.destroy_icon(icon_info.hIcon)
        report["matching"] = all(icon["matching"] for icon in report["icons"])
        return report
    finally:
        api.co_uninitialize()


def close_owned(api: WindowsIcons, process: subprocess.Popen | None, timeout: float = 10, *, force: bool = False) -> dict:
    if process is None:
        return {"action": "existing PID: read only"}
    if process.poll() is not None:
        return {"pid": process.pid, "exited": True, "exit_code": process.returncode}
    sent = []
    # 再列挙で HWND の再利用による他 PID への WM_CLOSE を防ぐ。
    for window in api.windows(process.pid):
        current = w.DWORD()
        api.window_pid(window["hwnd"], c.byref(current))
        if current.value == process.pid and api.post(window["hwnd"], WM_CLOSE, 0, 0):
            sent.append(window["hwnd"])
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        result = {"pid": process.pid, "action": "WM_CLOSE only", "hwnds": sent, "exited": False}
        if not force:
            return {**result, "note": "自分が起動したウィンドウの終了待ちがタイムアウトしました。強制終了は行っていません。"}
        # Popen が保持する元のプロセスハンドルで、同じ自己起動プロセスの存続を確認する。
        # --pid では process 自体が渡らず、この分岐へ入れない。
        if process.poll() is not None:
            return {**result, "exited": True, "exit_code": process.returncode, "forced": False}
        killer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            killed = subprocess.run([str(killer), "/PID", str(process.pid), "/T", "/F"],
                                    capture_output=True, text=True, encoding="mbcs", errors="replace",
                                    timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            result.update(action="WM_CLOSE then taskkill /PID /T /F (explicit --force-close)", forced=True,
                          taskkill_returncode=killed.returncode, taskkill_stdout=killed.stdout.strip(), taskkill_stderr=killed.stderr.strip())
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                result["note"] = "明示された自己起動 PID の強制終了後も終了を確認できませんでした。"
            result.update(exited=process.poll() is not None, exit_code=process.returncode)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result.update(forced=True, force_error=str(exc))
        return result
    return {"pid": process.pid, "action": "WM_CLOSE only", "hwnds": sent, "exited": True, "exit_code": process.returncode}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="実ウィンドウの large/small アイコンと source ICO を WinAPI で比較")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--launch", action="store_true", help="--exe のアプリをテスト起動し、終了時はその PID だけ WM_CLOSE")
    mode.add_argument("--pid", type=int, help="既存 PID を読み取り専用で検証（終了操作なし）")
    mode.add_argument("--shortcut", type=Path, help="Shellがショートカットに使う実際のアイコンを読み取り検証")
    parser.add_argument("--force-close", action="store_true", help="--launch 専用。WM_CLOSE を 10 秒待った後、自己起動 PID のみ taskkill /T /F")
    parser.add_argument("--exe", type=Path, help=f"テスト実行 exe、または既存 PID の期待 exe（既定: {DEFAULT_EXE}）")
    parser.add_argument("--source-icon", type=Path, default=DEFAULT_ICON)
    parser.add_argument("--title", default="", help="同じ PID 内のウィンドウタイトルを部分一致で絞る")
    parser.add_argument("--timeout", type=float, default=30, help="ウィンドウの出現待ち秒")
    parser.add_argument("--settle", type=float, default=1, help="ウィンドウ出現後のアイコン初期化待ち秒")
    parser.add_argument("--tolerance", type=int, default=0, help="RGB channel 差の許容値（既定は完全一致）")
    parser.add_argument("--out-dir", type=Path, help="比較 PNG と report.json を保存する任意フォルダー")
    args = parser.parse_args(argv)
    if args.force_close and not args.launch:
        parser.error("--force-close は --launch と併用してください。既存 --pid に強制終了は行えません")
    if os.name != "nt":
        parser.error("Windows 専用です")
    if args.pid is not None and args.pid <= 0:
        parser.error("--pid は正の整数です")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 120 or not math.isfinite(args.settle) or not 0 <= args.settle <= 10:
        parser.error("--timeout は 0〜120 秒（0 除外）、--settle は 0〜10 秒です")
    if not 0 <= args.tolerance <= 255:
        parser.error("--tolerance は 0〜255 です")
    source = args.source_icon.resolve()
    if not source.is_file():
        parser.error(f"source アイコンがありません: {source}")
    executable = (args.exe or DEFAULT_EXE).resolve() if args.launch or args.exe else None
    if executable and not executable.is_file():
        parser.error(f"実行ファイルがありません: {executable}")
    out_dir = args.out_dir.resolve() if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    api = WindowsIcons()
    if args.shortcut:
        shortcut = args.shortcut.resolve()
        if not shortcut.is_file() or shortcut.suffix.lower() != ".lnk":
            parser.error(f"Shortcut does not exist: {shortcut}")
        report = check_shortcut(api, shortcut, source, out_dir, args.tolerance)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if out_dir:
            (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if report["matching"] else 1
    child = None
    report = {"source_icon": str(source), "tolerance": args.tolerance, "mode": "launch" if args.launch else "existing PID", "windows": []}
    try:
        if args.launch:
            child = subprocess.Popen([str(executable)], cwd=executable.parent, creationflags=subprocess.CREATE_NO_WINDOW)
        pid = child.pid if child else args.pid
        report["pid"] = pid
        image = api.process_image(pid)
        report["process_image"] = image
        if executable and (not image or os.path.normcase(str(Path(image).resolve())) != os.path.normcase(str(executable))):
            raise RuntimeError("PID の実行ファイルが --exe の指定と一致しません")
        deadline = time.monotonic() + args.timeout
        windows = []
        while time.monotonic() < deadline:
            if child and child.poll() is not None:
                raise RuntimeError(f"テスト起動したプロセスが終了しました: {child.returncode}")
            windows = api.windows(pid, title=args.title)
            if windows:
                break
            time.sleep(0.2)
        if not windows:
            raise RuntimeError(f"PID {pid} の可視な独立ウィンドウが見つかりません")
        if args.settle:
            time.sleep(args.settle)
        for window in windows:
            window.update(api.raw_icon_handles(window["hwnd"]))
            window["icons"] = []
            for kind in ("large", "small"):
                try:
                    window["icons"].append(compare_icon(api, window, kind, source, out_dir, args.tolerance))
                except (OSError, RuntimeError, ValueError) as exc:
                    window["icons"].append({"kind": kind, "matching": False, "error": str(exc)})
            report["windows"].append(window)
        report["matching"] = all(icon["matching"] for window in report["windows"] for icon in window["icons"])
    except (OSError, RuntimeError, ValueError) as exc:
        report.update(matching=False, error=str(exc))
    finally:
        report["cleanup"] = close_owned(api, child, force=args.force_close)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if out_dir:
        (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["cleanup"].get("exited") is False:
        return 2
    return 0 if report.get("matching") else 1


if __name__ == "__main__":
    raise SystemExit(main())
