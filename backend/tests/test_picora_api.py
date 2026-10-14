"""限定公開 API と、ジョブ経路を通した実際の CPU/CUDA 計算を確認。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from es_sim.device import cuda_available
from es_sim.selftest import sample_project
from es_sim.server import app


def project():
    return sample_project().model_dump(mode="json")


def await_job(client, job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/v2/jobs/{job_id}").json()
        if job["state"] in ("done", "stopped", "error", "cancelled"):
            return job
        time.sleep(0.02)
    pytest.fail("計算ジョブが 30 秒で完了しませんでした")


def test_schema_and_capabilities_expose_only_2d_solvers():
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["name"] == "PICORA-2D"
    assert health["capabilities"]["solvers"] == ["pic", "fluid2d"]
    assert health["capabilities"]["mesh"] == ["cartesian"]
    schema = client.get("/v2/schema").json()["project"]
    assert {"pic", "fluid2d", "geometry", "mesh"} <= schema["properties"].keys()
    for field in ("pic1d", "fluid1d", "particles", "dsmc", "tl"):
        assert field not in schema["properties"]
    mesh = schema["$defs"]["CartesianMeshSettings"]["properties"]
    assert mesh["mode"]["const"] == "cartesian"
    assert client.get("/v2/jobs").json()["kinds"] == ["pic", "fluid2d", "boltz"]


@pytest.mark.parametrize("kind", ["pic1d", "fluid1d", "dsmc", "trace", "tl", "sweep"])
def test_unextracted_jobs_are_rejected(kind):
    response = TestClient(app).post("/v2/jobs", json={"kind": kind, "project": project()})
    assert response.status_code == 400


@pytest.mark.parametrize("mode", ["unstructured", "structured"])
def test_non_cartesian_mesh_is_rejected(mode):
    p = project()
    p["mesh"]["mode"] = mode
    client = TestClient(app)
    assert client.post("/mesh", json=p).status_code == 422
    assert client.post("/v2/jobs", json={"kind": "fluid2d", "project": p}).status_code == 422


@pytest.mark.parametrize("field", ["pic1d", "fluid1d", "dsmc", "particles", "tl"])
def test_legacy_module_payload_is_rejected(field):
    p = project()
    p[field] = {}
    assert TestClient(app).post("/mesh", json=p).status_code == 422


def test_embedded_boundary_mesh_and_field_preview():
    client = TestClient(app)
    p = project()
    mesh = client.post("/mesh", json=p)
    assert mesh.status_code == 200, mesh.text
    assert mesh.json()["nodes"] and mesh.json()["triangles"]
    result = client.post("/solve", json=p)
    assert result.status_code == 200, result.text
    assert all(value == 0.0 for value in result.json()["v"])
    profile = client.post("/profile", json={"project": p, "p1": [0, 0.005], "p2": [0.01, 0.005], "n": 20})
    assert profile.status_code == 200, profile.text
    assert len(profile.json()["s"]) == 20


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_fluid2d_jobs_and_continue(device):
    if device == "cuda" and not cuda_available():
        pytest.skip("CUDA GPU がありません")
    client = TestClient(app)
    response = client.post("/v2/jobs", json={"kind": "fluid2d", "project": project(), "options": {"device": device}})
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    try:
        job = await_job(client, job_id)
        assert job["state"] == "done", job
        assert job["can_continue"]
        started = client.get(f"/v2/jobs/{job_id}/started").json()
        assert started["device"] == device
        result = client.get(f"/v2/jobs/{job_id}/result").json()
        assert len(result["history"]["t"]) == 4
        assert result["mesh"]["triangles"]
        resumed = client.post(f"/v2/jobs/{job_id}/continue", json={"options": {"steps": 2}})
        assert resumed.status_code == 200, resumed.text
        assert await_job(client, job_id)["state"] == "done"
        continued = client.get(f"/v2/jobs/{job_id}/result").json()["history"]["t"]
        assert len(continued) == 2
        assert continued[-1] == pytest.approx(6e-12)
    finally:
        client.delete(f"/v2/jobs/{job_id}")


@pytest.mark.skipif(not cuda_available(), reason="CUDA GPU がありません")
def test_pic_cuda_job_delivers_results():
    client = TestClient(app)
    response = client.post("/v2/jobs", json={"kind": "pic", "project": project(), "options": {"device": "cuda"}})
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    try:
        job = await_job(client, job_id)
        assert job["state"] == "done", job
        assert client.get(f"/v2/jobs/{job_id}/started").json()["device"] == "cuda"
        result = client.get(f"/v2/jobs/{job_id}/result").json()
        assert len(result["history"]) == 4
        assert result["fields"]["phi"]
        assert result["frame"]["step"] == 4
    finally:
        client.delete(f"/v2/jobs/{job_id}")


def test_explicit_cuda_request_never_falls_back(monkeypatch):
    import es_sim.jobs.runners as runners

    monkeypatch.setattr(runners, "cuda_status", lambda: (False, "test GPU unavailable"))
    client = TestClient(app)
    for kind in ("pic", "fluid2d"):
        response = client.post("/v2/jobs", json={"kind": kind, "project": project(), "options": {"device": "cuda"}})
        assert response.status_code == 422
        assert "GPU" in response.json()["detail"]


def test_pic_rejects_cpu_and_mcc_dsmc():
    client = TestClient(app)
    p = project()
    assert client.post("/v2/jobs", json={"kind": "pic", "project": p, "options": {"device": "cpu"}}).status_code == 422
    p["pic"]["mcc"] = {"gas": {"name": "Ar", "pressure_pa": 10.0}, "use_dsmc_gas": True}
    assert client.post("/v2/jobs", json={"kind": "pic", "project": p}).status_code == 422


def test_non_rectangular_domain_is_rejected_before_job_creation():
    p = project()
    p["geometry"]["domain"]["polygon"] = [[0, 0], [0.01, 0], [0.009, 0.01], [0, 0.01]]
    response = TestClient(app).post("/v2/jobs", json={"kind": "fluid2d", "project": p})
    assert response.status_code == 422
    assert "矩形 domain" in response.json()["detail"]


def test_stale_boltzmann_table_is_rejected_before_job_creation():
    from es_sim.boltz import _hash_processes
    from es_sim.jobs.runners import Fluid2dRunner
    from es_sim.pic1d_presets import edupic_ar_processes

    p = project()
    p["fluid2d"]["electron_model"] = "boltzmann"
    p["fluid2d"]["boltz_table"] = {
        "en_td": [1, 100], "mean_energy_ev": [1, 10], "mobility_n": [1e24, 1e24],
        "k_ion": [0, 0], "k_exc": [0, 0], "e_ion_ev": [15.76, 15.76],
        "e_exc_ev": [11.5, 11.5], "eedf_eps_ev": [0, 1], "eedf": [[0, 0], [0, 0]],
        "source_hash": "different cross sections",
    }
    response = TestClient(app).post("/v2/jobs", json={"kind": "fluid2d", "project": p})
    assert response.status_code == 422
    assert "再生成" in response.json()["detail"]
    p["fluid2d"]["boltz_table"]["source_hash"] = _hash_processes(edupic_ar_processes()[0])
    Fluid2dRunner().validate(p, {"device": "cpu"})


@pytest.mark.parametrize("field,value", [
    ("injection", {"emitter": {"kind": "point", "p1": [0.005, 0.005], "n": 10}, "species": "electron", "current_a_per_m": 1e-4}),
    ("fn", {"edges": [0]}),
    ("merge", {"n_max": 1000, "every": 10}),
])
def test_unsupported_gpu_pic_options_are_rejected_before_job_creation(field, value):
    p = project()
    p["pic"][field] = value
    response = TestClient(app).post("/v2/jobs", json={"kind": "pic", "project": p})
    assert response.status_code == 422
    assert field in response.json()["detail"]
