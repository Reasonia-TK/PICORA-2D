"""v2 GPU PIC-MCC — 直交格子 + 埋め込み境界 (EB) 上の粒子シミュレーション (prompts/119 P3)。"""

from __future__ import annotations


def make_pic_simulation(project, gas_field=None, device=None):
    """直交格子 + EB の GPU PIC を作る。CUDA が使用できない場合は失敗する。"""
    if project.mesh.mode != "cartesian":
        raise ValueError("PICORA-2D は mesh.mode='cartesian' だけに対応しています")
    from .simulation import GpuPicSimulation

    return GpuPicSimulation(project, gas_field, device=device)


def __getattr__(name):
    # GpuPicSimulation は CuPy を import するので、使うときだけ読み込む
    if name == "GpuPicSimulation":
        from .simulation import GpuPicSimulation

        return GpuPicSimulation
    raise AttributeError(name)


__all__ = ["GpuPicSimulation", "make_pic_simulation"]
