from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.codebase.mock_adapter import MockCodebaseAdapter
from app.collector.law_api import LawApiClient
from app.db.database import init_db, get_session
from app.db.models import LawChange, Mapping, PatchProposal, Review, SyncState
from app.embedding.indexer import CodeIndexer
from app.llm.claude_client import ClaudeClient

MOCK_REPO_ROOT = "./mock_repo"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="국세 법령 변경 모니터링",
    version="0.0.1",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/collect")
def collect(db: Session = Depends(get_session)) -> dict:
    """
    법제처 API에서 last_sync 이후 변경 법령을 수집하여 DB에 저장한다.
    OC 키 없으면 mock 데이터로 동작.
    """
    state = db.get(SyncState, 1)
    if state is None:
        state = SyncState(id=1)
        db.add(state)

    since = state.last_sync or datetime.now(timezone.utc).strftime("%Y0101")

    client = LawApiClient()
    items = client.search_changed(since)

    saved = 0
    for item in items:
        exists = (
            db.query(LawChange)
            .filter_by(law_id=item["law_id"], promulgation_date=item["promulgation_date"])
            .first()
        )
        if exists:
            continue
        db.add(LawChange(
            law_id=item["law_id"],
            law_name=item["law_name"],
            article_no=item["article_no"],
            promulgation_date=item["promulgation_date"],
            effective_date=item["effective_date"],
            before_text=item["before_text"],
            after_text=item["after_text"],
        ))
        saved += 1

    state.last_sync = datetime.now(timezone.utc).strftime("%Y%m%d")
    state.last_run_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "fetched": len(items),
        "saved": saved,
        "since": since,
        "mock_mode": client._mock_mode,
    }


@app.get("/changes")
def list_changes(db: Session = Depends(get_session)) -> list[dict]:
    """수집된 법령 변경 목록 조회 (Phase 2: 담당자 검토용)"""
    rows = db.query(LawChange).order_by(LawChange.promulgation_date.desc()).all()
    return [
        {
            "id": r.id,
            "law_name": r.law_name,
            "article_no": r.article_no,
            "promulgation_date": r.promulgation_date,
            "effective_date": r.effective_date,
            "status": r.status,
            "ai_summary": r.ai_summary,
            "ai_impact": r.ai_impact,
        }
        for r in rows
    ]


@app.post("/changes/{change_id}/analyze")
def analyze(change_id: int, force: bool = False, db: Session = Depends(get_session)) -> dict:
    """
    Claude로 법령 변경 조문을 분석하여 ai_summary, ai_impact를 DB에 저장한다.
    이미 분석된 건은 재분석하지 않는다. force=true 로 강제 재분석 가능.
    """
    row = db.get(LawChange, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="변경 건을 찾을 수 없습니다.")
    if row.ai_summary and not force:
        return {"skipped": True, "reason": "이미 분석된 건입니다. 재분석하려면 ?force=true 를 사용하세요.", "id": change_id}

    llm = ClaudeClient()
    result = llm.analyze_change(
        before=row.before_text or "",
        after=row.after_text or "",
        context=f"{row.law_name} {row.article_no}",
    )

    if "raw" in result:
        # 파싱 실패 — raw 텍스트라도 저장
        row.ai_summary = result["raw"]
        row.ai_impact = ""
    else:
        row.ai_summary = result.get("summary", "")
        row.ai_impact = result.get("impact", "")

    row.status = "reviewing"
    db.commit()

    return {
        "id": change_id,
        "summary": row.ai_summary,
        "impact": row.ai_impact,
        "parse_ok": "raw" not in result,
    }


class ReviewBody(BaseModel):
    reviewer: str
    comment: str


@app.post("/changes/{change_id}/review")
def review(change_id: int, body: ReviewBody, db: Session = Depends(get_session)) -> dict:
    """담당자 검토 의견을 등록한다."""
    row = db.get(LawChange, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="변경 건을 찾을 수 없습니다.")

    rev = Review(
        law_change_id=change_id,
        reviewer=body.reviewer,
        comment=body.comment,
        status="open",
    )
    db.add(rev)
    db.commit()
    db.refresh(rev)

    return {"review_id": rev.id, "law_change_id": change_id, "status": rev.status}


@app.post("/index")
def index_codebase() -> dict:
    """
    mock_repo 코드베이스를 임베딩 인덱싱한다.
    실제 코드 연동 시 MOCK_REPO_ROOT를 실제 repo 경로로 교체.
    """
    indexer = CodeIndexer()
    adapter = MockCodebaseAdapter(repo_root=MOCK_REPO_ROOT, indexer=indexer)
    count = indexer.index(adapter)
    return {"indexed_chunks": count}


@app.post("/changes/{change_id}/map")
def map_change(change_id: int, k: int = 5, db: Session = Depends(get_session)) -> dict:
    """
    법령 변경 내용으로 벡터 검색 → 관련 코드 위치를 Mapping 테이블에 저장한다.
    분석(analyze) 완료 후 호출 권장 (ai_summary를 쿼리에 활용).
    """
    row = db.get(LawChange, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="변경 건을 찾을 수 없습니다.")

    query = " ".join(filter(None, [
        row.law_name, row.article_no,
        row.ai_summary, row.before_text, row.after_text,
    ]))

    indexer = CodeIndexer()
    hits = indexer.search(query, k=k)
    if not hits:
        return {"mapped": 0, "note": "인덱싱된 코드가 없습니다. POST /index 를 먼저 실행하세요."}

    # 같은 파일의 여러 청크가 동일 (path, symbol)로 반환될 수 있으므로
    # 최고 점수 청크만 남긴다 (hits는 이미 score 내림차순).
    best: dict[tuple[str, str], object] = {}
    for hit in hits:
        key = (hit.path, hit.symbol)
        if key not in best:
            best[key] = hit

    article_id = f"{row.law_id}:{row.article_no}"
    saved = 0
    for hit in best.values():
        exists = (
            db.query(Mapping)
            .filter_by(article_id=article_id, path=hit.path, symbol=hit.symbol)
            .first()
        )
        if exists:
            continue
        db.add(Mapping(
            article_id=article_id,
            repo="mock_repo",
            path=hit.path,
            symbol=hit.symbol,
            change_type=row.change_type or "unknown",
            confidence=hit.score,
            verified=False,
        ))
        saved += 1

    db.commit()
    return {
        "law_change_id": change_id,
        "hits": [{"path": h.path, "symbol": h.symbol, "score": h.score} for h in hits],
        "saved": saved,
    }


@app.get("/changes/{change_id}/mappings")
def get_mappings(change_id: int, db: Session = Depends(get_session)) -> list[dict]:
    """법령 변경에 매핑된 코드 위치 목록 조회."""
    row = db.get(LawChange, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="변경 건을 찾을 수 없습니다.")

    article_id = f"{row.law_id}:{row.article_no}"
    mappings = db.query(Mapping).filter_by(article_id=article_id).all()
    return [
        {
            "id": m.id,
            "path": m.path,
            "symbol": m.symbol,
            "confidence": m.confidence,
            "verified": m.verified,
        }
        for m in mappings
    ]


@app.post("/changes/{change_id}/apply")
def apply(
    change_id: int,
    min_confidence: float = 0.2,
    db: Session = Depends(get_session),
) -> dict:
    """
    매핑된 코드 스니펫 + 법령 변경 diff → Claude Sonnet으로 수정 초안(unified diff) 생성.
    결과는 PatchProposal(draft)으로 저장되며, 자동 적용되지 않는다.
    사람이 POST /proposals/{id}/approve 를 눌러야 반영된다.
    """
    row = db.get(LawChange, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="변경 건을 찾을 수 없습니다.")

    article_id = f"{row.law_id}:{row.article_no}"
    mappings = (
        db.query(Mapping)
        .filter(Mapping.article_id == article_id, Mapping.confidence >= min_confidence)
        .order_by(Mapping.confidence.desc())
        .all()
    )
    if not mappings:
        raise HTTPException(
            status_code=422,
            detail=f"신뢰도 {min_confidence} 이상 매핑이 없습니다. POST /changes/{change_id}/map 을 먼저 실행하세요.",
        )

    # 매핑된 파일의 실제 코드를 읽어 스니펫 조합
    adapter = MockCodebaseAdapter(repo_root=MOCK_REPO_ROOT)
    seen_paths: set[str] = set()
    snippets: list[str] = []
    for m in mappings:
        if m.path in seen_paths:
            continue
        seen_paths.add(m.path)
        try:
            code = adapter.read_file(m.path)
            snippets.append(f"// {m.path}\n{code}")
        except FileNotFoundError:
            pass

    law_diff = (
        f"[법령] {row.law_name} {row.article_no}\n\n"
        f"[개정 전]\n{row.before_text or '(내용 없음)'}\n\n"
        f"[개정 후]\n{row.after_text or '(내용 없음)'}\n\n"
        f"[AI 요약]\n{row.ai_summary or ''}\n"
        f"[AI 영향 분석]\n{row.ai_impact or ''}"
    )

    llm = ClaudeClient()
    diff_text = llm.propose_patch(law_diff=law_diff, code_snippets=snippets)

    proposal = PatchProposal(
        law_change_id=change_id,
        mapping_id=mappings[0].id,
        diff=diff_text,
        model_used=llm.model,
        approval_status="draft",
    )
    db.add(proposal)
    row.status = "pending_apply"
    db.commit()
    db.refresh(proposal)

    return {
        "proposal_id": proposal.id,
        "law_change_id": change_id,
        "approval_status": proposal.approval_status,
        "snippets_used": list(seen_paths),
        "diff_preview": diff_text[:400] + ("…" if len(diff_text) > 400 else ""),
    }


@app.get("/changes/{change_id}/proposals")
def list_proposals(change_id: int, db: Session = Depends(get_session)) -> list[dict]:
    """생성된 patch 초안 목록 조회."""
    proposals = (
        db.query(PatchProposal)
        .filter_by(law_change_id=change_id)
        .order_by(PatchProposal.created_at.desc())
        .all()
    )
    return [
        {
            "id": p.id,
            "approval_status": p.approval_status,
            "model_used": p.model_used,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "diff": p.diff,
        }
        for p in proposals
    ]


@app.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: int, db: Session = Depends(get_session)) -> dict:
    """
    사람 승인 게이트 — 승인 후 patch를 mock_repo/patches/ 에 기록한다.
    실제 repo 연동 시 여기서 git apply 또는 PR 생성.
    """
    proposal = db.get(PatchProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")
    if proposal.approval_status != "draft":
        raise HTTPException(status_code=409, detail=f"이미 처리된 초안입니다: {proposal.approval_status}")

    adapter = MockCodebaseAdapter(repo_root=MOCK_REPO_ROOT)
    patch_path = adapter.apply_patch(proposal_id=proposal_id, diff=proposal.diff)

    proposal.approval_status = "approved"
    change = db.get(LawChange, proposal.law_change_id)
    if change:
        change.status = "done"
    db.commit()

    return {
        "proposal_id": proposal_id,
        "approval_status": "approved",
        "patch_written_to": patch_path,
    }


@app.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: int, db: Session = Depends(get_session)) -> dict:
    """초안 거절 — law_change 상태를 reviewing으로 되돌린다."""
    proposal = db.get(PatchProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")
    if proposal.approval_status != "draft":
        raise HTTPException(status_code=409, detail=f"이미 처리된 초안입니다: {proposal.approval_status}")

    proposal.approval_status = "rejected"
    change = db.get(LawChange, proposal.law_change_id)
    if change:
        change.status = "reviewing"
    db.commit()

    return {"proposal_id": proposal_id, "approval_status": "rejected"}
