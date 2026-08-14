"""인코딩 정책 (issue-0024 step 0).

2026-08-14 eHR 실측: `.xfdl`은 UTF-8+BOM, sqlmap XML 절반이 CP949, XML 선언과 실제
바이트가 불일치하는 파일(선언 EUC-KR·실제 UTF-8)이 존재한다. 읽기 정책을
`utf-8-sig → cp949 → utf-8(errors="replace")`로 통일해 BOM 잔존·주석 깨짐을 막는다.

- `utf-8-sig`는 BOM 유무 모두 처리한다(있으면 제거, 없으면 일반 utf-8과 동일).
- XML 선언의 encoding= 속성은 신뢰하지 않는다(선언·실제 불일치 파일 존재) — 바이트 폴백.
"""

from __future__ import annotations

from app.codebase.real_adapter import RealCodebaseAdapter
from app.embedding.term_dict import _read

_BOM = b"\xef\xbb\xbf"


# ---------------------------------------------------------------------------
# 1) UTF-8 + BOM (xfdl 시나리오): BOM 잔존 없이 한글 무손실
# ---------------------------------------------------------------------------


def test_read_file_strips_utf8_bom(tmp_path):
    body = "this.fn = function() {\n\tvar 한도 = 3000000; // 직무발명보상금 비과세\n};\n"
    (tmp_path / "PayRefCom003.xfdl").write_bytes(_BOM + body.encode("utf-8"))
    text = RealCodebaseAdapter(str(tmp_path)).read_file("PayRefCom003.xfdl")
    assert "﻿" not in text
    assert not text.startswith("﻿")
    assert "직무발명보상금 비과세" in text
    assert text == body


def test_term_dict_read_strips_utf8_bom(tmp_path):
    path = tmp_path / "PayRefCom003.xfdl"
    path.write_bytes(_BOM + "// 자녀세액공제\n".encode("utf-8"))
    text = _read(path)
    assert not text.startswith("﻿")
    assert "﻿" not in text
    assert "자녀세액공제" in text


# ---------------------------------------------------------------------------
# 2) CP949 (sqlmap 시나리오): 한글 주석 무손실
# ---------------------------------------------------------------------------


def test_read_file_decodes_cp949(tmp_path):
    body = "SELECT NVL(rd.n0200,0) AS n0200 -- 자녀세액공제 공제대상자녀\n"
    (tmp_path / "PayRefCom_2026.xml").write_bytes(body.encode("cp949"))
    text = RealCodebaseAdapter(str(tmp_path)).read_file("PayRefCom_2026.xml")
    assert "자녀세액공제 공제대상자녀" in text


def test_term_dict_read_decodes_cp949(tmp_path):
    path = tmp_path / "PayRefCom_2026.xml"
    path.write_bytes("-- 자녀세액공제 공제대상자녀\n".encode("cp949"))
    assert "자녀세액공제 공제대상자녀" in _read(path)


# ---------------------------------------------------------------------------
# 3) 선언·실제 불일치 (TimTimm.xml 시나리오): 선언 EUC-KR·실제 UTF-8 → 선언 무시
# ---------------------------------------------------------------------------


def test_read_file_ignores_false_xml_declaration(tmp_path):
    # 선언은 EUC-KR 이지만 실제 바이트는 UTF-8. 선언을 따르면 깨진다.
    body = '<?xml version="1.0" encoding="EUC-KR"?>\n<sqlMap>연차 계산</sqlMap>\n'
    (tmp_path / "TimTimm.xml").write_bytes(body.encode("utf-8"))
    text = RealCodebaseAdapter(str(tmp_path)).read_file("TimTimm.xml")
    assert 'encoding="EUC-KR"' in text  # 선언 문자열은 그대로 보존
    assert "연차 계산" in text  # 한글 본문 무손실


def test_term_dict_read_ignores_false_xml_declaration(tmp_path):
    body = '<?xml version="1.0" encoding="EUC-KR"?>\n<sqlMap>연차 계산</sqlMap>\n'
    path = tmp_path / "TimTimm.xml"
    path.write_bytes(body.encode("utf-8"))
    assert "연차 계산" in _read(path)


# ---------------------------------------------------------------------------
# 4) apply_patch BOM 왕복: 적용 후에도 BOM 유지 + 본문 무손실
# ---------------------------------------------------------------------------


def test_apply_patch_preserves_bom(tmp_path):
    # BOM 있는 원본 (LF)
    original = "line1\nvar 한도 = 3000000;\nline3\n"
    target = tmp_path / "PayRefCom003.xfdl"
    target.write_bytes(_BOM + original.encode("utf-8"))

    # 2번째 줄의 한도 상수를 500만원으로 개정하는 unified diff
    diff = (
        "--- a/PayRefCom003.xfdl\n"
        "+++ b/PayRefCom003.xfdl\n"
        "@@ -2,1 +2,1 @@\n"
        "-var 한도 = 3000000;\n"
        "+var 한도 = 5000000;\n"
    )

    RealCodebaseAdapter(str(tmp_path)).apply_patch(1, diff)

    raw = target.read_bytes()
    assert raw.startswith(_BOM)  # BOM 여전히 선두에 유지
    assert raw.count(_BOM) == 1  # BOM 중복 부착 없음
    text = raw[len(_BOM):].decode("utf-8")
    assert text == "line1\nvar 한도 = 5000000;\nline3\n"


def test_apply_patch_without_bom_unchanged(tmp_path):
    """BOM 없는 파일은 기존 동작 그대로 — BOM 이 새로 붙지 않는다."""
    original = "line1\nvar limit = 3000000;\nline3\n"
    target = tmp_path / "plain.xml"
    target.write_bytes(original.encode("utf-8"))

    diff = (
        "--- a/plain.xml\n"
        "+++ b/plain.xml\n"
        "@@ -2,1 +2,1 @@\n"
        "-var limit = 3000000;\n"
        "+var limit = 5000000;\n"
    )

    RealCodebaseAdapter(str(tmp_path)).apply_patch(1, diff)

    raw = target.read_bytes()
    assert not raw.startswith(_BOM)
    assert raw.decode("utf-8") == "line1\nvar limit = 5000000;\nline3\n"
