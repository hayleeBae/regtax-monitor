# Retrieval Orchestration and Experiment Specification

- 문서 상태: Implementation Ready
- 관련 Issue: `#0008`, `#0009`, `#0010`, `#0016`, `#0020`
- 버전: `retrieval-spec-v1`

## 1. 목적

RAG, 용어 사전, 상수 매칭, 검증 매핑, 코드 그래프를 공통 후보 모델로 통합하고 각 방식의 기여를 동일 데이터셋으로 비교한다.

## 2. 원칙

- provider 원점수 보존
- 서로 다른 점수 scale 정규화
- 후보별 검색 근거 제공
- verified mapping도 현재 코드에서 재검증
- provider 실패 격리
- 점수 산식 version 관리
- ablation 결과 없이 성능 향상 주장 금지

## 3. 후보 모델

```python
@dataclass(frozen=True)
class CandidateLocation:
    path: str
    symbol: str | None
    line_start: int | None
    line_end: int | None
    content_hash: str | None

@dataclass(frozen=True)
class RetrievalEvidence:
    source: RetrievalSource
    raw_score: float | None
    normalized_score: float
    matched_terms: tuple[str, ...]
    matched_values: tuple[str, ...]
    explanation: str
    provider_version: str

@dataclass
class RetrievalCandidate:
    location: CandidateLocation
    evidences: list[RetrievalEvidence]
    final_score: float
    rank: int | None
    verified_state: str | None
    stale: bool
```

## 4. Provider 계약

```python
class RetrievalProvider(Protocol):
    source: RetrievalSource
    version: str
    def retrieve(self, query: RetrievalQuery, context: RetrievalContext) -> ProviderResult: ...
```

Provider: verified, rag, dictionary, constants, graph.

## 5. Query

법령 원문, summary/impact, classification, normalized change, domain, repository commit, top-k를 전달한다. provider가 각자 법령을 재파싱하지 않는다.

## 6. Provider별 요구

### RAG

기존 indexer adapter. query builder version, embedding model, index snapshot, chunk 위치를 보존한다.

### Dictionary

기존 term_dict 재사용. matched identifier와 한글 설명을 evidence에 저장한다. 일반적인 짧은 용어는 stop term 처리한다.

### Constants

기존 const_inventory 재사용. before value 우선 검색, kind 구분, 동일 숫자 과다 등장 시 context 감점.

### Verified

stable law/article, change type, domain, decision history를 사용한다. path/symbol/hash를 현재 commit에서 확인하고 stale을 판정한다.

### Graph

초기 비활성. seed candidate의 연관 파일을 확장하며 단독으로 자동 patch 근거가 되지 않는다.

## 7. 중복 제거

기본 key는 `normalized_path + symbol`, symbol이 없으면 line bucket을 사용한다. evidence는 합치고 provider별 최고 점수를 유지한다. content hash 충돌은 분리하고 warning을 남긴다.

## 8. 점수 정규화

`ScoreNormalizer`를 둔다.

- RAG: similarity 0~1
- Dictionary: provider 범위 scaling
- Constants: exact 1.0, normalized 0.9, weak 0.6
- Verified: valid 1.0, partial 0.7, stale 0
- Graph: 관계별 0.3~0.75

normalization version을 기록한다.

## 9. 최종 점수

초기 가중치:

```text
verified 0.35, constant 0.25, dictionary 0.20, rag 0.15, graph 0.05
```

추가:

- 2개 provider: +0.05
- 3개 이상: +0.10
- stale: -0.50
- legacy: -0.15
- generated: -0.20
- broad constant: -0.15
- module conflict: -0.20

수치는 설정과 version으로 관리하고 평가 후 조정한다.

## 10. Orchestrator

```python
class RetrievalOrchestrator:
    def retrieve(self, query: RetrievalQuery, config: RetrievalExperimentConfig) -> RetrievalResponse: ...
```

순서: provider 선택 → 호출 → 오류 격리 → normalize → dedup → score → stale validation → rank → top-k.

## 11. 응답

후보, provider 실행 상태, scoring version, query hash, repository commit, warning, duration을 포함한다.

기존 UI 호환을 위해 `rag_hits`, `dict_matches`, `const_matches` compatibility view를 유지한다.

## 12. 실험 설정

```python
@dataclass(frozen=True)
class RetrievalExperimentConfig:
    experiment_id: str
    use_verified: bool
    use_rag: bool
    use_dictionary: bool
    use_constants: bool
    use_graph: bool
    top_k_per_provider: int
    final_top_k: int
    scoring_version: str
    normalization_version: str
```

실험: `rag_only`, `rag_dict`, `rag_const`, `hybrid_all`, `verified_hybrid`, `graph_hybrid`.

## 13. 고정 조건

같은 dataset, repo commit/hash, embedding model, index snapshot, query builder, top-k, normalization version을 사용한다.

## 14. 보고서

- R@1, R@5, MRR, P@5, latency
- 유형별 결과
- provider contribution
- 기준선 대비 rank 하락 case
- provider failure count

`hybrid_all`이 항상 높아야 한다는 테스트는 금지한다. 저하도 그대로 보고한다.

## 15. Verified reranking

exact article + same type에 강한 boost, compatible type에 중간 boost. stale과 rejection 문맥은 penalty. 검증 이력만으로 다른 exact evidence를 제거하지 않는다.

## 16. 오류

- RAG 실패 + 다른 성공: partial
- all provider 실패: error
- malformed path/NaN score: 후보 제외
- verified DB 실패: warning
- commit unknown: 결과는 반환하되 policy에 전달

## 17. 테스트

중복 제거, score normalization, multi-source bonus, stale/legacy penalty, provider failure isolation, compatibility response, mock 자녀공제/최저임금/암호 컬럼/동일 숫자 다수/stale verified.

## 18. 수용 기준

- 기존 3개 검색을 공통 후보로 변환
- 근거와 점수 보존
- provider on/off
- 실험 보고서
- stale 검증
- 기존 map API 호환

## 19. Claude Code 요청문

```text
Issue #0008, #0009, #0010을 순서대로 구현하라.

기존 indexer, term_dict, const_inventory를 재구현하지 말고 adapter로 감싼다.
Provider 실패를 격리하고 raw/normalized score와 evidence를 보존한다.
기존 map API 호환 필드를 유지한다.

#0010은 EVALUATION_SPEC runner를 사용해 4개 이상 실험을 같은 fixture에서 비교한다.
점수는 version과 설정으로 관리한다.
```
