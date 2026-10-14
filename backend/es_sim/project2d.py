"""PICORA-2D の公開入力。共通数値エンジン用 Project と互換性を保つ。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from .schema import MeshSettings, Project


class CartesianMeshSettings(MeshSettings):
    mode: Literal["cartesian"] = "cartesian"


class Project2d(Project):
    """2D PIC と 2D 流体だけを受け付けるプロジェクト。"""

    mesh: CartesianMeshSettings
    dsmc: SkipJsonSchema[None] = Field(None, exclude=True)
    particles: SkipJsonSchema[None] = Field(None, exclude=True)
    pic1d: SkipJsonSchema[None] = Field(None, exclude=True)
    fluid1d: SkipJsonSchema[None] = Field(None, exclude=True)
    tl: SkipJsonSchema[None] = Field(None, exclude=True)

    @model_validator(mode="after")
    def _check_picora_features(self):
        # 格子を組み立てる前に domain と境界の対応を検査する。重い格子・行列は作らない。
        from .geom.model import GeometryModel

        GeometryModel(self)
        if self.pic is not None and self.pic.mcc is not None and self.pic.mcc.use_dsmc_gas:
            raise ValueError("PICORA-2D の MCC は登録した断面積と一様ガス条件を使用します (use_dsmc_gas=false)")
        if self.pic is not None:
            unsupported = [name for name in ("injection", "fn", "merge") if getattr(self.pic, name) is not None]
            if unsupported:
                raise ValueError("GPU 2D PIC は次の設定に未対応です: " + ", ".join(unsupported))
        return self


def project_schema() -> dict:
    return Project2d.model_json_schema()
