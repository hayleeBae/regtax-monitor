"""수확기 스캔 루트 통일 + `.xfdl` 라벨·상수 수확 — 스펙 §2-4, §3 / ADR-015.

`term_dict._iter_source_files()`는 두 수확기(`term_dict`/`const_inventory`)의
공용 순회자다. 이 순회자를 인덱서(`RealCodebaseAdapter.list_files`)와 같은
`REPO_INDEX_PATHS` 어휘로 통일하고, xfdl(웹 하위)의 한도 상수·한글 주석이
수확 범위에 들어오게 한다 — "15만원→25만원" 개정의 상수 매칭이 xfdl 한도값에
닿는 것이 실질 효용이다.

tmp_path 자기완결 — 실제 eHR 저장소는 건드리지 않는다.
"""
from __future__ import annotations

from app.embedding import const_inventory, term_dict

# n0200(자녀세액공제) 참조 + // 라벨 + 한도 상수 3000000 이 Script 안에 있다.
# 여는 중괄호는 함수 선언 다음 줄(Nexacro 관례). 레이아웃부는 라벨 없음.
XFDL_WITH_LABEL_AND_CONST = """<?xml version="1.0" encoding="utf-8"?>
<FDL version="1.5">
  <Form id="PayRefCom003">
    <Layout>
      <Dataset id="ds_pay"><ColumnInfo><Column id="n0200"/></ColumnInfo></Dataset>
    </Layout>
    <Script type="xscript5.1"><![CDATA[
this.fn_calc = function(row)
{
    var limit = 3000000 ;  // 직무발명보상금 비과세 한도
    this.ds_pay.setColumn(row, "n0200", limit);  // 자녀세액공제 공제대상자녀
    return limit;
};
]]></Script>
  </Form>
</FDL>
"""


def _make_file(root, rel: str, text: str, *, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


# ── 1. 스캔 루트 (REPO_INDEX_PATHS 어휘 공유) ──────────────────────────

def test_index_paths_limit_scan_roots(tmp_path, monkeypatch):
    """repo_index_paths 설정 시 그 목록만 순회하고 밖(src/b)은 안 본다."""
    _make_file(tmp_path, "src/a/A.java", "private Long n0200 = 0L; // 대상자녀\n")
    _make_file(tmp_path, "web/n/W.xfdl", XFDL_WITH_LABEL_AND_CONST, bom=True)
    _make_file(tmp_path, "src/b/B.java", "private Long a0121 = 0L; // 급여\n")

    monkeypatch.setattr(
        "app.embedding.term_dict.settings.repo_index_paths", "src/a,web/n"
    )
    rels = {rel for _p, rel in term_dict._iter_source_files(str(tmp_path))}
    assert rels == {"src/a/A.java", "web/n/W.xfdl"}
    assert "src/b/B.java" not in rels


def test_unset_index_paths_falls_back_to_src(tmp_path, monkeypatch):
    """미설정 시 <root>/src 있으면 src만 순회(기존 동작 — mock repo 회귀 방지)."""
    _make_file(tmp_path, "src/A.java", "private Long n0200 = 0L; // 대상자녀\n")
    _make_file(tmp_path, "other/B.java", "private Long a0121 = 0L; // 급여\n")

    monkeypatch.setattr("app.embedding.term_dict.settings.repo_index_paths", "")
    rels = {rel for _p, rel in term_dict._iter_source_files(str(tmp_path))}
    assert rels == {"src/A.java"}


def test_unset_index_paths_no_src_uses_root(tmp_path, monkeypatch):
    """src 디렉토리가 없으면 root 전체 순회(mock repo 형태)."""
    _make_file(tmp_path, "A.java", "private Long n0200 = 0L; // 대상자녀\n")

    monkeypatch.setattr("app.embedding.term_dict.settings.repo_index_paths", "")
    rels = {rel for _p, rel in term_dict._iter_source_files(str(tmp_path))}
    assert rels == {"A.java"}


# ── 4. 존재하지 않는 인덱스 경로는 조용히 건너뜀 ─────────────────────────

def test_missing_index_path_is_skipped(tmp_path, monkeypatch):
    """설정된 경로 중 실제 없는 것은 오류 없이 건너뛰고, 있는 것만 순회."""
    _make_file(tmp_path, "src/a/A.java", "private Long n0200 = 0L; // 대상자녀\n")

    monkeypatch.setattr(
        "app.embedding.term_dict.settings.repo_index_paths", "src/a,src/ghost,web/none"
    )
    rels = {rel for _p, rel in term_dict._iter_source_files(str(tmp_path))}
    assert rels == {"src/a/A.java"}  # 없는 경로가 예외를 던지지 않는다


# ── 2. xfdl 라벨 수확 (term_dict) ─────────────────────────────────────

def test_xfdl_label_harvested(tmp_path, monkeypatch):
    """xfdl Script의 // 주석에서 컬럼코드 라벨을 수확한다."""
    _make_file(tmp_path, "web/n/PayRefCom003.xfdl", XFDL_WITH_LABEL_AND_CONST, bom=True)
    monkeypatch.setattr("app.embedding.term_dict.settings.repo_index_paths", "web/n")

    table = term_dict.harvest(str(tmp_path))
    assert "n0200" in table
    assert any("자녀세액공제" in label for label in table["n0200"])


# ── 3. xfdl 상수 수확 (const_inventory, 무수정 자동 확장) ────────────────

def test_xfdl_constant_harvested(tmp_path, monkeypatch):
    """xfdl JS 숫자 리터럴(3000000)이 상수 인벤토리에 값+위치로 수확된다."""
    _make_file(tmp_path, "web/n/PayRefCom003.xfdl", XFDL_WITH_LABEL_AND_CONST, bom=True)
    monkeypatch.setattr("app.embedding.term_dict.settings.repo_index_paths", "web/n")

    inv = const_inventory.harvest(str(tmp_path))
    assert "3000000" in inv
    locs = inv["3000000"]
    assert any(loc[0] == "web/n/PayRefCom003.xfdl" for loc in locs)


# ── 5. 기존 .java/.xml 수확 회귀 없음 ─────────────────────────────────

def test_java_and_xml_harvest_regression(tmp_path, monkeypatch):
    """xfdl 확장 후에도 .java(private VO)·.xml(-- 주석) 수확이 유지된다."""
    _make_file(tmp_path, "src/vo/PayVO.java", "    private Long l0160 = 0L;   // 대중교통\n")
    _make_file(
        tmp_path,
        "src/sqlmap/Pay.xml",
        "  NVL(rd.n0201,0) AS n0201   -- 출산입양세액공제\n",
    )
    monkeypatch.setattr("app.embedding.term_dict.settings.repo_index_paths", "")

    table = term_dict.harvest(str(tmp_path))
    assert any("대중교통" in label for label in table.get("l0160", []))
    assert any("출산입양세액공제" in label for label in table.get("n0201", []))
