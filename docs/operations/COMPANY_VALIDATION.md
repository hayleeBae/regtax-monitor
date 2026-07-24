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
provider 오류:
기타 경고(context shift, timeout, SSL):
```

API 키, 사내 URL, 사용자명, 실제 코드, 실제 파일 경로는 전달하지 않는다.
