# 읽기 전용 MCP 서버

이 서버는 Codex나 Claude Code가 `regtax-monitor`의 법령 변경, 실행 기록,
감사 이벤트와 산출물 무결성을 직접 조회하는 로컬 창구다. 승인, 거절, 수정,
patch 적용 도구는 제공하지 않는다.

## 제공 도구

- `list_changes`: 변경 목록 조회
- `get_change`: 개정 전후 법령과 AI 분석 조회
- `get_execution_run`: 모델·설정·상태 등 실행 메타데이터 조회
- `get_audit_events`: 실행 이벤트 순서 조회
- `get_run_artifacts`: 저장 산출물과 SHA-256 검증 결과 조회
- `get_patch_draft`: patch 메타데이터 조회. 본문은 기본적으로 숨김

회사 코드가 포함될 수 있는 patch 본문은 기본값
`MCP_EXPOSE_PATCH_DRAFTS=false`에서 노출되지 않는다. 검토 목적상 꼭 필요한
로컬 환경에서만 `true`로 바꾼다.

## 실행

프로젝트의 Python 3.10 가상환경에 의존성을 설치한 뒤 아래 명령으로 stdio
서버를 실행한다.

```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m app.mcp.server
```

stdio 서버는 터미널에서 직접 실행하면 입력을 기다리는 것이 정상이다.

## Codex 연결

Codex CLI와 IDE 확장은 같은 MCP 설정을 공유한다. 프로젝트 경로는 각 PC의
실제 절대경로로 바꾼다.

```toml
[mcp_servers.regtax-monitor]
command = "/absolute/path/regtax-monitor/.venv/bin/python"
args = ["-m", "app.mcp.server"]
cwd = "/absolute/path/regtax-monitor"
enabled = true
required = false
enabled_tools = [
  "list_changes",
  "get_change",
  "get_execution_run",
  "get_audit_events",
  "get_run_artifacts",
  "get_patch_draft",
]
```

설정 후 Codex를 재시작한다. `cwd`는 상대경로인 DB와 artifact 디렉터리가
항상 이 프로젝트 안에서 해석되도록 고정한다.

## Claude Code 연결

회사 맥북의 프로젝트 루트에서 다음 명령을 실행한다. Python 경로는 회사
맥북에서 `pwd`와 `which python`으로 확인한 실제 절대경로를 사용한다.

```bash
claude mcp add regtax-monitor --scope local -- \
  /absolute/path/regtax-monitor/.venv/bin/python \
  -m app.mcp.server
```

`--scope local`은 현재 프로젝트에서만 사용하고 설정을 Git에 커밋하지 않는
범위다. 회사별 절대경로와 내부 데이터 설정이 포함될 수 있으므로 기본
등록 방식으로 사용한다. 팀 공용 `.mcp.json`이 필요해지는 시점에는 경로를
환경변수로 치환한 뒤 별도 보안 검토를 거친다.

등록 상태는 아래 명령으로 확인한다.

```bash
claude mcp get regtax-monitor
claude mcp list
```

Claude Code를 실행한 뒤 `/mcp`에서도 연결 상태를 확인할 수 있다. 연결 후
다음과 같이 요청해 읽기 동작을 점검한다.

```text
regtax-monitor에서 최근 법령 변경 5건을 조회해줘.
가장 최근 실행의 audit event를 순서대로 보여줘.
해당 실행 artifact의 SHA-256 검증 상태를 확인해줘.
```

서버가 보이지 않으면 프로젝트 루트에서 등록했는지, `.venv`에
`requirements.txt`가 설치되었는지, `python -m app.mcp.server`가 실행되는지
순서대로 확인한다.

## 보안 경계

- 서버는 로컬 SQLite와 `data/audit/`만 읽는다.
- 실제 eHR 저장소 파일을 임의 경로로 읽는 도구는 없다.
- DB나 파일을 변경하는 MCP 도구는 없다.
- artifact API는 절대경로 대신 상대경로와 해시만 반환한다.
- 사람 승인 게이트와 patch 적용은 기존 애플리케이션 경로에만 남아 있다.
