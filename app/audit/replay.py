"""저장 결과만 재구성하는 inspection replay. 모델·저장소를 호출하지 않는다."""

from __future__ import annotations

import json

from app.audit.artifacts import LocalArtifactStore, artifact_reference_from_dict
from app.domain.common.serialization import to_jsonable


class InspectionReplay:
    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store

    def inspect(self, run_id: str) -> dict:
        manifest_ref = self.store.find_manifest(run_id)
        manifest = json.loads(self.store.read(manifest_ref))
        artifacts = []
        verified = True
        for raw_ref in manifest.get("artifacts", []):
            ref = artifact_reference_from_dict(raw_ref)
            is_verified = self.store.verify(ref)
            verified = verified and is_verified
            artifacts.append({**to_jsonable(ref), "verified": is_verified})
        return {
            "mode": "inspection",
            "run_id": run_id,
            "verified": verified,
            "manifest": manifest,
            "artifacts": artifacts,
        }
