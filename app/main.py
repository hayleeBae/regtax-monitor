import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.codebase.mock_adapter import MockCodebaseAdapter
from app.codebase.real_adapter import RealCodebaseAdapter
from app.collector.law_api import LawApiClient
from app.db.database import init_db, get_session
from app.db.models import LawChange, Mapping, PatchProposal, Review, SyncState
from app.embedding.indexer import CodeIndexer
from app.llm.claude_client import ClaudeClient
from config import settings

MOCK_REPO_ROOT = "./mock_repo"

if settings.hf_hub_disable_ssl:
    import truststore
    truststore.inject_into_ssl()


def _make_adapter(indexer=None):
    """REPO_ROOT 설정 여부에 따라 Real/Mock 어댑터를 반환한다."""
    if settings.repo_root:
        return RealCodebaseAdapter(repo_root=settings.repo_root, indexer=indexer)
    return MockCodebaseAdapter(repo_root=MOCK_REPO_ROOT, indexer=indexer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _auto_index_if_empty()
    scheduler = _start_scheduler()
    yield
    if scheduler:
        scheduler.shutdown()


def _start_scheduler():
    # .env에서 SCHEDULER_ENABLED=true 로 설정하면 활성화된다.
    if not settings.scheduler_enabled:
        return None

    from apscheduler.schedulers.background import BackgroundScheduler
    from app.db.database import get_session as _get_session

    def _scheduled_collect():
        db = next(_get_session())
        try:
            collect(db)
            print(f"[스케줄러] 법령 수집 완료")
        except Exception as e:
            print(f"[스케줄러] 수집 오류: {e}")
        finally:
            db.close()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _scheduled_collect,
        "interval",
        hours=settings.scheduler_interval_hours,
        id="collect_job",
    )
    scheduler.start()
    print(f"[스케줄러] 시작 — {settings.scheduler_interval_hours}시간 간격으로 법령 수집")
    return scheduler


def _auto_index_if_empty() -> None:
    """ChromaDB 컬렉션이 비어 있으면 서버 시작 시 자동 인덱싱."""
    indexer = CodeIndexer()
    if indexer.collection.count() > 0:
        return
    print("ChromaDB가 비어 있습니다. 코드베이스 자동 인덱싱을 시작합니다...")
    adapter = _make_adapter(indexer=indexer)
    count = indexer.index(adapter)
    print(f"자동 인덱싱 완료: {count}개 청크")


app = FastAPI(
    title="국세 법령 변경 모니터링",
    version="0.0.1",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
def ui():
    with open("static/index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/collect")
def collect(db: Session = Depends(get_session)) -> dict:
    """
    법제처 API에서 last_sync 이후 변경 법령을 수집하여 DB에 저장하고,
    신규 건에 한해 개정문·제개정이유(신구대조)를 자동 조회한다.
    OC 키 없으면 mock 데이터로 동작.
    """
    state = db.get(SyncState, 1)
    if state is None:
        state = SyncState(id=1)
        db.add(state)

    since = state.last_sync or datetime.now(timezone.utc).strftime("%Y0101")

    client = LawApiClient()
    items = client.search_changed(since)

    saved_ids: list[int] = []
    for item in items:
        exists = (
            db.query(LawChange)
            .filter_by(law_id=item["law_id"], promulgation_date=item["promulgation_date"])
            .first()
        )
        if exists:
            continue
        row = LawChange(
            law_id=item["law_id"],
            law_mst=item.get("law_mst", ""),
            law_name=item["law_name"],
            article_no=item["article_no"],
            promulgation_date=item["promulgation_date"],
            effective_date=item["effective_date"],
            before_text=item["before_text"],
            after_text=item["after_text"],
        )
        db.add(row)
        db.flush()  # id 확보
        saved_ids.append(row.id)

    state.last_sync = datetime.now(timezone.utc).strftime("%Y%m%d")
    state.last_run_at = datetime.now(timezone.utc)
    db.commit()

    # 신규 건에 한해 신구대조 자동 조회 (mock 모드 또는 MST 없으면 건너뜀)
    detail_ok, detail_fail = 0, 0
    if not client._mock_mode:
        for change_id in saved_ids:
            row = db.get(LawChange, change_id)
            if not row or not row.law_mst:
                continue
            try:
                detail = client.fetch_detail(row.law_mst)
                row.article_no = detail["article_no"]
                row.before_text = detail["before_text"]
                row.after_text = detail["after_text"]
                detail_ok += 1
            except Exception:
                detail_fail += 1
        db.commit()

    return {
        "fetched": len(items),
        "saved": len(saved_ids),
        "since": since,
        "mock_mode": client._mock_mode,
        "detail_fetched": detail_ok,
        "detail_failed": detail_fail,
    }


@app.post("/changes/{change_id}/fetch-detail")
def fetch_detail(change_id: int, db: Session = Depends(get_session)) -> dict:
    """
    법령 MST로 개정문·제개정이유를 조회하여 before_text / after_text / article_no 를 채운다.
    collect 후 이 엔드포인트를 호출해야 analyze 에서 의미 있는 결과가 나온다.
    """
    row = db.get(LawChange, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="변경 건을 찾을 수 없습니다.")
    if not row.law_mst:
        raise HTTPException(status_code=422, detail="법령 MST가 없습니다. mock 데이터이거나 수집 오류입니다.")

    client = LawApiClient()
    detail = client.fetch_detail(row.law_mst)

    row.article_no = detail["article_no"]
    row.before_text = detail["before_text"]
    row.after_text = detail["after_text"]
    db.commit()

    return {
        "id": change_id,
        "article_no": row.article_no,
        "before_text_preview": row.before_text[:200] + ("…" if len(row.before_text) > 200 else ""),
        "after_text_preview": row.after_text[:200] + ("…" if len(row.after_text) > 200 else ""),
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
    adapter = _make_adapter(indexer=indexer)
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
def get_mappings(
    change_id: int,
    verified_only: bool = False,
    db: Session = Depends(get_session),
) -> list[dict]:
    """
    법령 변경에 매핑된 코드 위치 목록 조회.
    verified_only=true 이면 담당자가 확인한 매핑만 반환.
    """
    row = db.get(LawChange, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="변경 건을 찾을 수 없습니다.")

    article_id = f"{row.law_id}:{row.article_no}"
    q = db.query(Mapping).filter_by(article_id=article_id)
    if verified_only:
        q = q.filter(Mapping.verified == True)  # noqa: E712
    mappings = q.order_by(Mapping.verified.desc(), Mapping.confidence.desc()).all()
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


@app.patch("/mappings/{mapping_id}/verify")
def verify_mapping(
    mapping_id: int,
    verified: bool = True,
    db: Session = Depends(get_session),
) -> dict:
    """
    담당자가 매핑 정확성을 검증한다.
    verified=true: 이 코드 위치가 해당 조문과 관련 있음을 확인.
    verified=false: 잘못된 매핑으로 표시 (이후 apply에서 제외됨).
    """
    m = db.get(Mapping, mapping_id)
    if m is None:
        raise HTTPException(status_code=404, detail="매핑을 찾을 수 없습니다.")
    m.verified = verified
    db.commit()
    return {"mapping_id": mapping_id, "path": m.path, "symbol": m.symbol, "verified": m.verified}


@app.post("/changes/{change_id}/apply")
def apply(
    change_id: int,
    min_confidence: float = 0.0,
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

    # verified 매핑이 있으면 우선 사용, 없으면 confidence 기준 fallback
    verified_mappings = (
        db.query(Mapping)
        .filter(Mapping.article_id == article_id, Mapping.verified == True)  # noqa: E712
        .order_by(Mapping.confidence.desc())
        .all()
    )
    if verified_mappings:
        mappings = verified_mappings
        mapping_mode = "verified"
    else:
        mappings = (
            db.query(Mapping)
            .filter(Mapping.article_id == article_id, Mapping.confidence >= min_confidence)
            .order_by(Mapping.confidence.desc())
            .all()
        )
        mapping_mode = "confidence"

    if not mappings:
        raise HTTPException(
            status_code=422,
            detail=f"사용 가능한 매핑이 없습니다. POST /changes/{change_id}/map 을 먼저 실행하세요.",
        )

    # 매핑된 파일의 실제 코드를 읽어 스니펫 조합
    adapter = _make_adapter()
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

    # 코드 그래프 추적: 매핑된 클래스를 참조하는 Service/DAO 파일 자동 추가
    from pathlib import Path as _Path
    for m in mappings:
        class_name = _Path(m.path).stem
        for usage_path in adapter.find_usages(class_name, max_results=3):
            if usage_path in seen_paths:
                continue
            seen_paths.add(usage_path)
            try:
                code = adapter.read_file(usage_path)
                snippets.append(f"// {usage_path} [참조: {class_name}]\n{code}")
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
        "mapping_mode": mapping_mode,   # "verified" | "confidence"
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


@app.get("/proposals")
def list_all_proposals(db: Session = Depends(get_session)) -> list[dict]:
    """전체 patch 초안 목록 조회 (법령 정보 포함)."""
    proposals = (
        db.query(PatchProposal)
        .order_by(PatchProposal.created_at.desc())
        .all()
    )
    result = []
    for p in proposals:
        change = db.get(LawChange, p.law_change_id)
        result.append({
            "id": p.id,
            "law_change_id": p.law_change_id,
            "law_name": change.law_name if change else None,
            "article_no": change.article_no if change else None,
            "approval_status": p.approval_status,
            "model_used": p.model_used,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "diff": p.diff,
        })
    return result


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

    adapter = _make_adapter()
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
