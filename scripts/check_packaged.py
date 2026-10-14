"""凍結版の API と断面積永続化を、隔離したレジストリで確認する。"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "backend" / "dist" / "picora-2d-backend" / "picora-2d-backend.exe"


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PICORA-2D packaged API smoke test")
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    version = json.loads(ROOT.joinpath("ui/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]
    parser.add_argument("--expect-version", default=version)
    parser.add_argument("--require-gpu", action="store_true")
    args = parser.parse_args(argv)
    executable = args.exe.resolve()
    if not executable.is_file():
        parser.error(f"実行ファイルがありません: {executable}")
    runtime = (ROOT / ".runtime" / "packaged-smoke" / uuid.uuid4().hex).resolve()
    runtime.relative_to(ROOT.resolve())
    runtime.mkdir(parents=True)
    registry = runtime / "cross_sections.json"
    port = unused_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["PICORA_XS_REGISTRY"] = str(registry)
    process: subprocess.Popen | None = None
    report = {"executable": str(executable), "registry": str(registry), "port": port, "checks": []}

    def request(path, method="GET", body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=8) as response:
                status, payload = response.status, response.read()
        except HTTPError as exc:
            status, payload = exc.code, exc.read()
        return status, json.loads(payload) if payload else None

    def start(log):
        child = subprocess.Popen(
            [str(executable), "--host", "127.0.0.1", "--port", str(port)],
            cwd=executable.parent, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError(f"凍結版サーバーが終了しました: {child.returncode}")
                try:
                    status, health = request("/health")
                    if status == 200:
                        if health.get("name") != "PICORA-2D" or health.get("version") != args.expect_version:
                            raise RuntimeError(f"凍結版の名称または版が異なります: {health}")
                        if args.require_gpu and health.get("gpu") is not True:
                            raise RuntimeError(f"凍結版が GPU を認識しません: {health}")
                        return child, health
                except (URLError, TimeoutError, ConnectionError):
                    pass
                time.sleep(0.1)
            raise RuntimeError("凍結版サーバーの起動が 35 秒で完了しませんでした")
        except Exception:
            stop(child)
            raise

    try:
        with (runtime / "server.log").open("wb") as log:
            process, health = start(log)
            status, schema = request("/v2/schema")
            assert status == 200 and schema["version"] == args.expect_version, schema
            assert "computational_domain" in schema["project"]["$defs"]["Geometry"]["properties"], schema
            report["checks"].append({"name": "health", "version": health["version"], "gpu": health["gpu"],
                                     "device": health["v2"]["cuda_info"], "status": "ok"})
            status, listing = request("/xs/registry")
            assert status == 200 and listing["sets"] == [], listing
            assert Path(listing["storage_path"]).resolve() == registry, listing
            status, imported = request("/xs/registry/csv", "POST", {
                "text": "energy[eV],sigma[m2]\n0,1e-20\n10,2e-20\n100,1e-20\n",
                "gas": "Ar", "kind": "ELASTIC", "projectile": "e", "threshold_ev": 0,
                "mass_amu": 39.948, "source": "Isolated packaged smoke test; synthetic data only",
                "set_name": "Argon smoke set",
            })
            assert status == 200 and len(imported["set"]["entries"]) == 1, imported
            set_id = imported["set"]["id"]
            entry_id = imported["set"]["entries"][0]["id"]
            status, appended = request("/xs/registry/csv/batch", "POST", {
                "set_id": set_id, "source": "Isolated packaged smoke test; synthetic data only",
                "files": [{"text": "energy[eV],sigma[m2]\n0,0\n10,0\n100,1e-20\n",
                           "filename": "excitation.csv", "gas": "Ar", "kind": "EXCITATION",
                           "threshold_ev": 10, "mass_amu": 39.948, "projectile": "e"}],
            })
            assert status == 200 and len(appended["set"]["entries"]) == 2, appended
            status, renamed = request("/xs/registry/rename", "POST", {"set_id": set_id, "name": "Argon smoke renamed"})
            assert status == 200, renamed
            status, resolved = request("/xs/registry/resolve", "POST", {"set_id": set_id, "species": "electron"})
            assert status == 200 and resolved["gas"] == "Ar" and resolved["mass_amu"] == 39.948, resolved
            assert resolved["set_name"] == "Argon smoke renamed" and len(resolved["processes"]) == 2, resolved
            expected_ratio = 9.1093837015e-31 / (39.948 * 1.66053906660e-27)
            assert math.isclose(resolved["processes"][0]["mass_ratio"], expected_ratio, rel_tol=1e-12), resolved
            report["checks"].append({"name": "registry_named_set_batch_rename_resolve", "status": "ok", "set_id": set_id})
            status, rejected = request("/v2/jobs", "POST", {"kind": "dsmc", "project": {}})
            assert status == 400, rejected
            status, rejected = request("/v2/jobs", "POST", {
                "kind": "fluid2d",
                "project": {
                    "geometry": {"domain": {"polygon": [[0, 0], [0.01, 0], [0.009, 0.01], [0, 0.01]]}},
                    "mesh": {"mode": "cartesian", "size": 0.001},
                    "fluid2d": {"init_density_m3": 1e13, "gas_pressure_pa": 10.0},
                },
            })
            assert status == 422 and "矩形 domain" in rejected["detail"], rejected
            report["checks"].append({"name": "unsupported_job_and_domain_rejection", "status": "ok"})
            stop(process)
            process = None
            process, restarted_health = start(log)
            assert restarted_health["instance"] != health["instance"], restarted_health
            status, listing = request("/xs/registry")
            assert status == 200 and len(listing["sets"]) == 1, listing
            assert listing["sets"][0]["id"] == set_id, listing
            assert listing["sets"][0]["name"] == "Argon smoke renamed", listing
            assert len(listing["sets"][0]["entries"]) == 2, listing
            assert listing["sets"][0]["entries"][0]["id"] == entry_id, listing
            status, after_restart = request("/xs/registry/resolve", "POST", {"set_id": set_id, "species": "electron"})
            assert status == 200 and after_restart == resolved, after_restart
            report["checks"].append({"name": "registry_persistence_after_restart", "status": "ok"})
        report["ok"] = True
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop(process)
        report["process_stopped"] = process is None or process.poll() is not None
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    (runtime / "report.json").write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["ok"] and report["process_stopped"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
