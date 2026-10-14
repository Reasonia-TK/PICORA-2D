"""配布版の直交格子 + EB、2D PIC、2D 流体と CUDA 実行を確認する。"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from . import __version__
from .device import cuda_status
from .field import solve_electrostatic
from .gfluid import make_fluid2d_simulation
from .gpic import make_pic_simulation
from .project2d import Project2d


def sample_project() -> Project2d:
    return Project2d.model_validate({
        "geometry": {
            "domain": {"polygon": [[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01]]},
            "boundaries": [{"edges": [0, 1, 2, 3], "type": "dirichlet", "voltage": 0.0}],
            "regions": [{"id": "pin", "type": "conductor", "voltage": 0.0,
                         "shape": {"kind": "circle", "center": [0.0064, 0.0063], "radius": 0.0008}}],
        },
        "mesh": {"size": 0.001, "mode": "cartesian"},
        "pic": {"initial_plasma": {"density": 1e13, "te_ev": 1.0, "ti_ev": 0.026,
                                    "ion_mass_amu": 39.948, "seed": 1},
                "n_macro": 512, "dt": 1e-12, "n_steps": 4, "frame_every": 2, "avg_steps": 2},
        "fluid2d": {"init_density_m3": 1e13, "init_te_ev": 1.0, "gas_pressure_pa": 10.0,
                    "dt": 1e-12, "n_steps": 4, "frame_every": 2, "avg_steps": 2,
                    "linear_solver": "iterative"},
    })


def run_selftest(require_gpu=False, no_gpu=False) -> dict:
    report = {"name": "PICORA-2D", "version": __version__, "checks": []}

    def check(name, callback):
        t0 = time.perf_counter()
        try:
            info = callback()
            report["checks"].append({"name": name, "status": "ok", "detail": info,
                                     "elapsed_s": time.perf_counter() - t0})
        except Exception as exc:
            report["checks"].append({"name": name, "status": "error", "detail": f"{type(exc).__name__}: {exc}"})

    project = sample_project()

    def poisson():
        sol = solve_electrostatic(project, device="cpu")
        if not np.isfinite(sol.phi).all():
            raise RuntimeError("Poisson の電位が有限値でありません")
        return "CPU Cartesian + embedded boundary Poisson"

    def fluid(device):
        sim = make_fluid2d_simulation(project.model_copy(deep=True), device=device)
        sim.run_batch(store_frames=False)
        if sim.step_count != 4 or not np.isfinite(sim.n_e).all():
            raise RuntimeError("2D 流体のステップ数または密度が不正です")
        return f"{sim.__class__.__name__}: {sim.step_count} steps"

    def pic():
        sim = make_pic_simulation(project.model_copy(deep=True), device="cuda")
        sim.run_batch(store_frames=False)
        if sim.step_count != 4 or not np.isfinite(sim.fields["phi"]).all():
            raise RuntimeError("2D PIC のステップ数または電位が不正です")
        return f"{sim.__class__.__name__}: {sim.step_count} steps"

    check("poisson_cpu", poisson)
    check("fluid2d_cpu", lambda: fluid("cpu"))
    available, info = cuda_status() if not no_gpu else (False, "--no-gpu")
    report["gpu"] = {"available": available, "detail": info}
    if available:
        check("pic_cuda", pic)
        check("fluid2d_cuda", lambda: fluid("cuda"))
    elif require_gpu:
        report["checks"].append({"name": "cuda", "status": "error", "detail": info})
    else:
        report["checks"].append({"name": "cuda", "status": "skipped", "detail": info})
    report["ok"] = not any(item["status"] == "error" for item in report["checks"])
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PICORA-2D 2D solver self-test")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--require-gpu", action="store_true")
    group.add_argument("--no-gpu", action="store_true")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    report = run_selftest(args.require_gpu, args.no_gpu)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
