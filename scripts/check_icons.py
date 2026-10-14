"""PICORA-1D のアイコンが配布 exe の Windows リソースへ入っていることを確認。"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pefile

ROOT = Path(__file__).resolve().parents[1]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ico_images(path: Path) -> set[str]:
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert reserved == 0 and kind == 1
    images = set()
    for index in range(count):
        size, offset = struct.unpack_from("<II", data, 6 + index * 16 + 8)
        images.add(digest(data[offset:offset + size]))
    return images


def exe_images(path: Path) -> set[str]:
    images = set()
    with pefile.PE(str(path)) as binary:
        for resource in binary.DIRECTORY_ENTRY_RESOURCE.entries:
            if resource.id != 3:  # RT_ICON
                continue
            for icon in resource.directory.entries:
                for language in icon.directory.entries:
                    data = language.data.struct
                    images.add(digest(binary.get_data(data.OffsetToData, data.Size)))
    return images


def main() -> None:
    source = ROOT.parent / "20261009_PICORA-1D" / "ui" / "src-tauri" / "icons"
    icons = ROOT / "ui" / "src-tauri" / "icons"
    public = ROOT / "ui" / "public"
    for name, browser_name in (("icon.png", "icon.png"), ("icon.ico", "favicon.ico")):
        expected = source.joinpath(name).read_bytes()
        assert icons.joinpath(name).read_bytes() == expected
        assert public.joinpath(browser_name).read_bytes() == expected
        assert ROOT.joinpath("ui", "dist", browser_name).read_bytes() == expected
    expected = ico_images(icons / "icon.ico")
    dedicated = ROOT / "ui/src-tauri/target/release/icons/picora-shared-ad39f965.ico"
    assert dedicated.read_bytes() == icons.joinpath("icon.ico").read_bytes(), "ショートカット用の同梱ICOが一致しません"
    assert digest(dedicated.read_bytes()).startswith("ad39f965"), "アイコン変更時はキャッシュ識別用の資源名も更新してください"
    version = json.loads(ROOT.joinpath("ui", "src-tauri", "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    results = []
    for path in (ROOT / "ui" / "src-tauri" / "target" / "release" / "picora-2d.exe",
                 ROOT / "ui" / "src-tauri" / "target" / "release" / "bundle" / "nsis" / f"PICORA-2D_{version}_x64-setup.exe"):
        included = exe_images(path)
        assert expected <= included, f"共通アイコンの画像が不足しています: {path}"
        results.append({"file": str(path), "matching_icon_images": len(expected), "ok": True})
    print(json.dumps({"source_assets_match": True, "shortcut_icon_resource_match": True, "checks": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
