"""セット登録の永続化・非破壊移行・全過程適用。断面積は合成検査データ。"""
import copy
import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from es_sim.xs.registry import CrossSectionRegistry, parse_csv_cross_section
from es_sim.xs.registry_api import make_registry_router

CSV = "energy[eV],sigma[m2]\n0,1e-20\n10,2e-20\n100,1e-20\n"
LXCAT = """DATABASE: SYNTHETIC TEST ONLY
ELASTIC
Ar
1.373e-5
PROCESS: synthetic elastic test
-----
0 1e-20
10 2e-20
100 1e-20
-----
IONIZATION
Ar -> Ar^+
15.76
PROCESS: synthetic ionization test
-----
0 0
15.76 0
30 1e-21
100 2e-21
-----
"""


def register_elastic(registry, gas="Ar", name="合成試験セット"):
    cs = parse_csv_cross_section(CSV, gas=gas, kind="ELASTIC")
    return registry.register([cs], source="合成検査データ。物理計算用ではありません", set_name=name)


def client_for(path):
    app = FastAPI()
    app.include_router(make_registry_router(path))
    return TestClient(app)


@pytest.mark.parametrize("csv,match", [
    ("energy[eV],sigma[cm2]\n0,1\n1,2", "単位"),
    ("energy[keV],sigma[m2]\n0,1\n1,2", "単位"),
    ("energy[eV],sigma[m2]\n0,nan\n1,2", "有限"),
    ("energy[eV],sigma[m2]\n0,1e999\n1,2", "有限"),
    ("energy[eV],sigma[m2]\n-1,0\n1,2", "0 以上"),
    ("energy[eV],sigma[m2]\n0,-1\n1,2", "0 以上"),
    ("energy[eV],sigma[m2]\n2,1\n1,2", "昇順"),
    ("energy[eV],sigma[m2]\n1,1\n1,2", "昇順"),
    ("energy[eV],sigma[m2]\n0,1", "2〜"),
    ("energy[eV],sigma[m2]\n0,1,3\n1,2,4", "2 列"),
    ('energy[eV],sigma[m2]\n0,"broken', "引用符"),
])
def test_csv_rejects_invalid_numeric_tables(csv, match):
    with pytest.raises(ValueError, match=match):
        parse_csv_cross_section(csv, gas="Ar", kind="ELASTIC")


@pytest.mark.parametrize("kind,threshold,match", [
    ("ELASTIC", 2, "閾値.*0"), ("IONIZATION", 0, "正の"),
    ("EXCITATION", float("nan"), "有限"), ("IONIZATION", 200, "上限"),
    ("IONIZATION", 5, "閾値未満"),
])
def test_csv_rejects_inconsistent_threshold(kind, threshold, match):
    with pytest.raises(ValueError, match=match):
        parse_csv_cross_section(CSV, gas="Ar", kind=kind, threshold_ev=threshold)


def test_lxcat_all_processes_form_one_named_set_and_survive_restart(tmp_path):
    path = tmp_path / "project-data" / "xs.json"
    registry = CrossSectionRegistry(path)
    assert registry.list()["sets"] == []
    result = registry.import_lxcat(LXCAT, source="合成試験用", source_url="https://example.org/test", notes="実断面積ではない", set_name="Ar 試験セット")
    item = result["set"]
    assert result["added"] == 2 and len(item["entries"]) == 2
    assert len(registry.list()["sets"]) == 1
    reloaded = CrossSectionRegistry(path)
    assert reloaded.list()["sets"] == [item]
    resolved = reloaded.resolve(item["id"], "electron")
    assert [p["kind"] for p in resolved["processes"]] == ["elastic", "ionization"]
    assert resolved["processes"][1]["threshold_ev"] == 15.76
    np.testing.assert_allclose(resolved["processes"][0]["sigma_m2"], [1e-20, 2e-20, 1e-20])
    assert resolved["sources"][0]["set_name"] == "Ar 試験セット"
    assert resolved["sources"][0]["source"] == "合成試験用"
    assert item["entries"][0]["cross_section"]["database"] == "SYNTHETIC TEST ONLY"
    reloaded.rename(item["id"], "名前変更後")
    assert registry.resolve(item["id"], "electron")["set_name"] == "名前変更後"
    assert registry.delete([item["id"]]) == 1
    assert registry.list()["sets"] == []
    # resolve 応答は削除後も独立した計算入力として利用できる。
    assert resolved["processes"][1]["sigma_m2"][2] == 1e-21
    with pytest.raises(ValueError, match="存在しません"):
        registry.resolve(item["id"], "electron")
    assert not list(path.parent.glob("*.tmp"))


def test_lxcat_and_csv_batch_are_atomic_on_error(tmp_path):
    registry = CrossSectionRegistry(tmp_path / "xs.json")
    item = register_elastic(registry)
    before = registry.path.read_bytes()
    with pytest.raises(ValueError):
        registry.import_lxcat(LXCAT.replace("15.76\nPROCESS", "-15.76\nPROCESS"), source="試験", set_id=item["id"])
    with pytest.raises(ValueError, match="CSV 2"):
        registry.import_csv_batch([{"text": CSV, "gas": "Ar", "kind": "ISOTROPIC", "projectile": "Ar^+"}, {"text": CSV.replace("m2", "cm2"), "gas": "Ar", "kind": "ELASTIC"}], source="試験", set_id=item["id"])
    assert registry.path.read_bytes() == before


def test_csv_batch_appends_electron_and_ion_to_same_set(tmp_path):
    registry = CrossSectionRegistry(tmp_path / "xs.json")
    item = register_elastic(registry)
    result = registry.import_csv_batch([
        {"text": CSV, "filename": "isotropic.csv", "gas": "Ar", "kind": "ISOTROPIC", "projectile": "Ar^+"},
        {"text": CSV, "filename": "backscat.csv", "gas": "Ar", "kind": "BACKSCAT", "projectile": "Ar^+"},
    ], source="別の合成試験出典", set_id=item["id"])
    assert result["added"] == 2 and len(result["set"]["entries"]) == 3
    assert len(registry.list()["sets"]) == 1
    assert len(registry.resolve(item["id"], "electron")["processes"]) == 1
    ion = registry.resolve(item["id"], "ion")
    assert [p["kind"] for p in ion["processes"]] == ["isotropic", "backscat"]
    assert ion["sources"][1]["source"] == "別の合成試験出典"


def test_copy_preserves_sources_and_remove_only_changes_destination(tmp_path):
    registry = CrossSectionRegistry(tmp_path / "xs.json")
    item = registry.import_lxcat(LXCAT, source="合成試験出典", set_name="コピー元")["set"]
    entry_ids = [entry["id"] for entry in item["entries"]]
    copied = registry.copy_entries(item["id"], entry_ids, set_name="コピー先")["set"]
    assert copied["entries"][0]["id"] != entry_ids[0]
    assert copied["entries"][0]["origin"] == {"set_id": item["id"], "entry_id": entry_ids[0]}
    assert copied["entries"][0]["source"] == "合成試験出典"
    registry.remove_entries(copied["id"], [copied["entries"][1]["id"]])
    assert len(registry.list()["sets"][0]["entries"]) == 2
    assert len(registry.list()["sets"][1]["entries"]) == 1
    with pytest.raises(ValueError, match="最後の過程"):
        registry.remove_entries(copied["id"], [copied["entries"][0]["id"]])


def test_mixed_gas_and_duplicate_elastic_are_rejected_on_set_application(tmp_path):
    registry = CrossSectionRegistry(tmp_path / "xs.json")
    first = register_elastic(registry)
    second = register_elastic(registry, "He", "He 試験セット")
    registry.copy_entries(second["id"], [second["entries"][0]["id"]], set_id=first["id"])
    with pytest.raises(ValueError, match="同じガス"):
        registry.resolve(first["id"], "electron")
    duplicated = registry.copy_entries(second["id"], [second["entries"][0]["id"]], set_name="重複セット")["set"]
    registry.copy_entries(second["id"], [second["entries"][0]["id"]], set_id=duplicated["id"])
    with pytest.raises(ValueError, match="二重計上"):
        registry.resolve(duplicated["id"], "electron")


@pytest.mark.parametrize("kind,projectile,species", [("ELASTIC", "e", "electron"), ("ISOTROPIC", "Test^+", "ion")])
def test_unknown_gas_can_be_stored_but_mass_is_required_for_application(tmp_path, kind, projectile, species):
    registry = CrossSectionRegistry(tmp_path / "xs.json")
    cs = parse_csv_cross_section(CSV, gas="Unknown-test-gas", kind=kind, projectile=projectile)
    item = registry.register([cs], source="合成試験用", set_name="未知ガス")
    with pytest.raises(ValueError, match="ガスの質量が不明"):
        registry.resolve(item["id"], species)
    explicit = registry.register([cs], source="合成試験用", mass_amu=84, set_name="質量指定")
    assert registry.resolve(explicit["id"], species)["mass_amu"] == 84


def test_v1_migration_groups_original_import_and_preserves_exact_backup(tmp_path):
    path = tmp_path / "xs.json"
    registry = CrossSectionRegistry(path)
    imported = registry.import_lxcat(LXCAT, source="旧LXCat試験", set_name="一時セット")["set"]
    another = register_elastic(registry, "He", "別セット")
    entries = copy.deepcopy(imported["entries"] + another["entries"])
    # 旧バージョンの同じ LXCat 登録は全プロセスが同じ created_at を持つ。
    entries[0]["created_at"] = entries[1]["created_at"] = "2026-10-09T00:00:00Z"
    entries[2]["created_at"] = "2026-10-09T00:01:00Z"
    original = json.dumps({"version": 1, "entries": entries}, ensure_ascii=False).encode("utf-8")
    path.write_bytes(original)
    sets = registry.list()["sets"]
    assert len(sets) == 2 and len(sets[0]["entries"]) == 2
    assert [entry for item in sets for entry in item["entries"]] == entries
    assert path.with_name("xs.json.v1.bak").read_bytes() == original
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2
    assert CrossSectionRegistry(path).list()["sets"] == sets
    assert registry.resolve(sets[0]["id"], "electron")["processes"][1]["threshold_ev"] == 15.76


def test_invalid_old_registry_is_not_migrated_or_overwritten(tmp_path):
    path = tmp_path / "xs.json"
    original = b'{"version":1,"entries":[{"id":"bad"}]}'
    path.write_bytes(original)
    with pytest.raises(ValueError):
        CrossSectionRegistry(path).list()
    assert path.read_bytes() == original
    assert not path.with_name("xs.json.v1.bak").exists()


def test_bad_storage_does_not_get_overwritten(tmp_path):
    path = tmp_path / "xs.json"
    path.write_text('{"version":999,"entries":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="未対応"):
        register_elastic(CrossSectionRegistry(path))
    assert json.loads(path.read_text())["version"] == 999


def test_set_name_validation_and_unknown_destination_leave_storage_unchanged(tmp_path):
    registry = CrossSectionRegistry(tmp_path / "xs.json")
    item = register_elastic(registry)
    before = registry.path.read_bytes()
    for kwargs in ({"set_name": " "}, {"set_name": item["name"]}, {"set_id": "missing"}, {"set_name": "name", "set_id": item["id"]}):
        with pytest.raises(ValueError):
            registry.import_lxcat(LXCAT, source="試験", **kwargs)
        assert registry.path.read_bytes() == before


def test_api_import_rename_append_apply_copy_delete(tmp_path):
    client = client_for(tmp_path / "xs.json")
    payload = {"text": CSV, "gas": "Ar", "kind": "ELASTIC", "source": "合成試験用", "set_name": "API 試験セット"}
    for patch in ({"text": CSV.replace("m2", "cm2")}, {"source_url": "javascript:alert(1)"}, {"threshold_ev": -1}, {"set_name": ""}):
        assert client.post("/xs/registry/csv", json={**payload, **patch}).status_code == 422
    response = client.post("/xs/registry/csv", json=payload)
    assert response.status_code == 200
    item = response.json()["set"]
    set_id = item["id"]
    assert client.post("/xs/registry/rename", json={"set_id": set_id, "name": "変更後"}).json()["set"]["name"] == "変更後"
    appended = client.post("/xs/registry/csv/batch", json={"set_id": set_id, "source": "合成イオン出典", "files": [{"text": CSV, "gas": "Ar", "kind": "ISOTROPIC", "projectile": "Ar^+"} ]})
    assert appended.status_code == 200 and len(appended.json()["set"]["entries"]) == 2
    resolved = client.post("/xs/registry/resolve", json={"set_id": set_id, "species": "electron"})
    assert resolved.status_code == 200 and resolved.json()["set_name"] == "変更後"
    assert resolved.json()["processes"][0]["mass_ratio"] > 0
    copied = client.post("/xs/registry/copy", json={"source_set_id": set_id, "entry_ids": [item["entries"][0]["id"]], "set_name": "コピー"})
    assert copied.status_code == 200
    assert len(client.get("/xs/registry").json()["sets"]) == 2
    assert client.post("/xs/registry/delete", json={"set_ids": [set_id, copied.json()["set"]["id"]]}).json()["deleted"] == 2
    assert client.get("/xs/registry").json()["sets"] == []


def test_configured_storage_path(monkeypatch, tmp_path):
    path = tmp_path / "specified" / "cross_sections.json"
    monkeypatch.setenv("PICORA_XS_REGISTRY", str(path))
    register_elastic(CrossSectionRegistry())
    assert path.exists()
