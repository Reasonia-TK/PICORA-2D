"""PICORA-2D: PIC、流体 2D、流体係数用 Boltzmann の実行。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ..batch import _build_results_bundle
from ..boltz import DEFAULT_BOLTZ_OPTS, _hash_processes, boltzpm_available, run_boltz_sweep
from ..device import cuda_status
from ..fluid2d import build_fluid2d_result
from ..gfluid import make_fluid2d_simulation
from ..gpic import make_pic_simulation
from ..pic import WALK_DIAG_KEYS
from ..pic1d_presets import edupic_ar_processes
from ..project2d import Project2d
from ..schema import XsProcess
from .manager import Emitter


def _project(project: dict | None) -> Project2d:
    if project is None:
        raise ValueError("project を指定してください")
    return Project2d.model_validate(project)


def _device(options: dict) -> str:
    value = options.get("device", "auto")
    if value not in ("auto", "cuda", "cpu"):
        raise ValueError("options.device は auto / cuda / cpu から指定してください")
    return value


def _steps(options: dict, default: int) -> int:
    value = options.get("steps", default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("steps は正の整数を指定してください")
    return value


class BaseRunner:
    kind = ""
    continuable = False
    keep_state = False

    def started(self, state: Any) -> dict:
        return {}

    def prepare_continue(self, state: Any, options: dict) -> None:
        raise ValueError("この計算は続きを実行できません")

    def release(self, state: Any) -> None:
        pass


@dataclass
class SimState:
    sim: Any
    step_offset: int = 0


def _frame_sink(st: SimState, emit: Emitter) -> Callable[[dict], None]:
    conv = getattr(st.sim, "conv", None)
    if conv is None:
        return emit.frame

    def sink(frame: dict) -> None:
        if conv.run_end is not None:
            frame = {**frame, "n_steps": conv.run_end - st.step_offset}
        emit.frame(frame)

    return sink


class PicRunner(BaseRunner):
    kind = "pic"
    continuable = True

    def validate(self, project, options):
        p = _project(project)
        if p.pic is None:
            raise ValueError("project.pic が指定されていません")
        if _device(options) == "cpu":
            raise ValueError("2D PIC は CUDA GPU が必要です")
        ok, info = cuda_status()
        if not ok:
            raise ValueError(f"2D PIC は CUDA GPU が必要です ({info})")
        return p

    def build(self, p, options, ctx) -> SimState:
        return SimState(make_pic_simulation(p, device="cuda"))

    def started(self, st: SimState) -> dict:
        sim = st.sim
        st.step_offset = sim.step_count
        return {
            "dt": sim.dt, "n_steps": sim.pic.n_steps, "step_offset": sim.step_count,
            "effective_threads": sim.effective_threads, "warnings": sim.warnings,
            "device": sim.device.kind,
            "mesh": {"nodes": sim.mesh.nodes.tolist(), "triangles": sim.mesh.triangles.tolist()},
        }

    def run(self, st: SimState, emit: Emitter, should_stop) -> dict:
        sim = st.sim
        t0 = time.perf_counter()
        sim.run_batch(_frame_sink(st, emit), should_stop, False)
        result = _build_results_bundle(sim, st.step_offset, time.perf_counter() - t0)["pic"]
        result["frame"] = emit.last_frame
        timing = {k: v for k, v in sim.timing.items() if k not in WALK_DIAG_KEYS}
        result["timing"] = {**sim.timing, "total": sum(timing.values())}
        if getattr(sim, "mesh_version", 0) > 0:
            result["regrids"] = sim.regrid_log
        return result

    def prepare_continue(self, st, options):
        sim = st.sim
        sim.prepare_continue(_steps(options, sim.pic.n_steps), options.get("frame_every"),
                             options.get("avg_steps"), options.get("phase_bins"))


class Fluid2dRunner(BaseRunner):
    kind = "fluid2d"
    continuable = True

    def validate(self, project, options):
        p = _project(project)
        if p.fluid2d is None:
            raise ValueError("project.fluid2d が指定されていません")
        settings = p.fluid2d
        if settings.electron_model == "boltzmann":
            processes = settings.electron_processes or edupic_ar_processes()[0]
            if settings.boltz_table.source_hash != _hash_processes(processes):
                raise ValueError("断面積が Boltzmann 係数表の生成時から変わっています。係数表を再生成してください")
        device = _device(options)
        if device == "cuda":
            if p.fluid2d.linear_solver == "direct":
                raise ValueError("CUDA 流体計算では linear_solver='iterative' を指定してください")
            ok, info = cuda_status()
            if not ok:
                raise ValueError(f"CUDA GPU を使用できません ({info})")
        return p

    def build(self, p, options, ctx) -> SimState:
        return SimState(make_fluid2d_simulation(p, device=_device(options)))

    @staticmethod
    def _mesh(sim) -> dict:
        if hasattr(sim, "mesh_payload"):
            return sim.mesh_payload()
        return {"nodes": sim.mesh.nodes.tolist(), "triangles": sim.mesh.triangles.tolist()}

    def started(self, st) -> dict:
        sim = st.sim
        st.step_offset = sim.step_count
        return {
            "n_steps": sim.s.n_steps, "step_offset": sim.step_count, "dt": sim.dt,
            "warnings": sim.warnings, "effective_threads": sim.effective_threads,
            "device": sim._device.kind, "mesh": self._mesh(sim),
        }

    def run(self, st, emit, should_stop) -> dict:
        t0 = time.perf_counter()
        st.sim.run_batch(_frame_sink(st, emit), should_stop, False)
        out = build_fluid2d_result(st.sim, time.perf_counter() - t0)
        out["mesh"] = self._mesh(st.sim)
        return out

    def prepare_continue(self, st, options):
        sim = st.sim
        sim.prepare_continue(_steps(options, sim.s.n_steps), options.get("frame_every"),
                             options.get("avg_steps"), options.get("phase_bins"))


@dataclass
class BoltzInput:
    processes: list
    mass_amu: float
    p_pa: float
    t_k: float
    opts: dict | None


class BoltzRunner(BaseRunner):
    kind = "boltz"

    def validate(self, project, options) -> BoltzInput:
        if not boltzpm_available():
            raise ValueError("boltzpmp がインストールされていません")
        opts = options.get("opts")
        if options.get("processes") is not None:
            processes = [XsProcess.model_validate(x) for x in options["processes"]]
            if not processes:
                raise ValueError("電子衝突断面積を指定してください")
            mass, pressure, temperature = (float(options["mass_amu"]), float(options["p_pa"]),
                                           float(options.get("t_k", 300.0)))
        else:
            if options.get("module") != "fluid2d":
                raise ValueError("project と module='fluid2d'、または processes/mass_amu/p_pa を指定してください")
            p = _project(project)
            s = p.fluid2d
            if s is None:
                raise ValueError("project.fluid2d が指定されていません")
            processes = s.electron_processes or edupic_ar_processes()[0]
            mass, pressure, temperature = s.ion_mass_amu, s.gas_pressure_pa, s.gas_temperature_k
        if min(mass, pressure, temperature) <= 0:
            raise ValueError("質量、圧力、温度は正の値で指定してください")
        return BoltzInput(processes, mass, pressure, temperature, opts)

    def build(self, inp, options, ctx):
        return inp

    def started(self, inp) -> dict:
        return {"n_steps": int((inp.opts or {}).get("n_points") or DEFAULT_BOLTZ_OPTS["n_points"])}

    def run(self, inp, emit, should_stop) -> dict:
        def on_progress(i, n, en_td, elapsed_s):
            emit.progress(i, n, en_td=en_td, elapsed_s=elapsed_s)

        table = run_boltz_sweep(inp.processes, inp.mass_amu, inp.p_pa, inp.t_k,
                               inp.opts, on_progress, should_stop)
        return {"table": table}


def default_runners() -> list[BaseRunner]:
    return [PicRunner(), Fluid2dRunner(), BoltzRunner()]
