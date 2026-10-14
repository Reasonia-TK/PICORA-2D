"""PICORA-2D のローカル FastAPI サーバー (2D PIC / 流体プラズマ専用)。"""

from __future__ import annotations

import base64
import binascii
import secrets
from contextlib import asynccontextmanager
from functools import lru_cache

import numpy as np
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__, _numba_kernels
from .boltz import boltzpm_available
from .device import describe as describe_device
from .dxf import read_dxf, write_dxf
from .field.compat import cartesian_mesh_result, cartesian_profile, cartesian_solve
from .jobs import JobManager, default_runners, make_router
from .lxcat import parse_lxcat
from .project2d import Project2d, project_schema
from .schema import LxcatParseRequest, LxcatParseResult, MeshResult, Point, ProfileResult, SolveResult
from .xs.api import XsParseRequest, XsParseResponse, parse_xs_text
from .xs.registry_api import make_registry_router

SERVER_INSTANCE = secrets.token_hex(8)
_jobs = JobManager(default_runners(), max_running=1, default_per_kind=1, keep_sims_per_kind=2)


@asynccontextmanager
async def lifespan(app):
    yield
    _jobs.shutdown()


app = FastAPI(title="PICORA-2D backend", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|tauri\.localhost)(:\d+)?|tauri://localhost",
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(make_router(_jobs, SERVER_INSTANCE))
app.include_router(make_registry_router())


@app.get("/health")
def health():
    dev = describe_device()
    return {
        "status": "ok", "name": "PICORA-2D", "version": __version__, "instance": SERVER_INSTANCE,
        "gpu": bool(dev["cuda"]), "numba": _numba_kernels.HAVE_NUMBA, "jobs": True, "v2": dev,
        "boltz": boltzpm_available(),
        "capabilities": {"solvers": ["pic", "fluid2d"], "auxiliary": ["boltz"],
                         "mesh": ["cartesian"], "embedded_boundary": True, "amr": True},
    }


@app.post("/mesh", response_model=MeshResult)
def mesh_endpoint(project: Project2d) -> MeshResult:
    try:
        return cartesian_mesh_result(project)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/solve", response_model=SolveResult)
def solve_endpoint(project: Project2d) -> SolveResult:
    try:
        result, _ = cartesian_solve(project)
        return result
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


class ProfileRequest(BaseModel):
    project: Project2d
    p1: Point
    p2: Point
    n: int = Field(200, ge=2, le=100_000)


@app.post("/profile", response_model=ProfileResult)
def profile_endpoint(req: ProfileRequest) -> ProfileResult:
    try:
        s, v, e_abs = cartesian_profile(req.project, req.p1, req.p2, req.n)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc

    def finite(arr):
        return [float(x) if np.isfinite(x) else None for x in arr]

    return ProfileResult(s=s.tolist(), v=finite(v), e_abs=finite(e_abs))


@app.post("/lxcat/parse", response_model=LxcatParseResult)
def lxcat_endpoint(req: LxcatParseRequest) -> LxcatParseResult:
    try:
        processes, warnings = parse_lxcat(req.text, req.species)
        return LxcatParseResult(processes=processes, warnings=warnings)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/v2/xs/parse", response_model=XsParseResponse)
def xs_parse_endpoint(req: XsParseRequest) -> XsParseResponse:
    try:
        return parse_xs_text(req.text)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@lru_cache(maxsize=1)
def _project_schema():
    return project_schema()


@app.get("/v2/schema")
def schema_endpoint() -> dict:
    return {"version": __version__, "project": _project_schema()}


class DxfImportRequest(BaseModel):
    data: str
    unit: str | None = None


class DxfExportRequest(BaseModel):
    project: dict
    unit: str = "mm"


@app.post("/v2/cad/dxf/import")
def dxf_import_endpoint(req: DxfImportRequest) -> dict:
    try:
        raw = base64.b64decode(req.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "DXF のデータ (base64) を読めません") from exc
    try:
        return read_dxf(raw, req.unit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/v2/cad/dxf/export")
def dxf_export_endpoint(req: DxfExportRequest) -> Response:
    try:
        text = write_dxf(req.project, req.unit)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise HTTPException(422, f"DXF に書き出せません: {exc}") from exc
    return Response(content=text, media_type="application/dxf")
