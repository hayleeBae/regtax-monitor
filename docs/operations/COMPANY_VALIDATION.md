# Company Mac Validation Runbook

실제 경로·코드·검색 결과는 이 문서나 Git에 기록하지 않는다. 아래 결과를 공유할
때도 경로와 코드 원문은 가리고 건수·상태·순위만 전달한다.

## 1. 최신화와 기본 검증

```bash
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python --version
bash scripts/verify.sh full
bash scripts/verify.sh security
```

## 2. 환경 설정

`.env`에 `REPO_ROOT`, 필요한 경우 `REPO_INDEX_PATHS`, local LLM 설정을 입력한다.
`.env`는 커밋하지 않는다.

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

## 3. 실제 eHR 인덱싱

기존 mock `chroma_data`를 실제 index로 재사용하지 않는다. 삭제가 부담되면 회사
환경 전용 persist directory를 사용한다. 서버 자동 인덱싱 로그에서 파일 수, chunk
수, 소요시간, 오류를 기록한다.

## 4. 검색 확인

법령 사례 1~3건에 대해 상위 5개 파일을 확인하고 다음만 별도 private 메모에 기록한다.

```text
case id:
변경 유형:
상위 결과 순위:
정답 파일 rank:
맞음 / 일부 맞음 / 틀림:
provider 오류:
```

## 4-1. 검증 이력 rerank 발동 확인 (Issue #0016)

#0016 rerank는 검증 매핑의 `(path, symbol)`이 검색 후보의 `(path, symbol)`과
**정확히 일치**할 때만 적용된다(`CandidateLocation.dedup_key` 기준). 그런데 후보의
symbol은 검색원마다 다르다 — RAG는 청크 심볼, 용어 사전은 컬럼 코드, 상수 매칭은
값 문자열. 실제 `mapping` 테이블에 어떤 symbol이 쌓이느냐에 따라 rerank가 한 번도
발동하지 않을 수 있다. 집 mock의 ablation fixture는 파일 단위로 느슨하게 매칭해서
이 격차가 드러나지 않으므로, 회사에서만 확인 가능하다.

검증 매핑이 쌓인 뒤 사례 3~5건에 대해 아래를 기록한다(실제 경로·symbol 값은 적지
않고 일치 여부만).

```text
검증 매핑 수:
rerank 발동 건수 (응답 rerank_version 존재 + 순위 변동):
symbol 일치로 매칭된 건수 / symbol 불일치로 미매칭된 건수:
정답 파일 rank 변화 (rerank off → on):
stale 이력 후보의 rank 변화:
```

`stale 이력 후보의 rank 변화`는 별도 판단 대기 항목이다 — 현재 stale은 boost 제거에
더해 -0.50 penalty를 받아 이력이 전혀 없는 후보보다 아래로 내려간다(스펙 §10 문언
그대로). 정답이 부당하게 밀리는 사례가 관측되면 penalty를 중립화(boost 제거만)하는
방향으로 스펙 §10 수치 개정을 검토한다.

## 5. Private dataset

```bash
mkdir -p evaluation/private
cp evaluation/datasets/company_private.template.yaml evaluation/private/company.yaml
```

`company.yaml`에는 확인된 정답만 넣는다. `reviewed: true`는 담당자가 실제 관련
파일을 확인한 경우에만 사용한다. `evaluation/private/`, `evaluation/results/`,
`chroma_data/`, 각종 `*_cache.json`은 커밋하지 않는다.

## 6. 전달할 결과

```text
git pull: 성공/실패
Python: 3.10.x 여부
verify full/security: 성공/실패
Ollama 모델과 context length:
인덱싱 파일 수 / chunk 수 / 소요시간:
검색 사례 수:
정답 rank 요약:
rerank 발동 건수 / symbol 미매칭 건수:
provider 오류:
기타 경고(context shift, timeout, SSL):
```

API 키, 사내 URL, 사용자명, 실제 코드, 실제 파일 경로는 전달하지 않는다.
