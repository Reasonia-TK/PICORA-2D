"""ローカル API /xs/registry。登録の保存先はサーバー起動時に決める。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .model import Kind
from .registry import CrossSectionRegistry, MAX_TEXT_BYTES


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RegistryDestinationRequest(_Request):
    set_name: str = Field(default="", max_length=256)
    set_id: str | None = Field(default=None, min_length=1, max_length=128)


class RegistryMetadataRequest(RegistryDestinationRequest):
    source: str = Field(min_length=1, max_length=4096)
    source_url: str = Field(default="", max_length=2048)
    notes: str = Field(default="", max_length=4096)


class RegistryImportRequest(RegistryMetadataRequest):
    text: str = Field(max_length=MAX_TEXT_BYTES)


class RegistryCsvData(_Request):
    text: str = Field(max_length=MAX_TEXT_BYTES)
    filename: str = Field(default="", max_length=256)
    gas: str = Field(min_length=1, max_length=128)
    kind: Kind
    threshold_ev: float = Field(default=0, ge=0)
    projectile: str = Field(default="e", min_length=1, max_length=128)
    label: str = Field(default="", max_length=256)
    mass_amu: float | None = Field(default=None, gt=0)


class RegistryCsvRequest(RegistryMetadataRequest, RegistryCsvData):
    pass


class RegistryCsvBatchRequest(RegistryMetadataRequest):
    files: list[RegistryCsvData] = Field(min_length=1, max_length=100)


class RegistryIdsRequest(_Request):
    set_ids: list[str] = Field(min_length=1, max_length=1000)


class RegistryResolveRequest(_Request):
    set_id: str = Field(min_length=1, max_length=128)
    species: Literal["electron", "ion"]


class RegistryRenameRequest(_Request):
    set_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)


class RegistryCopyRequest(RegistryDestinationRequest):
    source_set_id: str = Field(min_length=1, max_length=128)
    entry_ids: list[str] = Field(min_length=1, max_length=1000)


class RegistryRemoveRequest(_Request):
    set_id: str = Field(min_length=1, max_length=128)
    entry_ids: list[str] = Field(min_length=1, max_length=1000)


def make_registry_router(storage_path: Path | str | None = None) -> APIRouter:
    registry = CrossSectionRegistry(storage_path)
    router = APIRouter(prefix="/xs/registry", tags=["cross-sections"])

    def call(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"断面積の保存先にアクセスできません: {exc}") from exc

    @router.get("")
    def list_entries():
        return call(registry.list)

    @router.post("/lxcat")
    def import_lxcat(body: RegistryImportRequest):
        return call(registry.import_lxcat, **body.model_dump())

    @router.post("/csv")
    def import_csv(body: RegistryCsvRequest):
        values = body.model_dump()
        metadata = {key: values.pop(key) for key in ("source", "source_url", "notes", "set_name", "set_id")}
        return call(registry.import_csv_batch, [values], **metadata)

    @router.post("/csv/batch")
    def import_csv_batch(body: RegistryCsvBatchRequest):
        values = body.model_dump()
        files = values.pop("files")
        return call(registry.import_csv_batch, files, **values)

    @router.post("/rename")
    def rename_set(body: RegistryRenameRequest):
        return {"set": call(registry.rename, body.set_id, body.name)}

    @router.post("/copy")
    def copy_entries(body: RegistryCopyRequest):
        return call(registry.copy_entries, **body.model_dump())

    @router.post("/remove-processes")
    def remove_entries(body: RegistryRemoveRequest):
        return {"set": call(registry.remove_entries, body.set_id, body.entry_ids)}

    @router.post("/delete")
    def delete_entries(body: RegistryIdsRequest):
        return {"deleted": call(registry.delete, body.set_ids)}

    @router.post("/resolve")
    def resolve_entries(body: RegistryResolveRequest):
        return call(registry.resolve, body.set_id, body.species)

    return router
