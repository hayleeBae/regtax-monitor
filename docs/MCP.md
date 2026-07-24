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

Claude Code에서도 동일한 stdio command와 args를 MCP 서버로 등록한다.
클라이언트 버전에 따라 설정 명령과 파일 위치가 달라질 수 있으므로, 회사
PC에 설치된 버전의 MCP 도움말을 기준으로 등록한다.

## 보안 경계

- 서버는 로컬 SQLite와 `data/audit/`만 읽는다.
- 실제 eHR 저장소 파일을 임의 경로로 읽는 도구는 없다.
- DB나 파일을 변경하는 MCP 도구는 없다.
- artifact API는 절대경로 대신 상대경로와 해시만 반환한다.
- 사람 승인 게이트와 patch 적용은 기존 애플리케이션 경로에만 남아 있다.
