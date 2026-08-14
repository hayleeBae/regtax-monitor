"""Nexacro `.xfdl` 청킹 — `indexer._chunk_xfdl` / `_extract_symbol`.

eHR UI는 JSP가 아니라 Nexacro이며(`web/nexacro/solution/**/*.xfdl` 1,301개),
**세법 한도값이 xfdl 내 <Script> JavaScript에 하드코딩**되어 있다
(직무발명보상금 비과세 한도 300→500→700만원 등, `PayRefCom003.xfdl:669-707`).
`.xfdl`이 `SOURCE_EXTS`에 없어 인덱싱 0건이던 재현율 공백을 청킹까지 뚫는다
(용어·상수 수확은 step 3). 스펙: `docs/specifications/EHR_INDEXING_SPEC.md` §2.
"""

from __future__ import annotations

from app.codebase.real_adapter import RealCodebaseAdapter
from app.embedding.indexer import _chunk_xfdl, _extract_symbol

# 실제 xfdl 형태를 따른 fixture — <Script> CDATA 안에 함수 2개, 여는 중괄호는
# 함수 선언 다음 줄에 온다(Nexacro 관례). 한도 상수는 첫 함수에 있다.
XFDL_TWO_FUNCS = """<?xml version="1.0" encoding="utf-8"?>
<FDL version="1.5">
  <Form id="PayRefCom003">
    <Layout>
      <Dataset id="ds_pay"><ColumnInfo><Column id="untax_amt"/></ColumnInfo></Dataset>
      <Grid id="grd_list" binddataset="ds_pay"/>
    </Layout>
    <Script type="xscript5.1"><![CDATA[
this.fn_calcUntaxLimit = function(base_year)
{
    var job_invention_untax_amt_limit = 3000000 ;  // 직무발명보상금 비과세 한도
    if (base_year >= "2024") {
        job_invention_untax_amt_limit = 7000000 ;
    }
    return job_invention_untax_amt_limit;
};

this.PayRefCom003_onload = function(obj,e)
{
    this.fn_calcUntaxLimit("2026");
};
]]></Script>
  </Form>
</FDL>
"""

XFDL_LAYOUT_ONLY = """<?xml version="1.0" encoding="utf-8"?>
<FDL version="1.5">
  <Form id="PayViewOnly">
    <Layout>
      <Dataset id="ds_view"><ColumnInfo><Column id="a0121"/></ColumnInfo></Dataset>
      <Grid id="grd" binddataset="ds_view"/>
    </Layout>
  </Form>
</FDL>
"""

XFDL_TWO_SCRIPTS = """<?xml version="1.0" encoding="utf-8"?>
<FDL version="1.5">
  <Form id="PayPayCom955">
    <Script type="xscript5.1"><![CDATA[
this.fn_truncTax = function(tax_amt)
{
    return Math.floor(tax_amt * 0.01) * 10;  // 지방소득세 절사
};
]]></Script>
    <Layout><Dataset id="ds"/></Layout>
    <Script type="xscript5.1"><![CDATA[
function fn_helper(x)
{
    return x + 1;
};
]]></Script>
  </Form>
</FDL>
"""


def test_splits_functions_into_separate_chunks():
    chunks = _chunk_xfdl(XFDL_TWO_FUNCS)
    assert len(chunks) == 2
    assert "fn_calcUntaxLimit" in chunks[0]
    assert "PayRefCom003_onload" in chunks[1]
    # 한도 상수는 첫 청크에 포함 (수치 개정 patch 대상)
    assert "3000000" in chunks[0]
    assert "7000000" in chunks[0]


def test_chunks_exclude_layout_xml():
    chunks = _chunk_xfdl(XFDL_TWO_FUNCS)
    joined = "\n".join(chunks)
    assert "<Layout>" not in joined
    assert "<Dataset" not in joined
    assert "<Grid" not in joined
    assert "binddataset" not in joined


def test_extract_symbol_returns_function_name():
    chunks = _chunk_xfdl(XFDL_TWO_FUNCS)
    assert _extract_symbol("x.xfdl", chunks[0]) == "fn_calcUntaxLimit"


def test_layout_only_falls_back_to_whole_file():
    chunks = _chunk_xfdl(XFDL_LAYOUT_ONLY)
    assert chunks == [XFDL_LAYOUT_ONLY]


def test_collects_functions_from_multiple_script_blocks():
    chunks = _chunk_xfdl(XFDL_TWO_SCRIPTS)
    joined = "\n".join(chunks)
    assert "fn_truncTax" in joined
    assert "fn_helper" in joined
    assert len(chunks) == 2
    # 레이아웃 XML부는 청킹되지 않는다
    assert "<Dataset" not in joined


def test_extract_symbol_bare_function_form():
    chunks = _chunk_xfdl(XFDL_TWO_SCRIPTS)
    helper = next(c for c in chunks if "fn_helper" in c)
    assert _extract_symbol("x.xfdl", helper) == "fn_helper"


def test_adapter_treats_xfdl_as_indexable(tmp_path):
    xfdl = tmp_path / "web" / "nexacro" / "solution" / "pay" / "PayRefCom003.xfdl"
    xfdl.parent.mkdir(parents=True, exist_ok=True)
    xfdl.write_text(XFDL_TWO_FUNCS, encoding="utf-8")
    adapter = RealCodebaseAdapter(str(tmp_path))
    assert adapter._is_indexable(xfdl) is True
