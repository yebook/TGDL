"""Versioned configuration backup transport; never contains decrypted secrets."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BackupEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["tgdl-settings"]
    version: Literal[1]
    kdf: Literal["scrypt-16384-8-1"]
    salt: str = Field(min_length=24, max_length=24)
    nonce: str = Field(min_length=16, max_length=16)
    ciphertext: str = Field(min_length=24, max_length=90000)


class BackupExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=6, max_length=128)


class BackupInput(BackupExport):
    backup: BackupEnvelope


class BackupRestore(BackupInput):
    revision: int = Field(ge=1, strict=True)


class BackupExportResponse(BaseModel):
    backup: BackupEnvelope
    excluded_fields: list[str]


class BackupPreview(BaseModel):
    revision: int
    created_at: str
    included_fields: list[str]
    skipped_fields: list[str]
    warnings: list[str]
    configured: dict[str, bool]
    summary: dict[str, Any]
