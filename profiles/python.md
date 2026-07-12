# 프로파일: Python (FastAPI / 스크립트 / 데이터 파이프라인)

## 기술 스택 (CLAUDE.md에 복사 후 수정)

- Python {버전: 3.11+}
- {프레임워크: FastAPI / Flask / 없음(CLI)}
- {DB: SQLite / PostgreSQL} + {SQLAlchemy / raw SQL}
- {기타: ChromaDB, anthropic SDK 등}

## verify.sh 블록 (scripts/verify.sh에 복사)

**quick** (Stop hook — 수 초 내):

```bash
ruff check .
```

**full** (step AC / 리뷰 — quick 이후 실행됨):

```bash
pytest -q
```

타입 검사까지 하려면 full에 `mypy . --ignore-missing-imports`를 추가한다.


## 개별 명령어 (CLAUDE.md "명령어" 섹션에 추가)

```bash
uvicorn app.main:app --reload    # 개발 서버 (FastAPI)
pip install -r requirements.txt
```

## CRITICAL 규칙 후보

- 의존성은 requirements.txt(또는 pyproject.toml)에만 추가. 세션 중 임의 pip install 후 미기록 금지
- API 키/시크릿은 .env + python-dotenv로만 관리
- 외부 API 호출부는 재시도/타임아웃을 반드시 명시

## 흔한 함정

- 가상환경 미활성 상태에서 시스템 파이썬에 설치되는 문제 → verify.sh 첫 줄에서 venv 확인 고려
- 사내망 SSL 인증서 문제 → pip `--trusted-host` 또는 사내 인증서 등록으로 해결 (코드에서 verify=False 하드코딩 금지)

## security 블록 (verify.sh security에 추가)

```bash
pip-audit
```

## CI 블록 (.github/workflows/ci.yml 셋업 step)

```yaml
- uses: actions/setup-python@v5
  with: { python-version: '3.11', cache: pip }
- run: pip install -r requirements.txt
```
