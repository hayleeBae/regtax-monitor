"""LocalClient 서버 실효 컨텍스트 대조 (_verify_server_context) 테스트.

F-20260712-0001 잔여 리스크 개선: LOCAL_LLM_NUM_CTX(경고 기준)와 서버
OLLAMA_CONTEXT_LENGTH(실효 값)의 수동 동기화 구조 — 불일치를 첫 호출 시 감지한다.
실제 서버 없이 httpx만 모킹한다.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.llm.local_client import LocalClient

CHAT_RESPONSE = {"choices": [{"message": {"content": "ok"}}]}


@pytest.fixture(autouse=True)
def reset_ctx_check_state():
    """클래스 공유 상태(프로세스당 1회 체크)를 테스트마다 초기화."""
    LocalClient._ctx_check_done = False
    LocalClient._ctx_check_attempts = 0
    yield
    LocalClient._ctx_check_done = False
    LocalClient._ctx_check_attempts = 0


def _resp(payload):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    return r


def _fake_http(show_parameters=None, ps_models=None, show_error=None):
    """URL별로 분기하는 httpx.post/get 대역을 만든다."""
    calls = {"show": 0, "ps": 0, "chat": 0}

    def fake_post(url, json=None, timeout=None):
        if url.endswith("/api/show"):
            calls["show"] += 1
            if show_error is not None:
                raise show_error
            return _resp({"parameters": show_parameters or ""})
        calls["chat"] += 1
        return _resp(CHAT_RESPONSE)

    def fake_get(url, timeout=None):
        calls["ps"] += 1
        return _resp({"models": ps_models or []})

    return fake_post, fake_get, calls


def _run_complete(fake_post, fake_get, n=1):
    with patch("app.llm.local_client.httpx.post", side_effect=fake_post), \
         patch("app.llm.local_client.httpx.get", side_effect=fake_get):
        client = LocalClient()
        results = [client.complete("테스트 프롬프트") for _ in range(n)]
    return client, results


def test_warns_when_server_ctx_smaller(capsys):
    fake_post, fake_get, _ = _fake_http(show_parameters="num_ctx 4096")
    client, results = _run_complete(fake_post, fake_get)

    assert results == ["ok"]
    out = capsys.readouterr().out
    assert "불일치" in out
    assert "4,096" in out                                   # 실효 값
    assert f"{client.num_ctx:,}" in out                     # 기대 값
    assert f"OLLAMA_CONTEXT_LENGTH={client.num_ctx}" in out  # 해결 명령
    assert "context shift" in out                            # 축소 방향의 위험 명시


def test_no_warning_when_matched(capsys):
    client_ctx = LocalClient().num_ctx
    fake_post, fake_get, _ = _fake_http(show_parameters=f"num_ctx {client_ctx}")
    _, results = _run_complete(fake_post, fake_get)

    assert results == ["ok"]
    assert "불일치" not in capsys.readouterr().out


def test_ps_fallback_when_show_has_no_num_ctx(capsys):
    # OLLAMA_CONTEXT_LENGTH로만 설정된 경우: /api/show 파라미터에는 num_ctx가 없고
    # 로드된 모델의 실효 값은 /api/ps에 나타난다
    fake_post, fake_get, calls = _fake_http(
        show_parameters="temperature 0.7",
        ps_models=[{"name": "qwen3:8b", "model": "qwen3:8b", "context_length": 4096}],
    )
    _, results = _run_complete(fake_post, fake_get)

    assert results == ["ok"]
    assert calls["ps"] == 1
    assert "불일치" in capsys.readouterr().out


def test_silently_skips_non_ollama_server(capsys):
    # /api/show가 없는 서버(vLLM 등) — 본 호출은 정상, 경고 없음, 재시도도 없음
    fake_post, fake_get, calls = _fake_http(show_error=httpx.ConnectError("no /api/show"))
    _, results = _run_complete(fake_post, fake_get, n=2)

    assert results == ["ok", "ok"]
    assert "불일치" not in capsys.readouterr().out
    assert calls["show"] == 1          # 첫 실패 후 결론(건너뜀) — 재조회 안 함
    assert LocalClient._ctx_check_done is True


def test_check_concludes_once(capsys):
    # 결론(값 확보) 후에는 다시 조회하지 않는다
    fake_post, fake_get, calls = _fake_http(show_parameters="num_ctx 4096")
    _, results = _run_complete(fake_post, fake_get, n=3)

    assert results == ["ok"] * 3
    assert calls["show"] == 1
    assert calls["chat"] == 3
    assert capsys.readouterr().out.count("불일치") == 1


def test_retries_until_model_loaded_then_gives_up():
    # 판단 불가(모델 미로드, ps 빈 목록)가 계속되면 상한(3회)까지만 시도
    fake_post, fake_get, calls = _fake_http(show_parameters="", ps_models=[])
    _, results = _run_complete(fake_post, fake_get, n=5)

    assert results == ["ok"] * 5
    assert calls["show"] == 3
    assert LocalClient._ctx_check_done is False
