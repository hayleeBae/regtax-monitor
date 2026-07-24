# Security Dependency Compatibility Review

- 검토일: 2026-07-24
- 환경: macOS ARM64, Python 3.10.20

## 결론

| Package | 기존 | 검토 버전 | 결과 |
|---|---:|---:|---|
| setuptools | 81.0.0 | 83.0.0 | torch 2.12.1과 단독 조합은 충돌 |
| torch | 2.12.1 | 2.13.0 | macOS ARM64/Python 3.10 wheel 확인 |
| 결합 | 위 기존 조합 | setuptools 83 + torch 2.13 | 의존성 해석·설치 성공 |

`setuptools`만 83으로 올리면 torch 2.12.1의 `setuptools<82` 제약을 위반한다.
따라서 두 패키지를 함께 올렸다. 이후 `verify.sh full`, bge-m3 임베딩 검색,
`verify.sh security`를 모두 통과해야 requirements 기준으로 확정한다.

## 최종 검증 결과

- 설치: torch 2.13.0, setuptools 83.0.0
- `bash scripts/verify.sh full`: 189 passed
- bge-m3 로딩·실제 Chroma 검색: 통과
- 5개 retrieval 조합 benchmark: 통과, provider failure 0
- `bash scripts/verify.sh security`: `No known vulnerabilities found, 1 ignored`

ignore 1건은 기존 `PYSEC-2026-311(chromadb)`이며 수정판 미출시 사유가
`scripts/verify.sh`에 기록되어 있다. 이번 두 취약점은 ignore하지 않고 수정 버전으로
해결했다.

## 취약점 판단

- `PYSEC-2026-3447`: macOS 파일명 Unicode 정규화 차이로 sdist 제외 규칙을
  우회할 수 있으며 setuptools 83.0.0에서 수정됐다.
- `PYSEC-2025-194`: 공식 OSV는 마지막 영향 버전을 2.6.0으로 표시하지만
  감사 도구가 2.12.1을 경고했다. 오탐 예외 대신 2.13.0 조합을 실제 검증했다.

## 운영 주의

- 회사망에서 wheel 설치가 막히면 사내 승인된 wheel/mirror가 필요하다.
- torch 업그레이드 후 bge-m3 모델 로딩과 CPU 임베딩을 반드시 확인한다.
- ROCm 관련 2.13 회귀는 macOS ARM64 wheel에는 해당하지 않는다.
