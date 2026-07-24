"""Issue #0014 artifact 무결성과 inspection replay 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.audit.artifacts import LocalArtifactStore
from app.audit.replay import InspectionReplay


def test_artifact_round_trip_and_hash_verification(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    ref = store.put_bytes(
        "run-1",
        "analysis-output",
        b'{"summary":"ok"}',
        ".json",
        media_type="application/json",
    )

    assert not Path(ref.relative_path).is_absolute()
    assert ref.sha256.startswith("sha256:")
    assert store.read(ref) == b'{"summary":"ok"}'
    assert store.verify(ref) is True


def test_artifact_tampering_is_detected(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    ref = store.put_bytes("run-1", "retrieval", b"original", ".json")
    (tmp_path / ref.relative_path).write_bytes(b"tampered")

    assert store.verify(ref) is False
    with pytest.raises(ValueError, match="hash mismatch"):
        store.read(ref)


@pytest.mark.parametrize(
    ("run_id", "artifact_type", "suffix"),
    [
        ("../escape", "output", ".json"),
        ("run-1", "../output", ".json"),
        ("run-1", "output", "/../../secret"),
    ],
)
def test_path_traversal_is_rejected(
    tmp_path: Path,
    run_id: str,
    artifact_type: str,
    suffix: str,
) -> None:
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="safe"):
        store.put_bytes(run_id, artifact_type, b"x", suffix)


def test_atomic_write_leaves_no_temporary_files(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    store.put_bytes("run-1", "proposal", b"patch", ".patch", contains_code=True)

    assert not list(tmp_path.rglob("*.tmp"))


def test_manifest_and_inspection_replay_do_not_execute_anything(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    output = store.put_bytes("run-1", "analysis-output", b"result", ".txt")
    manifest = store.write_manifest(
        "run-1",
        [output],
        {
            "source_hash": "sha256:source",
            "repository_alias": "mock_repo",
            "repository_commit": "abc123",
            "model": "qwen",
            "prompt_versions": {"analysis": "v1"},
            "settings_hash": "sha256:settings",
            "replayability": "inspection_only",
        },
    )

    replay = InspectionReplay(store).inspect("run-1")

    assert store.verify(manifest)
    assert replay["mode"] == "inspection"
    assert replay["verified"] is True
    assert replay["manifest"]["repository_alias"] == "mock_repo"
    assert replay["artifacts"][0]["verified"] is True
    assert json.loads(store.read(manifest))["replayability"] == "inspection_only"

