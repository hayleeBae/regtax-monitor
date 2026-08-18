"""RealCodebaseAdapter.list_files() 의 인덱싱 대상 선별 규칙.

2026-08-05 회사 실측에서 인덱싱 대상 8,100개 중 상당수가
`out/artifacts/eHR_war_exploded/...` 하위였다 — exploded WAR(빌드 산출물)이다.
빌드 산출물을 인덱싱하면 매핑이 재생성되는 경로를 가리켜 patch 가 무의미해지고,
같은 코드가 중복 인덱싱돼 검색 상위를 산출물이 차지한다.

기존 동작(확장자 필터·`.git` 제외·`REPO_INDEX_PATHS` 서브디렉토리 필터)은 그대로
유지되어야 하므로 함께 고정한다.
"""

from __future__ import annotations

import pytest

from app.codebase.real_adapter import RealCodebaseAdapter


def _touch(root, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// synthetic\n", encoding="utf-8")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """확장자·디렉토리 조합을 섞은 합성 repo."""
    monkeypatch.setattr("app.codebase.real_adapter.settings.repo_index_paths", "")
    _touch(tmp_path, "src/main/java/com/example/TaxService.java")
    _touch(tmp_path, "src/main/resources/mapper/TaxMapper.xml")
    _touch(tmp_path, "src/main/sql/tax.sql")
    _touch(tmp_path, "README.md")  # 확장자 미대상
    return tmp_path


# ---------------------------------------------------------------------------
# 기존 동작 고정
# ---------------------------------------------------------------------------


def test_lists_source_extensions_only(repo):
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert "src/main/java/com/example/TaxService.java" in files
    assert "src/main/resources/mapper/TaxMapper.xml" in files
    assert "src/main/sql/tax.sql" in files
    assert "README.md" not in files


def test_paths_are_relative_to_repo_root(repo):
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert all(not f.startswith("/") for f in files)


def test_repo_index_paths_limits_roots(repo, monkeypatch):
    _touch(repo, "other/module/Ignored.java")
    monkeypatch.setattr(
        "app.codebase.real_adapter.settings.repo_index_paths", "src"
    )
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert "src/main/java/com/example/TaxService.java" in files
    assert "other/module/Ignored.java" not in files


# ---------------------------------------------------------------------------
# 빌드 산출물 제외 (이번 수정)
# ---------------------------------------------------------------------------


def test_excludes_exploded_war_under_out(repo):
    """회사에서 실제로 관측된 경로 형태 — exploded WAR."""
    _touch(
        repo,
        "out/artifacts/eHR_war_exploded/eHR/solution/pay/pay/com/PayPayCom026.xfdl.js",
    )
    _touch(repo, "out/artifacts/eHR_war_exploded/WEB-INF/classes/TaxService.java")
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert not [f for f in files if f.startswith("out/")]


@pytest.mark.parametrize(
    "relative",
    [
        "target/classes/com/example/TaxService.java",
        "build/generated/Gen.java",
        "out/production/App.java",
        "dist/bundle.js",
        "node_modules/pkg/index.js",
        ".venv/lib/site.py",
        "venv/lib/site.py",
        "__pycache__/mod.py",
        ".svn/tmp/entry.xml",
        "classes/com/example/TaxService.java",
    ],
)
def test_excludes_build_output_directories(repo, relative):
    _touch(repo, relative)
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert relative not in files


def test_excludes_nested_build_directory(repo):
    """제외 디렉토리가 중간 경로에 있어도 걸러진다."""
    _touch(repo, "modules/payroll/target/classes/Generated.java")
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert "modules/payroll/target/classes/Generated.java" not in files


def test_keeps_source_when_name_is_only_a_substring(repo):
    """`build`/`out` 이 **경로 구성요소**일 때만 제외한다.

    부분일치로 판정하면 `outbound`·`buildings` 같은 정상 디렉토리가 통째로 사라진다.
    """
    _touch(repo, "src/outbound/OutboundService.java")
    _touch(repo, "src/buildings/BuildingService.java")
    _touch(repo, "src/main/java/com/example/BuildHelper.java")
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert "src/outbound/OutboundService.java" in files
    assert "src/buildings/BuildingService.java" in files
    assert "src/main/java/com/example/BuildHelper.java" in files


def test_git_directory_still_excluded(repo):
    """기존에도 제외되던 `.git` 이 계속 제외되는지(회귀 고정)."""
    _touch(repo, ".git/hooks/sample.py")
    files = RealCodebaseAdapter(str(repo)).list_files()
    assert not [f for f in files if f.startswith(".git/")]


def _list(root) -> list[str]:
    """OS 경로구분자를 `/` 로 정규화한 목록 — Windows/macOS 양쪽에서 의미 동일."""
    return [f.replace("\\", "/") for f in RealCodebaseAdapter(str(root)).list_files()]


def test_excludes_classes_exploded_war(repo):
    """eHR `classes/` (1.7GB·4중 중첩 exploded WAR, 2026-08-14 실측) 제외."""
    _touch(repo, "classes/com/example/PayrollService.java")
    files = _list(repo)
    assert not [f for f in files if f.startswith("classes/")]
    # 정상 소스는 그대로 남는지 (과다 제외 방지)
    assert "src/main/java/com/example/TaxService.java" in files


def test_excludes_deeply_nested_exploded_classes(repo):
    """자기 자신을 재귀 중첩하는 exploded WAR — 중간 `classes/` 로도 걸러진다."""
    _touch(
        repo,
        "classes/artifacts/eHR_war_exploded/WEB-INF/classes/src/A.java",
    )
    files = _list(repo)
    assert not [f for f in files if "eHR_war_exploded" in f]


def test_root_path_containing_excluded_word_is_not_self_excluded(tmp_path, monkeypatch):
    """repo 루트 경로 자체에 제외어가 있어도 저장소가 통째로 제외되지 않는다.

    `_is_excluded` 가 절대경로 parts 를 검사하면 `.../build/repo` 같은 루트에서
    `build` 가 걸려 모든 파일이 사라진다 — root 상대 경로로 판정해야 한다.
    """
    monkeypatch.setattr("app.codebase.real_adapter.settings.repo_index_paths", "")
    root = tmp_path / "build" / "repo"  # 경로에 제외어("build") 포함
    _touch(root, "src/main/java/com/example/TaxService.java")
    files = _list(root)
    assert "src/main/java/com/example/TaxService.java" in files


def test_excluded_dirs_matches_golden_ignore_vocabulary():
    """`app/golden.py::_IGNORE` 와 같은 어휘를 쓰는지 — 한쪽만 바뀌는 것을 막는다.

    두 목록은 형태가 다르다(집합 vs `shutil.ignore_patterns` 콜러블). `_IGNORE`
    는 이름 목록으로 호출하면 무시할 이름 집합을 돌려주므로, `EXCLUDED_DIRS` 의
    모든 이름이 `_IGNORE` 에도 잡히는지 실제로 호출해 확인한다.
    """
    from app.golden import _IGNORE

    names = sorted(RealCodebaseAdapter.EXCLUDED_DIRS)
    ignored = _IGNORE(".", names)
    missing = set(names) - set(ignored)
    assert not missing, f"golden._IGNORE 에서 누락된 제외 디렉토리: {sorted(missing)}"
    assert "classes" in RealCodebaseAdapter.EXCLUDED_DIRS
