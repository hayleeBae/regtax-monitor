# replay_sources — replay mock repo 원본 파일 트리 (Issue #0017)

HISTORICAL_REPLAY_SPEC §10 "최소 fixture 3건"의 mock git repo를 만들기 위한
**평범한 파일 트리**다. `.git` 이 없으므로 그대로 커밋된다(ADR-010).

```
<case>/
├── base/     # base commit 시점의 전체 파일 트리 스냅샷
└── answer/   # answer commit 시점의 전체 파일 트리 스냅샷
```

두 디렉토리는 diff가 아니라 **각 시점의 완전한 스냅샷**이다. 빌드 스크립트가
base 트리로 커밋 1개(태그 `<case>/base`), answer 트리로 커밋 1개(태그
`<case>/answer`)를 만든다.

```bash
python3 scripts/build_replay_repos.py     # → evaluation/fixtures/replay_repos/ (gitignore)
```

## 케이스

| case | 목적 | answer diff |
|---|---|---|
| `case1_value_change` | 단일 value change | 상수 파일 1개 |
| `case2_condition_test` | 조건 변경 + 테스트 동시 수정 | 서비스 1개 + 테스트 1개 |
| `case3_unrelated_noise` | answer commit에 무관한 문서 변경 혼입 | 코드 1개 + `README.md` |

`case3` 는 "answer commit 전체를 정답으로 보지 않는다"(SPEC §2·§11)를 검증하기
위한 케이스다 — `README.md` 는 fixture 의 `excluded_paths` 로 빠진다.

모든 코드는 **합성 데이터**다. 실제 eHR 코드를 옮겨 담지 않는다.
