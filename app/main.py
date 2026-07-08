import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.codebase.mock_adapter import MockCodebaseAdapter
from app.codebase.real_adapter import RealCodebaseAdapter
from app.collector.law_api import ApiNotGrantedError, LawApiClient
from app.collector.registry import load_domains
from app.db.database import init_db, get_session
from app.db.models import LawChange, Mapping, PatchProposal, Review, SyncState
from app.embedding.indexer import CodeIndexer
from app.llm import get_llm_client
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
    domains = load_domains()
    items: list[dict] = []
    admrul_warnings: list[str] = []

    if client._mock_mode:
        # mock은 고정 데이터 — 법령은 tax, 행정규칙은 hr(있으면)로 태깅
        first = next(iter(domains))
        law_domain = "tax" if "tax" in domains else first
        adm_domain = "hr" if "hr" in domains else first
        for it in client.search_changed(since):
            items.append({**it, "domain": law_domain})
        for it in client.search_admin_rules(since, domains[adm_domain].admin_rule_queries):
            items.append({**it, "domain": adm_domain})
    else:
        for key, dom in domains.items():
            for it in client.search_changed(since, dom.laws):
                items.append({**it, "domain": key})
            if not dom.admin_rule_queries:
                continue
            # 행정규칙 수집 실패는 경고로 남기고 법령 수집은 유지
            try:
                for it in client.search_admin_rules(since, dom.admin_rule_queries):
                    items.append({**it, "domain": key})
            except ApiNotGrantedError as e:
                admrul_warnings.append(str(e))
            except Exception as e:
                admrul_warnings.append(f"행정규칙 수집 실패({key}): {e}")

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
            source=item.get("source", "law"),
            domain=item.get("domain", "tax"),
        )
        db.add(row)
        db.flush()  # id 확보
        saved_ids.append(row.id)

    state.last_sync = datetime.now(timezone.utc).strftime("%Y%m%d")
    state.last_run_at = datetime.now(timezone.utc)
    db.commit()

    # 신규 건에 한해 상세 자동 조회 — 법령은 신구대조(개정문), 행정규칙은 본문 전문
    detail_ok, detail_fail = 0, 0
    if not client._mock_mode:
        for change_id in saved_ids:
            row = db.get(LawChange, change_id)
            if not row:
                continue
            try:
                if row.source and row.source != "law":
                    detail = client.fetch_admin_rule_detail(row.law_mst, row.law_id)
                elif row.law_mst:
                    detail = client.fetch_detail(row.law_mst)
                else:
                    continue
                row.article_no = detail["article_no"]
                row.before_text = detail["before_text"]
                row.after_text = detail["after_text"]
                detail_ok += 1
            except Exception:
                detail_fail += 1
        db.commit()

    from app.collector.law_api import law_tier
    tier_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    for item in items:
        t = law_tier(item["law_name"], item.get("source", "law"))
        tier_counts[t] = tier_counts.get(t, 0) + 1
        d = item.get("domain", "tax")
        domain_counts[d] = domain_counts.get(d, 0) + 1

    result = {
        "fetched": len(items),
        "saved": len(saved_ids),
        "tiers": tier_counts,     # 예: {"법률": 2, "시행령": 3, "고시": 1}
        "domains": domain_counts,  # 예: {"tax": 4, "hr": 2}
        "since": since,
        "mock_mode": client._mock_mode,
        "detail_fetched": detail_ok,
        "detail_failed": detail_fail,
    }
    if admrul_warnings:
        result["admrul_warning"] = " / ".join(admrul_warnings)
    return result


@app.post("/changes/{change_id}/fetch-detail")
def fetch_detail(change_id: int, db: Session = Depends(get_session)) -> dict:
    """
    법령 MST로 개정문·제개정이유를 조회하여 before_text / after_text / article_no 를 채운다.
    collect 후 이 엔드포인트를 호출해야 analyze 에서 의미 있는 결과가 나온다.
    """
    row = db.get(LawChange, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="변경 건을 찾을 수 없습니다.")

    client = LawApiClient()
    if client._mock_mode:
        raise HTTPException(status_code=422, detail="mock 모드에서는 상세 조회를 지원하지 않습니다.")
    if row.source and row.source != "law":
        detail = client.fetch_admin_rule_detail(row.law_mst, row.law_id)
    elif row.law_mst:
        detail = client.fetch_detail(row.law_mst)
    else:
        raise HTTPException(status_code=422, detail="법령 MST가 없습니다. mock 데이터이거나 수집 오류입니다.")

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
def list_changes(domain: str | None = None, db: Session = Depends(get_session)) -> list[dict]:
    """수집된 법령 변경 목록 조회 (Phase 2: 담당자 검토용). ?domain=hr 필터 지원."""
    from app.collector.law_api import law_tier
    q = db.query(LawChange)
    if domain:
        q = q.filter(LawChange.domain == domain)
    rows = q.order_by(LawChange.promulgation_date.desc()).all()
    return [
        {
            "id": r.id,
            "law_name": r.law_name,
            "domain": r.domain or "tax",
            "tier": law_tier(r.law_name or "", r.source or "law"),   # 법률/시행령/시행규칙/고시 등
            "article_no": r.article_no,
            "promulgation_date": r.promulgation_date,
            "effective_date": r.effective_date,
            "status": r.status,
            "ai_summary": r.ai_summary,
            "ai_impact": r.ai_impact,
        }
        for r in rows
    ]


# ── 참고 문서 (개정세법 해설 등) ────────────────────────────────
# 경로가 /docs 면 FastAPI 자동 문서(Swagger)와 충돌하므로 /refdocs 사용.

@app.get("/refdocs")
def list_refdocs() -> list[dict]:
    """인덱싱된 참고 문서 목록: [{name, chunks}]."""
    from app.embedding.docs_index import DocsIndexer
    return DocsIndexer().list_sources()


@app.post("/refdocs/upload")
async def upload_refdoc(file: UploadFile) -> dict:
    """참고 문서(PDF/TXT/MD) 업로드 → docs/ 저장 → 즉시 인덱싱.
    국세청 『개정세법 해설』처럼 연 1회 발간 자료를 담당자가 직접 올린다."""
    from app.embedding.docs_index import ALLOWED_SUFFIXES, DocsIndexer

    name = Path(file.filename or "").name  # 경로 성분 제거
    if not name or Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"지원 형식: {', '.join(sorted(ALLOWED_SUFFIXES))}",
        )

    indexer = DocsIndexer()
    indexer.docs_dir.mkdir(parents=True, exist_ok=True)
    target = indexer.docs_dir / name
    target.write_bytes(await file.read())

    try:
        chunks = indexer.index_file(target)
    except Exception as e:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"인덱싱 실패: {e}")
    if chunks == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail="추출된 텍스트가 없습니다 — 스캔 이미지 PDF는 지원하지 않습니다.",
        )
    return {"name": name, "chunks": chunks}


@app.delete("/refdocs/{name}")
def delete_refdoc(name: str) -> dict:
    """참고 문서와 그 인덱스를 함께 삭제."""
    from app.embedding.docs_index import DocsIndexer
    DocsIndexer().delete_source(name)
    return {"deleted": name}


@app.post("/changes/{change_id}/analyze")
def analyze(change_id: int, force: bool = False, db: Session = Depends(get_session)) -> dict:
    """
    LLM(설정된 백엔드)으로 법령 변경 조문을 분석하여 ai_summary, ai_impact를 DB에 저장한다.
    이미 분석된 건은 재분석하지 않는다. force=true 로 강제 재분석 가능.
    """
    row = db.get(LawChange, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="변경 건을 찾을 수 없습니다.")
    if row.ai_summary and not force:
        return {"skipped": True, "reason": "이미 분석된 건입니다. 재분석하려면 ?force=true 를 사용하세요.", "id": change_id}

    # 참고 문서(개정세법 해설 등)에서 관련 발췌를 컨텍스트로 주입
    context = f"{row.law_name} {row.article_no}"
    try:
        from app.embedding.docs_index import DocsIndexer
        doc_hits = DocsIndexer().search(
            f"{row.law_name} {row.article_no} {(row.before_text or '')[:200]}", k=2,
        )
        if doc_hits:
            context += "\n\n[참고 자료 발췌]\n" + "\n---\n".join(
                f"({h['source']}) {h['snippet'][:600]}" for h in doc_hits
            )
    except Exception:
        pass  # 참고 문서는 보강일 뿐 — 실패해도 분석은 진행

    from app.llm.common import analyze_with_retry
    llm = get_llm_client()
    # JSON 형식 이탈 시 1회 재포맷 재시도 (로컬 소형 모델 보정)
    result = analyze_with_retry(
        llm,
        before=row.before_text or "",
        after=row.after_text or "",
        context=context,
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

    # 같은 파일의 여러 청크가 동일 (path, symbol)로 반환될 수 있으므로
    # 최고 점수 청크만 남긴다 (hits는 이미 score 내림차순).
    best: dict[tuple[str, str], object] = {}
    for hit in hits:
        key = (hit.path, hit.symbol)
        if key not in best:
            best[key] = hit

    # 후보 = (path, symbol, confidence, source) 통합 목록.
    candidates: list[tuple[str, str, float, str]] = [
        (h.path, h.symbol, h.score, "rag") for h in best.values()
    ]

    # ── 사전 기반 부트스트랩 ──────────────────────────────────────
    # 법령 텍스트 ↔ 컬럼코드 '정확 어휘 일치' → 컬럼코드를 symbol로 고신뢰 시드.
    # 암호 컬럼명을 임베딩이 못 잡는 한계를 보완한다.
    from app.embedding.term_dict import load, load_locations, match_codes, rank_locations

    table = load(settings.repo_root)
    loc = load_locations(settings.repo_root)
    dict_matches = match_codes(query, table)
    for code, term, score in dict_matches:
        conf = round(min(0.99, 0.7 + score / 100), 3)  # 0.7~0.99 (퍼지 RAG보다 항상 위)
        for path in rank_locations(loc.get(code, []))[:3]:
            candidates.append((path, code, conf, "dict"))

    # ── 상수 인벤토리 부트스트랩 ──────────────────────────────────
    # 개정문의 금액·세율(연 15만원 → 150000, 100분의 6 → 0.06)을 코드 숫자
    # 리터럴과 '정확 값 일치'시켜 시드. 수치 개정에서 임베딩보다 정밀하다.
    from app.embedding.const_inventory import load_inventory, match_constants

    inv = load_inventory(settings.repo_root or "mock_repo")
    const_matches = match_constants(query, inv)
    for value, expr, score, files in const_matches:
        conf = round(min(0.99, 0.75 + score / 40), 3)  # 0.75~0.99
        for path in rank_locations(files)[:3]:
            candidates.append((path, value, conf, "const"))

    if not candidates:
        return {"mapped": 0, "note": "인덱싱된 코드도, 사전 매칭도 없습니다. POST /index 를 먼저 실행하세요."}

    repo_name = (settings.repo_root.replace("\\", "/").rstrip("/").split("/")[-1]
                 or "mock_repo")
    article_id = f"{row.law_id}:{row.article_no}"
    saved = 0
    for path, symbol, conf, _src in candidates:
        exists = (
            db.query(Mapping)
            .filter_by(article_id=article_id, path=path, symbol=symbol)
            .first()
        )
        if exists:
            continue
        db.add(Mapping(
            article_id=article_id,
            repo=repo_name,
            path=path,
            symbol=symbol,
            change_type=row.change_type or "unknown",
            confidence=conf,
            verified=False,
        ))
        saved += 1

    db.commit()
    return {
        "law_change_id": change_id,
        "rag_hits": [{"path": h.path, "symbol": h.symbol, "score": h.score} for h in hits],
        "dict_matches": [
            {"code": c, "term": t, "score": s} for c, t, s in dict_matches
        ],
        "const_matches": [
            {"value": v, "expr": e, "score": s, "files": f}
            for v, e, s, f in const_matches
        ],
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
    매핑된 코드 스니펫 + 법령 변경 diff → LLM(설정된 백엔드)으로 수정 초안(unified diff) 생성.
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

    # VO 포함 보강: 매핑된 컬럼코드(symbol=n0200 등)가 '선언된' VO(.java)를 컨텍스트에 강제 포함.
    # 매핑이 매퍼 XML에 치우쳐 자바 필드 선언이 누락되는 것을 방지한다.
    from app.embedding.term_dict import CODE_RE as _CODE_RE, load_locations as _load_loc
    _loc = _load_loc(settings.repo_root)
    for m in mappings:
        if not (m.symbol and _CODE_RE.fullmatch(m.symbol)):
            continue
        for vo_path in _loc.get(m.symbol, []):
            if not vo_path.endswith(".java") or vo_path in seen_paths:
                continue
            seen_paths.add(vo_path)
            try:
                snippets.append(f"// {vo_path} [VO 선언: {m.symbol}]\n{adapter.read_file(vo_path)}")
            except FileNotFoundError:
                pass

    law_diff = (
        f"[법령] {row.law_name} {row.article_no}\n\n"
        f"[개정 전]\n{row.before_text or '(내용 없음)'}\n\n"
        f"[개정 후]\n{row.after_text or '(내용 없음)'}\n\n"
        f"[AI 요약]\n{row.ai_summary or ''}\n"
        f"[AI 영향 분석]\n{row.ai_impact or ''}"
    )

    llm = get_llm_client()
    # 앵커 편집 → unified diff 변환. 앵커 불일치 시 원본 발췌를 보여주며 자동 재시도
    from app.llm.common import propose_and_build
    diff_text, warnings, applied, raw_edits = propose_and_build(
        llm, law_diff=law_diff, code_snippets=snippets, read_file=adapter.read_file,
    )
    if not diff_text.strip():
        diff_text = (
            "# 자동 적용 가능한 변경을 만들지 못했습니다. 모델 제안 원문:\n#\n"
            + "\n".join("# " + line for line in raw_edits.splitlines())
        )
    if warnings:
        diff_text = (
            "# ⚠ 적용 경고 — 담당자 확인 필요\n"
            + "\n".join(f"#   - {w}" for w in warnings)
            + "\n\n" + diff_text
        )

    # ── 골든 테스트 검증 (설정된 경우) ─────────────────────────────
    # 스크래치 사본에 diff를 적용해 계산 기대값 대조 — 실제 repo는 건드리지 않는다.
    golden = None
    if settings.golden_test_cmd:
        from app.golden import run_golden_tests
        golden = run_golden_tests(
            settings.repo_root or MOCK_REPO_ROOT, diff_text,
            settings.golden_test_cmd, settings.golden_test_timeout_seconds,
        )

    proposal = PatchProposal(
        law_change_id=change_id,
        mapping_id=mappings[0].id,
        diff=diff_text,
        model_used=llm.model,
        approval_status="draft",
        golden_status=golden["status"] if golden else None,
        golden_output=golden["output"] if golden else None,
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
        "edits_applied": applied,
        "warnings": warnings,
        "golden": golden,               # None=미설정, 아니면 {status, output, duration_s}
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
            "golden_status": p.golden_status,
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
            "golden_status": p.golden_status,
            "model_used": p.model_used,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "diff": p.diff,
        })
    return result


@app.post("/proposals/{proposal_id}/golden")
def run_golden(proposal_id: int, db: Session = Depends(get_session)) -> dict:
    """저장된 초안에 골든 테스트를 실행한다 — 승인 전 계산 검증 (수동 트리거).
    apply 시 자동 실행되지만, 골든 케이스 갱신 후 재검증할 때 이 엔드포인트를 쓴다."""
    proposal = db.get(PatchProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")

    from app.golden import run_golden_tests
    result = run_golden_tests(
        settings.repo_root or MOCK_REPO_ROOT, proposal.diff,
        settings.golden_test_cmd, settings.golden_test_timeout_seconds,
    )
    proposal.golden_status = result["status"]
    proposal.golden_output = result["output"]
    db.commit()
    return {"proposal_id": proposal_id, **result}


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

    resp = {
        "proposal_id": proposal_id,
        "approval_status": "approved",
        "patch_written_to": patch_path,
    }
    # 승인은 사람의 결정이므로 막지 않되, 골든 테스트 미통과 사실은 명시한다
    if proposal.golden_status in ("failed", "apply_failed", "error"):
        resp["warning"] = (
            f"골든 테스트 결과가 '{proposal.golden_status}'인 초안을 승인했습니다. "
            "계산 검증이 통과되지 않았으니 수동 확인을 권장합니다."
        )
    return resp


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
