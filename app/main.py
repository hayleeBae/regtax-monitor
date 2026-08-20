from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.codebase.mock_adapter import MockCodebaseAdapter
from app.codebase.real_adapter import RealCodebaseAdapter
from app.collector.law_api import ApiNotGrantedError, LawApiClient
from app.collector.registry import DbDataRegistry, load_domains
from app.db.database import init_db, get_session
from app.db.models import LawChange, Mapping, PatchProposal, Review, SyncState
from app.domain.mappings.decisions import (
    MappingDecisionRecord,
    MappingDecisionType,
    allowed_reason_codes,
    resolve_state,
)
from app.embedding.indexer import CodeIndexer
from app.mappings.repository import SqlAlchemyMappingDecisionRepository
from app.llm import get_llm_client
from app.audit.sanitizer import stable_settings_hash
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


def _make_mapping_service(db: Session, article_id: str):
    """기존 네 검색원을 #0009 공통 orchestrator로 조합한다."""
    from app.application.services import MappingService
    from app.embedding import symbol_index
    from app.retrieval.orchestrator import RetrievalOrchestrator
    from app.retrieval.providers import (
        ConstantProvider, DictionaryProvider, RagProvider,
        VerifiedMappingProvider, VerifiedMappingRecord,
    )

    indexer = CodeIndexer()
    adapter = _make_adapter(indexer=indexer)
    graph = symbol_index.load(adapter)

    def verified_lookup(_query):
        rows = db.query(Mapping).filter_by(article_id=article_id, verified=True).all()
        records = []
        for mapping in rows:
            try:
                adapter.read_file(mapping.path)
                valid = True
            except (FileNotFoundError, OSError):
                valid = False
            records.append(
                VerifiedMappingRecord(
                    mapping.path, mapping.symbol, valid, content_hash=mapping.code_hash
                )
            )
        return tuple(records)

    repo_root = settings.repo_root or MOCK_REPO_ROOT
    providers = (
        VerifiedMappingProvider(verified_lookup),
        RagProvider(lambda text, k: adapter.search(text, k=k)),
        DictionaryProvider(settings.repo_root),
        ConstantProvider(repo_root),
    )
    # provider(후보 생성)와 reranker(순위 조정)는 별개 역할이다 — 위 verified_lookup을
    # 대체하지 않고 검증 이력 문맥만 덧붙인다(ADR-009).
    reranker = None
    if settings.verified_reranking_enabled:
        from app.mappings.reranking_lookup import SqlAlchemyDecisionContextLookup

        reranker = SqlAlchemyDecisionContextLookup(db, article_id)
    orchestrator = RetrievalOrchestrator(providers, reranker=reranker, graph=graph)
    return MappingService(orchestrator), adapter


def _start_audit(db: Session, run_type, row):
    from app.audit.integration import AuditScope
    from app.domain.changes.normalization import ChangeNormalizer

    normalized = ChangeNormalizer().normalize(row.before_text or "", row.after_text or "")
    return AuditScope.start(
        db,
        run_type,
        law_change_id=row.id,
        source_hash=normalized.source_hash,
        settings_hash=stable_settings_hash(
            {
                "llm_backend": settings.llm_backend,
                "llm_model": settings.local_llm_model
                if settings.llm_backend == "local"
                else settings.llm_model,
                "embedding_model": settings.embedding_model,
                "local_llm_num_ctx": settings.local_llm_num_ctx,
            }
        ),
        repository_alias=(
            Path(settings.repo_root).name if settings.repo_root else "mock_repo"
        ),
        llm_backend=settings.llm_backend,
        llm_model=(
            settings.local_llm_model
            if settings.llm_backend == "local"
            else settings.llm_model
        ),
        embedding_model=settings.embedding_model,
        prompt_versions={"analysis": "analysis-v1", "classification": "classification-v1"},
    )


def _write_audit_manifest(audit, artifacts: list, metadata: dict) -> None:
    """Artifact 복제 실패는 업무 결과를 무효화하지 않고 부분 실패로 표시한다."""
    if audit.run_id is None:
        return
    try:
        from app.audit.artifacts import LocalArtifactStore

        LocalArtifactStore(settings.audit_artifact_dir).write_manifest(
            audit.run_id,
            artifacts,
            {
                "settings_hash": stable_settings_hash(metadata),
                "model": metadata.get("model"),
                "prompt_versions": metadata.get("prompt_versions", {}),
                "repository_alias": metadata.get("repository_alias"),
                "repository_commit": metadata.get("repository_commit"),
                "source_hash": metadata.get("source_hash"),
                "replayability": "inspection_only",
            },
        )
    except Exception as exc:
        audit.incomplete = True
        audit.error = f"artifact write failed: {exc}"


def _store_audit_json(audit, artifact_type: str, payload: dict):
    if audit.run_id is None:
        return None
    try:
        from app.audit.artifacts import LocalArtifactStore
        from app.audit.sanitizer import sanitize_payload

        content = json.dumps(
            sanitize_payload(payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        return LocalArtifactStore(settings.audit_artifact_dir).put_bytes(
            audit.run_id,
            artifact_type,
            content,
            ".json",
            media_type="application/json",
            redacted=True,
        )
    except Exception as exc:
        audit.incomplete = True
        audit.error = f"artifact write failed: {exc}"
        return None


def _audit_manifest_metadata(row, *, model: str | None = None) -> dict:
    from app.domain.changes.normalization import ChangeNormalizer

    return {
        "model": model,
        "prompt_versions": {
            "analysis": "analysis-v1",
            "classification": "classification-v1",
        },
        "repository_alias": (
            Path(settings.repo_root).name if settings.repo_root else "mock_repo"
        ),
        "source_hash": ChangeNormalizer()
        .normalize(row.before_text or "", row.after_text or "")
        .source_hash,
        "llm_backend": settings.llm_backend,
        "embedding_model": settings.embedding_model,
    }


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
            print("[스케줄러] 법령 수집 완료")
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
    title="법령 변경 모니터링 (세법·인사)",
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


@app.get("/runs/{run_id}")
def get_execution_run(run_id: str, db: Session = Depends(get_session)) -> dict:
    """경로·시크릿을 제외한 실행 단위 감사 메타데이터를 조회한다."""
    from app.audit.repository import SqlAlchemyAuditRepository
    from app.domain.common.serialization import to_jsonable

    try:
        run = SqlAlchemyAuditRepository(db).get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="실행 기록을 찾을 수 없습니다.") from exc
    return to_jsonable(run)


@app.get("/runs/{run_id}/events")
def get_execution_run_events(
    run_id: str,
    db: Session = Depends(get_session),
) -> list[dict]:
    """append-only 감사 이벤트를 발생 순서대로 조회한다."""
    from app.audit.repository import SqlAlchemyAuditRepository
    from app.domain.common.serialization import to_jsonable

    repository = SqlAlchemyAuditRepository(db)
    try:
        repository.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="실행 기록을 찾을 수 없습니다.") from exc
    return [to_jsonable(event) for event in repository.list_events(run_id)]


@app.get("/runs/{run_id}/artifacts")
def get_execution_run_artifacts(
    run_id: str,
    db: Session = Depends(get_session),
) -> list[dict]:
    """manifest에 기록된 artifact 참조와 현재 무결성 상태를 조회한다."""
    from app.audit.artifacts import LocalArtifactStore
    from app.audit.replay import InspectionReplay
    from app.audit.repository import SqlAlchemyAuditRepository

    try:
        SqlAlchemyAuditRepository(db).get_run(run_id)
        replay = InspectionReplay(
            LocalArtifactStore(settings.audit_artifact_dir)
        ).inspect(run_id)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="감사 artifact를 찾을 수 없습니다.") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="감사 artifact가 손상되었습니다.") from exc
    return replay["artifacts"]


@app.post("/runs/{run_id}/replay")
def replay_execution_run(
    run_id: str,
    db: Session = Depends(get_session),
) -> dict:
    """LLM·코드 저장소를 호출하지 않는 inspection replay만 제공한다."""
    from app.audit.artifacts import LocalArtifactStore
    from app.audit.replay import InspectionReplay
    from app.audit.repository import SqlAlchemyAuditRepository

    try:
        SqlAlchemyAuditRepository(db).get_run(run_id)
        return InspectionReplay(
            LocalArtifactStore(settings.audit_artifact_dir)
        ).inspect(run_id)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="재현 정보를 찾을 수 없습니다.") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="재현 정보가 손상되었습니다.") from exc


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
            amendment_text=item.get("amendment_text", ""),
            reason_text=item.get("reason_text", ""),
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
                row.amendment_text = detail["amendment_text"]
                row.reason_text = detail["reason_text"]
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
    row.amendment_text = detail["amendment_text"]
    row.reason_text = detail["reason_text"]
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

    from app.domain.audit import AuditEventType, ExecutionRunType
    audit = _start_audit(db, ExecutionRunType.ANALYZE, row)
    audit.record(AuditEventType.ANALYSIS_REQUESTED, {"force": force})

    # 도메인 라벨(세법/노동·인사 등)을 맥락 첫 줄로 — 프롬프트 자체는 도메인 중립
    domain_label = ""
    try:
        dom = load_domains().get(row.domain or "tax")
        if dom:
            domain_label = f"[도메인] {dom.label}\n"
    except Exception:
        pass

    # 참고 문서(개정세법 해설 등)에서 관련 발췌를 컨텍스트로 주입
    context = f"{domain_label}{row.law_name} {row.article_no}"
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

    from app.application.services import AnalysisService
    from app.domain.changes.classification import HybridChangeClassifier
    from app.domain.changes.normalization import ChangeNormalizer
    from app.llm.common import analyze_with_retry
    llm = get_llm_client()
    service = AnalysisService(
        ChangeNormalizer(),
        HybridChangeClassifier(llm),
        lambda before, after, ctx, amendment_text="", reason_text="": analyze_with_retry(
            llm, before=before, after=after, context=ctx,
            amendment_text=amendment_text, reason_text=reason_text,
        ),
    )
    # 개정문 원문·제개정이유는 LLM 프롬프트 컨텍스트로만 전달한다 —
    # normalize/검색 질의의 before/after 자리에는 넣지 않는다(스펙 §2).
    result = service.analyze(
        row.before_text or "",
        row.after_text or "",
        context,
        amendment_text=row.amendment_text or "",
        reason_text=row.reason_text or "",
    )
    # 폴백 비율 계측용(스펙 §3-4): 저장된 개정문을 순수 파서로 다시 돌려 edit이
    # 잡히는지로 판정한다 — step 1의 amendment_parsed(bool(edits))와 같은 정의다.
    # (파싱 실패 폴백은 before="" / after=개정문 전문이라 필드 비교로는 구분되지 않는다.)
    from app.domain.changes.amendment import parse_amendment
    amendment_parsed = bool(parse_amendment(row.amendment_text or ""))
    audit.record(
        AuditEventType.NORMALIZATION_COMPLETED,
        {
            "normalizer_version": result.normalized.normalizer_version,
            "money_delta_count": len(result.normalized.money_changes),
            "rate_delta_count": len(result.normalized.rate_changes),
            "date_delta_count": len(result.normalized.date_changes),
        },
    )
    audit.record(
        AuditEventType.CLASSIFICATION_COMPLETED,
        {
            "primary_type": result.classification.primary_type.value,
            "confidence": result.classification.confidence,
            "source": result.classification.source.value,
        },
    )
    audit.record(
        AuditEventType.ANALYSIS_COMPLETED,
        {
            "parse_ok": result.parse_ok,
            "summary_length": len(result.summary),
            "amendment_parsed": amendment_parsed,
        },
    )

    row.ai_summary = result.summary
    row.ai_impact = result.impact
    row.change_type = result.classification.primary_type.value

    row.status = "reviewing"
    db.commit()
    artifacts = []
    if settings.audit_store_llm_raw_output:
        analysis_ref = _store_audit_json(
            audit,
            "analysis-output",
            {
                "summary": result.summary,
                "impact": result.impact,
                "parse_ok": result.parse_ok,
                "classification": result.classification.primary_type.value,
            },
        )
        if analysis_ref:
            artifacts.append(analysis_ref)
    _write_audit_manifest(
        audit,
        artifacts,
        _audit_manifest_metadata(row, model=llm.model),
    )
    audit.complete()

    return {
        "id": change_id,
        "summary": row.ai_summary,
        "impact": row.ai_impact,
        "parse_ok": result.parse_ok,
        "amendment_parsed": amendment_parsed,
        "classification": {
            "primary_type": result.classification.primary_type.value,
            "secondary_types": [item.value for item in result.classification.secondary_types],
            "confidence": result.classification.confidence,
            "source": result.classification.source.value,
            "reason": result.classification.reason,
        },
        "normalizer_version": result.normalized.normalizer_version,
        **audit.response_fields(),
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

    from app.domain.audit import AuditEventType, ExecutionRunType
    audit = _start_audit(db, ExecutionRunType.MAP, row)

    query = " ".join(filter(None, [
        row.law_name, row.article_no,
        row.ai_summary, row.before_text, row.after_text,
    ]))

    article_id = f"{row.law_id}:{row.article_no}"
    service, _adapter = _make_mapping_service(db, article_id)
    # change_type은 레거시 자유 문자열(rate/limit/…)일 수 있고 None일 수도 있다 — 그대로 넘긴다.
    result = service.map(
        query, top_k=k, article_id=article_id, change_type=row.change_type
    )
    audit.record(
        AuditEventType.RETRIEVAL_COMPLETED,
        {
            "candidate_count": len(result.candidates),
            "top_k": k,
            "provider_statuses": result.compatibility_payload.get(
                "provider_statuses", {}
            ),
        },
    )
    if not result.candidates:
        retrieval_ref = _store_audit_json(
            audit,
            "retrieval",
            result.compatibility_payload,
        )
        _write_audit_manifest(
            audit,
            [retrieval_ref] if retrieval_ref else [],
            _audit_manifest_metadata(row),
        )
        audit.complete()
        return {
            "mapped": 0,
            "note": "인덱싱된 코드도, 사전 매칭도 없습니다. POST /index 를 먼저 실행하세요.",
            **audit.response_fields(),
        }

    repo_name = (settings.repo_root.replace("\\", "/").rstrip("/").split("/")[-1]
                 or "mock_repo")
    saved = 0
    for candidate in result.candidates:
        path = candidate.location.path
        symbol = candidate.location.symbol or ""
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
            confidence=candidate.final_score,
            verified=False,
        ))
        saved += 1

    db.commit()
    retrieval_ref = _store_audit_json(
        audit,
        "retrieval",
        result.compatibility_payload,
    )
    _write_audit_manifest(
        audit,
        [retrieval_ref] if retrieval_ref else [],
        _audit_manifest_metadata(row),
    )
    audit.complete()
    return {
        "law_change_id": change_id,
        **result.compatibility_payload,
        "saved": saved,
        **audit.response_fields(),
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


def _load_mapping(db: Session, mapping_id: int) -> Mapping:
    m = db.get(Mapping, mapping_id)
    if m is None:
        raise HTTPException(status_code=404, detail="매핑을 찾을 수 없습니다.")
    return m


def _validate_reason_code(decision, reason_code: str | None) -> None:
    """reason_code allowlist 검증 (스펙 §3). None은 허용(선택 필드)."""
    if reason_code is None:
        return
    allowed = allowed_reason_codes(decision)
    if reason_code not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"'{decision.value}'에 허용되지 않는 reason_code: {reason_code}",
        )


def _record_mapping_decision(
    db: Session,
    mapping: Mapping,
    decision,
    *,
    reason_code: str | None = None,
    reason_text: str | None = None,
    actor: str | None = None,
):
    """결정 이벤트 append + `Mapping.verified` compat cache 갱신 (ADR-008).

    두 쓰기가 같은 트랜잭션이어야 cache 불일치(apply 오작동)를 막을 수 있으므로,
    commit 하는 쪽인 repository.append() 앞에서 cache 값을 session 에 올려둔다 —
    같은 commit 으로 이벤트 insert 와 cache update 가 함께 반영된다.
    유효성 스냅샷(commit/hash)은 #0015 에서 nullable best-effort 다.
    """
    record = MappingDecisionRecord(
        mapping_id=mapping.id,
        decision=decision,
        reason_code=reason_code,
        reason_text=reason_text,
        actor=actor or "owner",
        created_at=datetime.now(timezone.utc),
    )
    repo = SqlAlchemyMappingDecisionRepository(db)
    state = resolve_state(repo.list_for_mapping(mapping.id) + (record,))
    mapping.verified = state is MappingDecisionType.VERIFIED
    decision_id = repo.append(record)
    return decision_id, state


def _db_value_delta(normalized) -> tuple[str, str]:
    """DB 갱신 안내의 before/after 값 델타 — ChangeNormalizer가 잡아낸 첫 값
    델타를 사용한다(DB_DATA_ROUTING_SPEC §6). 값 델타가 없으면 빈 문자열
    (추론하지 않는다)."""
    for deltas in (
        normalized.rate_changes,
        normalized.money_changes,
        normalized.date_changes,
        normalized.duration_changes,
        normalized.age_changes,
    ):
        if not deltas:
            continue
        delta = deltas[0]
        before = delta.before.raw if delta.before else ""
        after = delta.after.raw if delta.after else ""
        return before, after
    return "", ""


def _decision_payload(record) -> dict:
    """결정 1건 직렬화 — 코드 본문은 저장하지도, 반환하지도 않는다(스펙 §7)."""
    return {
        "decision": record.decision.value,
        "reason_code": record.reason_code,
        "reason_text": record.reason_text,
        "repository_commit": record.repository_commit,
        "path_hash": record.path_hash,
        "symbol_hash": record.symbol_hash,
        "actor": record.actor,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


@app.patch("/mappings/{mapping_id}/verify")
def verify_mapping(
    mapping_id: int,
    verified: bool = True,
    actor: str | None = None,
    reason_code: str | None = None,
    db: Session = Depends(get_session),
) -> dict:
    """
    담당자가 매핑 정확성을 검증한다.
    verified=true: 이 코드 위치가 해당 조문과 관련 있음을 확인 (VERIFIED 이벤트).
    verified=false: 직전 검증을 되돌린다 (REVOKED 이벤트, 이후 apply에서 제외됨).
    거절 사유를 남기려면 POST /mappings/{id}/decisions 로 REJECTED를 기록한다.
    """
    m = _load_mapping(db, mapping_id)
    decision = (
        MappingDecisionType.VERIFIED if verified else MappingDecisionType.REVOKED
    )
    _validate_reason_code(decision, reason_code)
    _record_mapping_decision(db, m, decision, reason_code=reason_code, actor=actor)
    return {"mapping_id": mapping_id, "path": m.path, "symbol": m.symbol, "verified": m.verified}


class MappingDecisionBody(BaseModel):
    decision: str
    reason_code: str | None = None
    reason_text: str | None = None
    actor: str | None = None


@app.post("/mappings/{mapping_id}/decisions", status_code=201)
def create_mapping_decision(
    mapping_id: int,
    body: MappingDecisionBody,
    db: Session = Depends(get_session),
) -> dict:
    """매핑 결정을 이력에 추가한다 (append-only — 수정·삭제 없음, 스펙 §6)."""
    m = _load_mapping(db, mapping_id)
    try:
        decision = MappingDecisionType(body.decision)
    except ValueError:
        allowed = ", ".join(d.value for d in MappingDecisionType)
        raise HTTPException(
            status_code=422, detail=f"알 수 없는 decision: {body.decision} (허용: {allowed})"
        )
    _validate_reason_code(decision, body.reason_code)
    decision_id, state = _record_mapping_decision(
        db,
        m,
        decision,
        reason_code=body.reason_code,
        reason_text=body.reason_text,
        actor=body.actor,
    )
    return {
        "mapping_id": mapping_id,
        "decision_id": decision_id,
        "decision": decision.value,
        "reason_code": body.reason_code,
        "state": state.value if state else None,
        "verified": m.verified,
    }


@app.get("/mappings/{mapping_id}/decisions")
def get_mapping_decisions(
    mapping_id: int,
    db: Session = Depends(get_session),
) -> list[dict]:
    """매핑 결정 이력을 시간순으로 반환한다."""
    _load_mapping(db, mapping_id)
    repo = SqlAlchemyMappingDecisionRepository(db)
    return [_decision_payload(r) for r in repo.list_for_mapping(mapping_id)]


@app.get("/mappings/{mapping_id}/state")
def get_mapping_state(
    mapping_id: int,
    db: Session = Depends(get_session),
) -> dict:
    """현재 상태 + 마지막 이유 + 검증 당시 commit (스펙 §12 UI 표시용)."""
    m = _load_mapping(db, mapping_id)
    repo = SqlAlchemyMappingDecisionRepository(db)
    history = repo.list_for_mapping(mapping_id)
    state = resolve_state(history)
    latest = history[-1] if history else None
    return {
        "mapping_id": mapping_id,
        "path": m.path,
        "symbol": m.symbol,
        "state": state.value if state else None,
        "verified": m.verified,
        "reason_code": latest.reason_code if latest else None,
        "reason_text": latest.reason_text if latest else None,
        "repository_commit": latest.repository_commit if latest else None,
        "actor": latest.actor if latest else None,
        "decided_at": latest.created_at.isoformat() if latest and latest.created_at else None,
        "decision_count": len(history),
    }


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

    from app.domain.audit import AuditEventType, ExecutionRunType
    audit = _start_audit(db, ExecutionRunType.APPLY, row)

    article_id = f"{row.law_id}:{row.article_no}"

    from app.application.services import DbUpdateGuidance, ProposalService
    from app.domain.changes.classification import RuleChangeClassifier
    from app.domain.changes.normalization import ChangeNormalizer
    from app.policy.automation import PolicyInput

    # DB 데이터 개정 라우팅(ADR-016, DB_DATA_ROUTING_SPEC §5) — 매핑 유무를
    # 확인하기 전에 먼저 판정한다. 매칭 건이 "사용 가능한 매핑 없음" 422로
    # 빠지면 안 되고, 매칭되면 매핑 조회·LLM patch 생성 경로에 진입하지 않는다.
    db_match = DbDataRegistry(load_domains()).match(row.law_id, row.article_no)
    if db_match is not None:
        normalized = ChangeNormalizer().normalize(row.before_text or "", row.after_text or "")
        classification = RuleChangeClassifier().classify(normalized)
        before, after = _db_value_delta(normalized)
        guidance = DbUpdateGuidance(
            item_label=db_match.item_label,
            law_name=row.law_name,
            article=row.article_no,
            before=before,
            after=after,
            guidance=db_match.guidance,
        )
        policy_input = PolicyInput(
            change_type=classification.primary_type,
            classification_confidence=classification.confidence,
            candidates=(),
            repository_commit=None,
            existing_files=frozenset(),
        )
        gate = ProposalService().propose(
            policy_input, lambda: {"allowed": True}, db_match=db_match, guidance=guidance,
        )
        audit.record(
            AuditEventType.POLICY_DECIDED,
            {
                "decision": gate.policy.decision.value,
                "reason_codes": [reason.code for reason in gate.policy.block_reasons],
                "policy_version": gate.policy.policy_version,
            },
        )
        audit.complete()
        return {
            "blocked": True,
            "law_change_id": change_id,
            "decision": gate.policy.decision.value,
            "item_label": guidance.item_label,
            "law_name": guidance.law_name,
            "article": guidance.article,
            "before": guidance.before,
            "after": guidance.after,
            "guidance": guidance.guidance,
            **audit.response_fields(),
        }

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
        audit.fail("retrieval_error", "사용 가능한 매핑이 없음")
        raise HTTPException(
            status_code=422,
            detail=f"사용 가능한 매핑이 없습니다. POST /changes/{change_id}/map 을 먼저 실행하세요.",
        )

    # 분류·검색 근거·repository 상태를 정책에 전달한다. 정책이 차단하면 LLM patch
    # 생성에 진입하지 않는다. analyze/map 조회 자체는 이 정책과 무관하게 유지된다.
    normalized = ChangeNormalizer().normalize(row.before_text or "", row.after_text or "")
    classification = RuleChangeClassifier().classify(normalized)
    mapping_service, adapter = _make_mapping_service(db, article_id)
    query = " ".join(filter(None, [
        row.law_name, row.article_no, row.ai_summary, row.before_text, row.after_text,
    ]))
    try:
        # map 엔드포인트와 같은 문맥을 넘겨 두 경로가 같은 후보 집합을 보게 한다.
        policy_candidates = mapping_service.map(
            query, top_k=5, article_id=article_id, change_type=row.change_type
        ).candidates
    except Exception:
        policy_candidates = ()
    policy_input = PolicyInput(
        change_type=classification.primary_type,
        classification_confidence=classification.confidence,
        candidates=policy_candidates,
        repository_commit=adapter.repository_revision(),
        existing_files=frozenset(adapter.list_files()),
        source_conflict=False,
    )
    gate = ProposalService().propose(policy_input, lambda: {"allowed": True})
    audit.record(
        AuditEventType.POLICY_DECIDED,
        {
            "decision": gate.policy.decision.value,
            "reason_codes": [reason.code for reason in gate.policy.block_reasons],
            "policy_version": gate.policy.policy_version,
        },
    )
    if gate.blocked:
        audit.complete()
        return {
            "blocked": True,
            "law_change_id": change_id,
            "decision": gate.policy.decision.value,
            "classification": classification.primary_type.value,
            "block_reasons": [
                {"code": reason.code, "message": reason.message}
                for reason in gate.policy.block_reasons
            ],
            "policy_version": gate.policy.policy_version,
            **audit.response_fields(),
        }

    # 매핑된 파일의 실제 코드를 읽어 스니펫 조합
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
    audit.record(
        AuditEventType.EDIT_REQUESTED,
        {"snippet_file_count": len(seen_paths), "mapping_mode": mapping_mode},
    )
    # 앵커 편집 → unified diff 변환. 앵커 불일치 시 원본 발췌를 보여주며 자동 재시도
    from app.llm.common import propose_and_build
    diff_text, warnings, applied, raw_edits = propose_and_build(
        llm, law_diff=law_diff, code_snippets=snippets, read_file=adapter.read_file,
    )
    audit.record(
        AuditEventType.EDIT_COMPLETED,
        {"edits_applied": applied, "warning_count": len(warnings)},
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
        audit.record(AuditEventType.GOLDEN_STARTED, {})
        from app.golden import run_golden_tests
        golden = run_golden_tests(
            settings.repo_root or MOCK_REPO_ROOT, diff_text,
            settings.golden_test_cmd, settings.golden_test_timeout_seconds,
        )
        audit.record(
            AuditEventType.GOLDEN_COMPLETED,
            {"status": golden["status"], "duration_s": golden.get("duration_s")},
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
    artifact_refs = []
    if settings.audit_store_code_snippets and audit.run_id:
        try:
            from app.audit.artifacts import LocalArtifactStore

            patch_ref = LocalArtifactStore(settings.audit_artifact_dir).put_bytes(
                audit.run_id,
                "proposal",
                diff_text.encode("utf-8"),
                ".patch",
                media_type="text/x-diff",
                contains_code=True,
            )
            artifact_refs.append(patch_ref)
            audit.record(
                AuditEventType.PATCH_BUILT,
                {"sha256": patch_ref.sha256, "size": patch_ref.size},
            )
        except Exception as exc:
            audit.incomplete = True
            audit.error = f"artifact write failed: {exc}"
    _write_audit_manifest(
        audit,
        artifact_refs,
        _audit_manifest_metadata(row, model=llm.model),
    )
    audit.record(
        AuditEventType.PROPOSAL_CREATED,
        {
            "proposal_id": proposal.id,
            "approval_status": proposal.approval_status,
            "diff_length": len(diff_text),
        },
    )
    audit.complete()

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
        **audit.response_fields(),
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
