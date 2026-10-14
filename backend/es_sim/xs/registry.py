"""ユーザー登録した衝突断面積の永続レジストリ。

データは SI (eV, m²) で保存する。保存先は起動時に指定するか、
PICORA_XS_REGISTRY 環境変数で選べる。既定は作業フォルダー/data。
登録データを単一ガスの既存 MCC / 流体係数経路へ明示的に変換する。
"""

from __future__ import annotations

import csv
import copy
import io
import json
import math
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import numpy as np

from .convert import to_v1_processes
from .lxcat import parse_lxcat_document
from .masses import lookup_mass_amu, mass_amu_from_ratio, mass_ratio_from_amu
from .model import CrossSection, LxcatDatabase, LxcatDocument

MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_POINTS = 200_000
_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def default_registry_path() -> Path:
    return Path(os.environ.get("PICORA_XS_REGISTRY", str(Path.cwd() / "data" / "cross_sections.json"))).expanduser().resolve()


def _text(value: str, label: str, *, required: bool = False, limit: int = 4096) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} は文字列で指定してください")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{label} を入力してください")
    if len(value) > limit or "\x00" in value:
        raise ValueError(f"{label} は {limit} 文字以内で指定してください")
    return value


def _positive(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} は有限の正の数で指定してください")
    return value


def _source_url(value: str) -> str:
    value = _text(value, "出典 URL", limit=2048)
    if value:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("出典 URL は http:// または https:// で指定してください")
    return value


def validate_cross_section(cs: CrossSection) -> None:
    """モデルのテーブル検査に加えて、登録・保存済み入力の物理量を検査する。"""
    cs._check_table()
    _text(cs.target, "ガス名", required=True, limit=128)
    _text(cs.projectile, "入射粒子", required=True, limit=128)
    if len(cs.energy_ev) < 2 or len(cs.energy_ev) > MAX_POINTS:
        raise ValueError(f"断面積は 2〜{MAX_POINTS} 点の表が必要です")
    if not np.any(np.diff(cs.energy_ev) > 0):
        raise ValueError("エネルギー表には異なるエネルギーの点が必要です")
    if not math.isfinite(cs.threshold_ev) or cs.threshold_ev < 0:
        raise ValueError("閾値 [eV] は有限の 0 以上の値が必要です")
    if cs.kind in ("EXCITATION", "IONIZATION") and cs.threshold_ev <= 0:
        raise ValueError("励起・電離の閾値 [eV] は正の値が必要です")
    if cs.kind in ("ELASTIC", "EFFECTIVE", "ISOTROPIC", "BACKSCAT") and cs.threshold_ev != 0:
        raise ValueError("弾性・有効・イオン散乱の閾値 [eV] は 0 です")
    if cs.threshold_ev > float(cs.energy_ev[-1]):
        raise ValueError("閾値 [eV] がエネルギー表の上限を超えています")
    if cs.mass_ratio is not None and (not math.isfinite(cs.mass_ratio) or not 0 < cs.mass_ratio < 1):
        raise ValueError("質量比 m/M は有限で 0 < m/M < 1 が必要です")
    if cs.weight_ratio is not None and (not math.isfinite(cs.weight_ratio) or cs.weight_ratio <= 0):
        raise ValueError("統計重み比は有限の正の数が必要です")
    # 閾値より下の非零断面積を黙って捨てない。丸め誤差はエネルギーにだけ許容する。
    if cs.threshold_ev > 0:
        below = cs.energy_ev < cs.threshold_ev - max(1e-10, cs.threshold_ev * 1e-8)
        if np.any(cs.sigma_m2[below] > 0):
            raise ValueError("閾値未満の断面積は 0 にしてください")


def parse_csv_cross_section(
    text: str, *, gas: str, kind: str, threshold_ev: float = 0,
    projectile: str = "e", label: str = "", mass_amu: float | None = None,
) -> CrossSection:
    """厳密な 2 列 CSV を読む。単位換算を推測せず、eV / m² のみ受理する。"""
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("CSV ファイルは 8 MiB 以下にしてください")
    try:
        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff")), strict=True))
    except csv.Error as exc:
        raise ValueError(f"CSV の引用符・列区切りが不正です: {exc}") from exc
    rows = [(n, row) for n, row in enumerate(rows, 1) if row and any(s.strip() for s in row) and not row[0].strip().startswith("#")]
    if not rows:
        raise ValueError("CSV が空です")
    normalize = lambda value: re.sub(r"\s+", "", value).replace("²", "2").replace("^", "")
    header = [normalize(s) for s in rows[0][1]]
    if header not in (["energy[eV]", "sigma[m2]"], ["energy_ev", "sigma_m2"]):
        raise ValueError("CSV ヘッダーは energy[eV],sigma[m2]（または energy_ev,sigma_m2）です。単位は eV / m² に変換してください")
    energy, sigma = [], []
    for line, row in rows[1:]:
        if len(row) != 2:
            raise ValueError(f"CSV {line} 行目: 2 列（エネルギー、断面積）が必要です")
        values = []
        for token in row:
            token = token.strip()
            if not _NUMBER.fullmatch(token) or not math.isfinite(float(token)):
                raise ValueError(f"CSV {line} 行目: 有限の数値を入力してください")
            values.append(float(token))
        energy.append(values[0])
        sigma.append(values[1])
    if any(right <= left for left, right in zip(energy, energy[1:])):
        raise ValueError("CSV のエネルギーは重複なしの昇順で指定してください")
    gas = _text(gas, "ガス名", required=True, limit=128)
    projectile = _text(projectile, "入射粒子", required=True, limit=128)
    mass = _positive(mass_amu, "ガス質量 [amu]") or lookup_mass_amu(gas)
    ratio = mass_ratio_from_amu(mass) if mass and kind in ("ELASTIC", "EFFECTIVE") and projectile == "e" else None
    cs = CrossSection(kind=kind, target=gas, projectile=projectile, threshold_ev=threshold_ev,
                      mass_ratio=ratio, energy_ev=energy, sigma_m2=sigma,
                      process=_text(label, "過程名", limit=256), columns="Energy (eV) | Cross section (m2)")
    validate_cross_section(cs)
    return cs


class CrossSectionRegistry:
    """単一ローカルサーバー向け。各操作で再読込し、同一プロセス内をロックする。"""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path).expanduser().resolve() if path is not None else default_registry_path()
        if self.path.suffix.lower() != ".json":
            raise ValueError("断面積レジストリの保存先は .json ファイルを指定してください")
        self._lock = threading.RLock()

    def _validate_entries(self, entries: list[dict[str, Any]]) -> None:
        ids: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or entry["id"] in ids:
                raise ValueError("登録 ID が不正または重複しています")
            ids.add(entry["id"])
            cs = CrossSection.from_dict(entry["cross_section"])
            validate_cross_section(cs)
            if entry["gas"] != cs.target:
                raise ValueError("登録ガス名が断面積の標的と一致しません")
            _text(entry["source"], "出典", required=True)
            _source_url(entry.get("source_url", ""))
            _positive(entry.get("mass_amu"), "ガス質量 [amu]")

    def _migrate_v1(self, entries: list[dict[str, Any]], original: bytes) -> list[dict[str, Any]]:
        """同じ旧登録操作で作られた過程をまとめる。元の bytes は別ファイルへ保持する。"""
        self._validate_entries(entries)
        grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for entry in entries:
            # 旧 LXCat 一括登録は同じ created_at を持つ。CSV 個別登録は別のセットとなる。
            key = tuple(str(entry.get(k, "")) for k in ("created_at", "format", "source", "source_url", "notes"))
            if not key[0]:
                key = (*key, entry["id"])
            grouped.setdefault(key, []).append(entry)
        sets = []
        for index, group in enumerate(grouped.values(), 1):
            gases = list(dict.fromkeys(entry["gas"] for entry in group))
            name = f"{' / '.join(gases)} · {group[0]['source']}"[:240]
            sets.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"picora-xs-v1:{group[0]['id']}")),
                         "name": f"{name} ({index})", "created_at": group[0].get("created_at", ""),
                         "updated_at": group[0].get("created_at", ""), "entries": group, "warnings": [],
                         "migrated_from": 1})
        backup = self.path.with_name(self.path.name + ".v1.bak")
        if backup.exists() and backup.read_bytes() != original:
            backup = self.path.with_name(self.path.name + f".v1-{uuid.uuid4().hex}.bak")
        if not backup.exists():
            with backup.open("xb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
        self._write(sets)
        return sets

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            original = self.path.read_bytes()
            data = json.loads(original.decode("utf-8"))
            if isinstance(data, dict) and data.get("version") == 1 and isinstance(data.get("entries"), list):
                return self._migrate_v1(data["entries"], original)
            if not isinstance(data, dict) or data.get("version") != 2 or not isinstance(data.get("sets"), list):
                raise ValueError("未対応のレジストリ形式です")
            sets = data["sets"]
            ids: set[str] = set()
            for item in sets:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in ids:
                    raise ValueError("セット ID が不正または重複しています")
                ids.add(item["id"])
                _text(item["name"], "セット名", required=True, limit=256)
                if not isinstance(item.get("entries"), list) or not item["entries"]:
                    raise ValueError("セットに断面積がありません")
                self._validate_entries(item["entries"])
            return sets
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"断面積レジストリを読めません: {exc}") from exc

    def _write(self, sets: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"version": 2, "sets": sets}, ensure_ascii=False, indent=2, allow_nan=False)
        handle, name = tempfile.mkstemp(prefix=f".{self.path.stem}-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def list(self) -> dict[str, Any]:
        with self._lock:
            sets = self._read()
        return {"storage_path": str(self.path), "sets": sets}

    def register(
        self, cross_sections: list[CrossSection], *, source: str, source_url: str = "",
        notes: str = "", format: str = "csv", mass_amu: float | None = None,
        set_name: str = "", set_id: str | None = None, warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        source = _text(source, "出典", required=True)
        source_url = _source_url(source_url)
        notes = _text(notes, "メモ")
        mass_amu = _positive(mass_amu, "ガス質量 [amu]")
        if not cross_sections:
            raise ValueError("登録する断面積がありません")
        created = datetime.now(timezone.utc).isoformat()
        added = []
        for cs in cross_sections:
            validate_cross_section(cs)
            added.append({"id": str(uuid.uuid4()), "gas": cs.target, "source": source,
                          "source_url": source_url, "notes": notes, "format": format,
                          "mass_amu": mass_amu or lookup_mass_amu(cs.target) or (mass_amu_from_ratio(cs.mass_ratio) if cs.mass_ratio else None), "created_at": created,
                          "cross_section": cs.to_dict()})
        return self._add_entries(added, set_name=set_name, set_id=set_id, warnings=warnings)

    def _add_entries(self, added: list[dict[str, Any]], *, set_name: str = "", set_id: str | None = None,
                     warnings: list[str] | None = None) -> dict[str, Any]:
        if set_id and set_name.strip():
            raise ValueError("新規セット名と追加先セット ID は同時に指定できません")
        name = _text(set_name, "セット名", required=not set_id, limit=256)
        with self._lock:
            sets = self._read()
            if set_id:
                item = next((item for item in sets if item["id"] == set_id), None)
                if item is None:
                    raise ValueError("追加先セットが存在しません")
                item["entries"].extend(added)
                item["warnings"] = list(dict.fromkeys(item.get("warnings", []) + (warnings or [])))
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
            else:
                if any(existing["name"] == name for existing in sets):
                    raise ValueError("同じ名前のセットがあります。別の名前を指定するか、既存セットへの追加を選んでください")
                now = datetime.now(timezone.utc).isoformat()
                item = {"id": str(uuid.uuid4()), "name": name, "created_at": now, "updated_at": now,
                        "entries": added, "warnings": list(warnings or [])}
                sets.append(item)
            self._write(sets)
        return item

    def import_lxcat(self, text: str, *, source: str, source_url: str = "", notes: str = "",
                     set_name: str = "", set_id: str | None = None) -> dict[str, Any]:
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("LXCat ファイルは 8 MiB 以下にしてください")
        doc = parse_lxcat_document(text)
        item = self.register(doc.all_cross_sections(), source=source, source_url=source_url, notes=notes,
                             format="lxcat", set_name=set_name, set_id=set_id, warnings=doc.warnings)
        return {"set": item, "added": len(doc.all_cross_sections()), "warnings": doc.warnings, "storage_path": str(self.path)}

    def import_csv_batch(self, files: list[dict[str, Any]], *, source: str, source_url: str = "", notes: str = "",
                         set_name: str = "", set_id: str | None = None) -> dict[str, Any]:
        """複数ファイルを全件検証してから1回で保存する。途中成功を残さない。"""
        if not files or sum(len(item["text"].encode("utf-8")) for item in files) > MAX_TEXT_BYTES:
            raise ValueError("CSV は 1 件以上、合計 8 MiB 以下で指定してください")
        added = []
        for index, values in enumerate(files, 1):
            filename = _text(values.get("filename", ""), "ファイル名", limit=256)
            kwargs = {key: value for key, value in values.items() if key != "filename"}
            try:
                cs = parse_csv_cross_section(**kwargs)
                mass = values.get("mass_amu") or lookup_mass_amu(cs.target) or (mass_amu_from_ratio(cs.mass_ratio) if cs.mass_ratio else None)
                validate_cross_section(cs)
                added.append({"id": str(uuid.uuid4()), "gas": cs.target, "source": _text(source, "出典", required=True),
                              "source_url": _source_url(source_url), "notes": _text(notes, "メモ"),
                              "format": "csv", "filename": filename, "mass_amu": mass,
                              "created_at": datetime.now(timezone.utc).isoformat(), "cross_section": cs.to_dict()})
            except ValueError as exc:
                raise ValueError(f"CSV {index} ({filename or 'テキスト入力'}): {exc}") from exc
        item = self._add_entries(added, set_name=set_name, set_id=set_id)
        return {"set": item, "added": len(added), "warnings": [], "storage_path": str(self.path)}

    def rename(self, set_id: str, name: str) -> dict[str, Any]:
        name = _text(name, "セット名", required=True, limit=256)
        with self._lock:
            sets = self._read()
            item = next((item for item in sets if item["id"] == set_id), None)
            if item is None:
                raise ValueError("編集するセットが存在しません")
            if any(existing["id"] != set_id and existing["name"] == name for existing in sets):
                raise ValueError("同じ名前のセットがあります。別の名前を指定してください")
            item["name"] = name
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write(sets)
        return item

    def copy_entries(self, source_set_id: str, entry_ids: list[str], *, set_name: str = "", set_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            sets = self._read()
            source = next((item for item in sets if item["id"] == source_set_id), None)
            if source is None:
                raise ValueError("コピー元セットが存在しません")
            by_id = {entry["id"]: entry for entry in source["entries"]}
            if not entry_ids or len(set(entry_ids)) != len(entry_ids) or any(id_ not in by_id for id_ in entry_ids):
                raise ValueError("追加する過程 ID が空、重複、または存在しません")
            added = []
            for entry_id in entry_ids:
                entry = copy.deepcopy(by_id[entry_id])
                entry["origin"] = {"set_id": source_set_id, "entry_id": entry_id}
                entry["id"] = str(uuid.uuid4())
                added.append(entry)
            item = self._add_entries(added, set_name=set_name, set_id=set_id, warnings=source.get("warnings", []))
        return {"set": item, "added": len(added), "warnings": [], "storage_path": str(self.path)}

    def remove_entries(self, set_id: str, entry_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            sets = self._read()
            item = next((item for item in sets if item["id"] == set_id), None)
            if item is None:
                raise ValueError("編集するセットが存在しません")
            known = {entry["id"] for entry in item["entries"]}
            if not entry_ids or len(set(entry_ids)) != len(entry_ids) or any(id_ not in known for id_ in entry_ids):
                raise ValueError("削除する過程 ID が空、重複、または存在しません")
            if len(entry_ids) == len(known):
                raise ValueError("セットの最後の過程は削除できません。セット全体を削除してください")
            item["entries"] = [entry for entry in item["entries"] if entry["id"] not in set(entry_ids)]
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write(sets)
        return item

    def delete(self, ids: list[str]) -> int:
        with self._lock:
            sets = self._read()
            known = {item["id"] for item in sets}
            if not ids or len(set(ids)) != len(ids) or any(id_ not in known for id_ in ids):
                raise ValueError("削除するセット ID が空、重複、または存在しません")
            self._write([item for item in sets if item["id"] not in set(ids)])
        return len(ids)

    def resolve(self, set_id: str, species: Literal["electron", "ion"]) -> dict[str, Any]:
        """セットの全過程を既存 MCC / 流体系の XsProcess へ渡す。

        登録データが削除されても計算条件を再現できるよう、配列をプロジェクトへ埋め込む。
        現在の計算エンジンは単一ガス。異なるガスの同時選択を明示的に拒否する。
        """
        with self._lock:
            sets = self._read()
        item = next((item for item in sets if item["id"] == set_id), None)
        if item is None:
            raise ValueError("適用するセットが存在しません")
        selected = item["entries"]
        gases = {entry["gas"] for entry in selected}
        if len(gases) != 1:
            raise ValueError("現在の計算は単一ガスです。同じガスの過程だけでセットを構成してください")
        masses = {entry["mass_amu"] for entry in selected if entry.get("mass_amu") is not None}
        if len(masses) > 1:
            raise ValueError("選択した断面積のガス質量 [amu] が一致しません")
        css = [CrossSection.from_dict(entry["cross_section"]) for entry in selected]
        mass = next(iter(masses), None)
        if mass is None:
            raise ValueError("このガスの質量が不明です。CSV 登録時にガス質量 [amu] を指定するか、LXCat の質量比 m/M を指定してください")
        if species == "electron":
            for kind in ("ELASTIC", "EFFECTIVE"):
                if sum(cs.projectile == "e" and cs.kind == kind for cs in css) > 1:
                    raise ValueError(f"セットに {kind} が複数あります。運動量移行断面積の二重計上を防ぐため 1 過程だけにしてください")
        if mass is not None:
            for cs in css:
                if cs.projectile == "e" and cs.kind in ("ELASTIC", "EFFECTIVE") and cs.mass_ratio is None:
                    cs.mass_ratio = mass_ratio_from_amu(mass)
        doc = LxcatDocument(databases=[LxcatDatabase(name=None, cross_sections=css)], warnings=list(item.get("warnings", [])))
        processes, warnings = to_v1_processes(doc, species)
        if not processes:
            raise ValueError("選択した断面積に、この計算で利用できる衝突過程がありません")
        return {"gas": next(iter(gases)), "mass_amu": mass, "species": species, "set_id": item["id"], "set_name": item["name"],
                "processes": [process.model_dump() for process in processes], "warnings": warnings,
                "registry_ids": [entry["id"] for entry in selected],
                "sources": [{"id": entry["id"], "set_id": item["id"], "set_name": item["name"],
                             "source": entry["source"], "source_url": entry.get("source_url", "")} for entry in selected]}
