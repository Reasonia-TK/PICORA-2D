"""2D PIC の結果を GUI 互換の JSON 形式に変換する。ES-Sim の変換を継承。"""

from __future__ import annotations

from typing import Any

import numpy as np

_DIAG_KEYS = (
    "t", "ke_e", "ke_i", "fe", "n_e", "n_i", "wall_e", "wall_i", "phi_min", "phi_max",
    "coll_e", "ion_events", "see_events", "surf_q", "fn_i", "fn_events", "merged", "split",
)


def _build_results_bundle(sim, step_offset: int, elapsed_s: float) -> dict:
    """結果付き保存の pic (ui/src/results/bundle.ts、v1 の ResultsBundle.pic) と同じ形の dict を組み立てる。

    server.py の _stream_run が started/done メッセージを組み立てる変換をそのまま流用する。
    history は pic.py が返す「列ごとの辞書」なので、frontend の toDiagArray 相当の
    変換 (列→行) をここで行う (バッチ出力は GUI の picHistory state と同じ「行の配列」形式)。
    elapsed_s は run_batch の壁時計秒 (server.py done メッセージの elapsed_s と同じ計測対象、prompts/86)。
    """
    started = {
        "type": "started",
        "dt": sim.dt,
        "n_steps": sim.pic.n_steps,
        "effective_threads": sim.effective_threads,
        "step_offset": step_offset,
        "warnings": sim.warnings,
        "mesh": {
            "nodes": sim.mesh.nodes.tolist(),
            "triangles": sim.mesh.triangles.tolist(),
        },
    }

    n_rows = len(sim.history.get("t", []))
    history = [{k: sim.history[k][i] for k in _DIAG_KEYS if k in sim.history} for i in range(n_rows)]

    fields = None
    if sim.fields is not None:
        fields = {
            k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in sim.fields.items()
        }

    cycle = None
    if sim.cycle is not None:
        def _f32(arr: Any) -> list:
            return np.asarray(arr, dtype=np.float32).tolist()

        c = sim.cycle
        cycle = {
            "bins": c["bins"],
            "period_s": c["period_s"],
            "phi": _f32(c["phi"]),
            "n_e": _f32(c["n_e"]),
            "n_i": _f32(c["n_i"]),
            "e_abs": _f32(c["e_abs"]),
            "te_ev": _f32(c["te_ev"]),
            "ion_rate": _f32(c["ion_rate"]),
            "particles": {
                name: [_f32(s) for s in snaps] for name, snaps in c["particles"].items()
            },
        }

    # ResultsBundle.pic.collectors は必須の配列 (undefined 不可) なので未使用時は空配列にする
    collectors: list[dict] = []
    if sim.collector_results is not None:
        for cr in sim.collector_results:
            collectors.append(
                {
                    "count": cr["count"],
                    "total_weight": cr["total_weight"],
                    "energies_ev": cr["energies_ev"].tolist(),
                    "angles_deg": cr["angles_deg"].tolist(),
                    "weights": cr["weights"].tolist(),
                    "truncated": cr["truncated"],
                }
            )

    # ResultsBundle.pic.eedf も同様に必須配列 (prompts/85)。server.py の done 組み立てと同じ変換
    eedf: list[dict] = []
    if sim.eedf_results is not None:
        for r in sim.eedf_results:
            eedf.append(
                {
                    "label": r["label"],
                    "e_centers": r["e_centers"].tolist(),
                    "f": r["f"].tolist(),
                    "mean_energy_ev": r["mean_energy_ev"],
                    "t_eff_ev": r["t_eff_ev"],
                    "total_weight": r["total_weight"],
                    "overflow_frac": r["overflow_frac"],
                    "n_samples": r["n_samples"],
                }
            )

    return {
        "version": 1,
        "pic": {
            "started": started,
            "frame": None,  # ライブフレームは無い (バッチは history/fields/cycle のみ復元すればよい)
            "history": history,
            "fields": fields,
            "cycle": cycle,
            "collectors": collectors,
            "eedf": eedf,
            "elapsed_s": elapsed_s,
            # 阻止コンデンサ (自己バイアス、prompts/134)。コンデンサが無ければ None
            "circuit": pic_circuit_result(sim),
            # 収束の判定 (prompts/137)。判定しなければ None
            "convergence": pic_convergence_result(sim),
        },
    }


def pic_convergence_result(sim) -> dict | None:
    """2D PIC の結果の convergence (流体と同じ形、prompts/137)。判定しなければ None。"""
    conv = getattr(sim, "conv", None)
    return None if conv is None else conv.result()


def pic_circuit_result(sim) -> dict | None:
    """2D PIC の結果の circuit (流体 2D の build_fluid2d_result と同じ形・単位)。コンデンサが無ければ None。"""
    circuit = getattr(sim, "circuit", None)
    if circuit is None:
        return None
    return circuit.result(
        {"capacitance": "F", "charge": "C", "current": "A"} if sim.rz
        else {"capacitance": "F/m", "charge": "C/m", "current": "A/m"}
    )
