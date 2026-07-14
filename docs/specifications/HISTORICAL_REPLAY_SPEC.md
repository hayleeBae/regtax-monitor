# Historical Replay Specification

- 문서 상태: Implementation Ready
- 관련 Issue: `#0017`, `#0018`
- 버전: `historical-replay-v1`

## 1. 목적

과거 법령 개정 당시 코드 상태에서 파이프라인을 실행하고 실제 수정 commit과 비교한다.

## 2. 원칙

- 원본 repo 무변경
- base와 answer commit 분리
- answer commit 전체를 무조건 정답으로 보지 않음
- 사람이 relevant scope 지정
- private fixture 지원
- 줄 동일성보다 파일/값/조건/테스트 우선

## 3. Fixture

```yaml
schema_version: "1"
case_id: "historical_tax_2024_child_credit"
law:
  law_name: "소득세법"
  article: "제59조의2"
  before_text: "..."
  after_text: "..."
repository:
  source_type: "local_git"
  path_env: "EHR_REPO_ROOT"
  base_commit: "abc123"
  answer_commit: "def456"
scope:
  relevant_paths: ["module-tax/src/main/java/.../TaxService.java"]
  excluded_paths: ["README.md"]
  expected_replacements:
    - {path: "module-tax/src/main/java/.../TaxService.java", before: "150000", after: "250000"}
execution:
  golden_command: "mvn -q test -Dtest=YearEndGoldenTest"
  timeout_seconds: 1800
  privacy_mode: "metadata_only"
metadata:
  reviewed: true
```

절대경로는 YAML이 아니라 환경변수로 주입한다.

## 4. Worktree 절차

1. repo/commit 검증
2. 임시 root
3. detached worktree at base
4. dirty 없음 확인
5. base index 또는 cache
6. pipeline
7. generated patch check/apply in scratch
8. golden test
9. answer diff 추출
10. 비교
11. worktree remove/cleanup

source working tree에서 checkout/reset/clean/commit/push를 금지한다.

## 5. Git allowlist

rev-parse, cat-file, diff, show, worktree add/remove, status, apply --check, apply만 wrapper로 허용한다. 모든 command에 timeout을 둔다.

## 6. Index cache

key: repository id, base commit, embedding model, chunker/indexer version. 운영 index를 덮어쓰지 않는다.

## 7. 지표

- relevant path Recall@K
- primary rank/symbol hit
- expected replacement accuracy
- file coverage
- unnecessary file rate
- git apply
- golden result
- changed file Jaccard
- normalized diff similarity(참고)

동일 결과를 다른 구현으로 만들 수 있으므로 diff similarity는 필수 합격 기준이 아니다.

## 8. Privacy

- full: diff/snippet 저장
- redacted: 일부 마스킹
- metadata_only: hash/count/metric만 저장

실제 회사 사례는 metadata_only를 기본으로 한다.

## 9. 실패

commit 없음, worktree 실패, index 실패, LLM unavailable, golden timeout, cleanup 실패를 구분한다. cleanup은 finally에서 수행한다.

## 10. 최소 fixture

공개 mock git repo 3개:

1. 단일 value change
2. condition + test update
3. answer commit에 unrelated 문서 변경 포함

## 11. 테스트

commit validation, worktree lifecycle, original repo unchanged, unrelated exclusion, replacement, Jaccard, golden timeout, privacy, exception cleanup, cache key.

## 12. 수용 기준

- 임시 worktree 실행
- 원본 무변경 검증
- answer와 file/replacement 비교
- privacy mode
- mock 3건
- report
- cleanup

## 13. Claude Code 요청문

```text
Issue #0017과 #0018을 구현하라.

base_commit, answer_commit, relevant scope를 분리한다.
answer commit 전체를 자동 정답으로 사용하지 않는다.
모든 실행은 임시 detached worktree에서 수행한다.
source repo의 checkout/reset/clean/commit/push를 금지한다.

git allowlist와 timeout을 사용하고 예외에도 cleanup한다.
metadata_only에서는 코드와 diff artifact를 저장하지 않는다.
공개 mock fixture 3개를 추가한다.
```
