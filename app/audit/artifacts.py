"""로컬 감사 artifact 저장소 — 상대경로, 원자적 쓰기, SHA-256 검증."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol
from uuid import uuid4

from app.audit.sanitizer import sanitize_payload
from app.domain.audit import ArtifactReference
from app.domain.common.serialization import to_jsonable

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,12}$")


class ArtifactStore(Protocol):
    def put_bytes(
        self,
        run_id: str,
        artifact_type: str,
        content: bytes,
        suffix: str,
        **metadata,
    ) -> ArtifactReference: ...

    def read(self, ref: ArtifactReference) -> bytes: ...

    def verify(self, ref: ArtifactReference) -> bool: ...


class LocalArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def put_bytes(
        self,
        run_id: str,
        artifact_type: str,
        content: bytes,
        suffix: str,
        *,
        media_type: str | None = None,
        contains_code: bool = False,
        redacted: bool = False,
    ) -> ArtifactReference:
        _validate_component(run_id)
        _validate_component(artifact_type)
        if not _SAFE_SUFFIX.fullmatch(suffix):
            raise ValueError("suffix must be a safe extension")

        artifact_id = uuid4().hex
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{artifact_type}-{artifact_id}{suffix}"
        target = _safe_join(self.root, Path(run_id) / filename)
        digest = _digest(content)

        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{artifact_type}-",
                suffix=".tmp",
                dir=run_dir,
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        return ArtifactReference(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            relative_path=target.relative_to(self.root).as_posix(),
            sha256=digest,
            size=len(content),
            media_type=media_type
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream",
            created_at=datetime.now(timezone.utc),
            contains_code=contains_code,
            redacted=redacted,
        )

    def read(self, ref: ArtifactReference) -> bytes:
        path = _safe_join(self.root, Path(ref.relative_path))
        content = path.read_bytes()
        if _digest(content) != ref.sha256 or len(content) != ref.size:
            raise ValueError(f"artifact hash mismatch: {ref.artifact_id}")
        return content

    def verify(self, ref: ArtifactReference) -> bool:
        try:
            self.read(ref)
        except (OSError, ValueError):
            return False
        return True

    def write_manifest(
        self,
        run_id: str,
        artifacts: Iterable[ArtifactReference],
        metadata: dict,
    ) -> ArtifactReference:
        refs = list(artifacts)
        payload = {
            "schema_version": "audit-manifest-v1",
            "run_id": run_id,
            **sanitize_payload(metadata),
            "artifacts": [to_jsonable(ref) for ref in refs],
        }
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        return self.put_bytes(
            run_id,
            "manifest",
            content,
            ".json",
            media_type="application/json",
            redacted=True,
        )

    def find_manifest(self, run_id: str) -> ArtifactReference:
        _validate_component(run_id)
        run_dir = _safe_join(self.root, Path(run_id))
        candidates = sorted(run_dir.glob("manifest-*.json"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise FileNotFoundError(f"manifest not found: {run_id}")
        path = candidates[-1]
        content = path.read_bytes()
        return ArtifactReference(
            artifact_id=path.stem.removeprefix("manifest-"),
            artifact_type="manifest",
            relative_path=path.relative_to(self.root).as_posix(),
            sha256=_digest(content),
            size=len(content),
            media_type="application/json",
            created_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
            redacted=True,
        )


def artifact_reference_from_dict(value: dict) -> ArtifactReference:
    data = dict(value)
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    return ArtifactReference(**data)


def _validate_component(value: str) -> None:
    if not _SAFE_COMPONENT.fullmatch(value) or value in {".", ".."}:
        raise ValueError("path component must be safe")


def _safe_join(root: Path, relative: Path) -> Path:
    if relative.is_absolute():
        raise ValueError("artifact path must be safe and relative")
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("artifact path must be safe and remain under root")
    return candidate


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()
