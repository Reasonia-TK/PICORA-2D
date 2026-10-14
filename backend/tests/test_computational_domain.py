"""領域追従設定をバックエンド往復で維持し、実際の EB 計算へ渡す。"""

from __future__ import annotations

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from es_sim.device import cuda_available
from es_sim.project2d import Project2d
from es_sim.selftest import sample_project
from es_sim.server import app


def test_legacy_project_preserves_explicit_domain():
    raw = sample_project().model_dump(mode="json")
    raw["geometry"].pop("computational_domain", None)
    loaded = Project2d.model_validate(raw)
    assert loaded.geometry.computational_domain is None
    assert loaded.model_dump(mode="json")["geometry"]["domain"] == raw["geometry"]["domain"]


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_domain_settings_survive_round_trip(mode):
    raw = sample_project().model_dump(mode="json")
    settings = {"mode": mode, "margin": 0.005}
    raw["geometry"]["computational_domain"] = settings
    loaded = Project2d.model_validate(raw)
    exported = loaded.model_dump(mode="json")
    assert exported["geometry"]["computational_domain"] == settings
    assert Project2d.model_validate_json(loaded.model_dump_json()).geometry.computational_domain == loaded.geometry.computational_domain


@pytest.mark.parametrize("settings", [
    {"mode": "invalid", "margin": 0.005},
    {"mode": "auto", "margin": -0.001},
    {"mode": "auto", "margin": float("inf")},
    {"mode": "auto", "margin": float("nan")},
])
def test_invalid_domain_settings_are_rejected(settings):
    raw = sample_project().model_dump(mode="json")
    raw["geometry"]["computational_domain"] = settings
    with pytest.raises(ValidationError):
        Project2d.model_validate(raw)


def test_auto_domain_metadata_accepts_embedded_boundary_mesh_and_solve():
    raw = sample_project().model_dump(mode="json")
    # UI がパーツの移動に追従して配置した domain。余白を含めた数値輪郭をそのまま使う。
    raw["geometry"]["computational_domain"] = {"mode": "auto", "margin": 0.001}
    raw["geometry"]["domain"]["polygon"] = [[0.02, 0.03], [0.03, 0.03], [0.03, 0.04], [0.02, 0.04]]
    raw["geometry"]["regions"] = [{"id": "electrode", "type": "conductor", "voltage": 0,
                                      "shape": {"kind": "circle", "center": [0.025, 0.035], "radius": 0.001}}]
    client = TestClient(app)
    response = client.post("/mesh", json=raw)
    assert response.status_code == 200, response.text
    nodes = response.json()["nodes"]
    assert nodes
    assert min(x for x, _ in nodes) == pytest.approx(0.02)
    assert max(x for x, _ in nodes) == pytest.approx(0.03)
    response = client.post("/solve", json=raw)
    assert response.status_code == 200, response.text
    assert all(v == 0 for v in response.json()["v"])


def test_public_schema_describes_domain_tracking_settings():
    schema = TestClient(app).get("/v2/schema").json()["project"]
    assert "computational_domain" in schema["$defs"]["Geometry"]["properties"]
    props = schema["$defs"]["ComputationalDomain"]["properties"]
    assert props["mode"]["enum"] == ["auto", "manual"]
    assert props["margin"]["minimum"] == 0


@pytest.mark.skipif(not cuda_available(), reason="CUDA GPU がありません")
@pytest.mark.parametrize("kind", ["pic", "fluid2d"])
@pytest.mark.parametrize("coord", ["xy", "rz", "rz_x0"])
def test_gpu_jobs_use_explicit_shifted_domain_with_auto_metadata(kind, coord):
    """auto は保存メタデータ。GPU 計算は UI が明示した輪郭をそのまま使う。"""
    raw = sample_project().model_dump(mode="json")
    raw["coord"] = coord
    # 部品の外接矩形 + margin はこの輪郭と異なる。backend が自動配置すると検査に失敗する。
    raw["geometry"]["computational_domain"] = {"mode": "auto", "margin": 0.001}
    bounds = {
        "xy": (0.02, 0.03, 0.03, 0.04),
        "rz": (0.02, 0.0, 0.03, 0.01),
        "rz_x0": (0.0, 0.03, 0.01, 0.04),
    }
    x0, y0, x1, y1 = bounds[coord]
    raw["geometry"]["domain"]["polygon"] = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    center = [(x0 + x1) / 2, (y0 + y1) / 2]
    raw["geometry"]["regions"] = [
        {"id": "electrode", "type": "conductor", "voltage": 0,
         "shape": {"kind": "circle", "center": center, "radius": 0.001}},
        {"id": "insulator", "type": "dielectric", "eps_r": 3,
         "polygon": [[x0 + 0.002, y0 + 0.001], [x0 + 0.003, y0 + 0.001],
                     [x0 + 0.003, y0 + 0.002], [x0 + 0.002, y0 + 0.002]]},
    ]
    axis_edge = {"xy": None, "rz": 0, "rz_x0": 3}[coord]
    raw["geometry"]["boundaries"] = [
        {"edges": [edge], "type": "symmetry" if edge == axis_edge else "dirichlet", "voltage": 0.0}
        for edge in range(4)
    ]
    client = TestClient(app)
    response = client.post("/v2/jobs", json={"kind": kind, "project": raw, "options": {"device": "cuda"}})
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = client.get(f"/v2/jobs/{job_id}").json()
            if job["state"] in ("done", "stopped", "error", "cancelled"):
                break
            time.sleep(0.02)
        else:
            pytest.fail("GPU ジョブが 30 秒以内に完了しませんでした")
        assert job["state"] == "done", job
        assert job["project"]["geometry"]["computational_domain"] == {"mode": "auto", "margin": 0.001}
        started = client.get(f"/v2/jobs/{job_id}/started").json()
        assert started["device"] == "cuda"
        result = client.get(f"/v2/jobs/{job_id}/result").json()
        result_mesh = result["started"]["mesh"] if kind == "pic" else result["mesh"]
        for mesh in (started["mesh"], result_mesh):
            nodes = np.asarray(mesh["nodes"])
            assert nodes.shape[1] == 2 and mesh["triangles"]
            np.testing.assert_allclose(nodes.min(axis=0), [x0, y0], atol=1e-14)
            np.testing.assert_allclose(nodes.max(axis=0), [x1, y1], atol=1e-14)
        assert np.isfinite(np.asarray(result["fields"]["phi"])).all()
        history = result["history"] if kind == "pic" else result["history"]["t"]
        assert len(history) == 4
    finally:
        client.delete(f"/v2/jobs/{job_id}")
