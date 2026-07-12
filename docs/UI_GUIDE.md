# UI 디자인 가이드

> 대상: `static/index.html` (단일 파일 대시보드 — 프레임워크 없음, 바닐라 JS + CSS 변수). 아래 값은 현재 구현에서 추출한 사실 기준이다. 새 UI 요소는 이 변수를 재사용하고 새 색상·스타일을 임의로 추가하지 않는다.

## 디자인 원칙
1. 도구처럼 보여야 한다 — 담당자가 매일 쓰는 업무 대시보드. 마케팅 페이지 아님.
2. 라이트 테마 고정, 정보 밀도 우선 (작은 폰트 13~17px, 촘촘한 테이블/카드).
3. 상태는 시맨틱 색상으로만 구분 (success/danger/warning) — 장식용 색상 금지.

## AI 슬롭 안티패턴 — 하지 마라
| 금지 사항 | 이유 |
|-----------|------|
| backdrop-filter: blur() | glass morphism은 AI 템플릿의 가장 흔한 징후 |
| gradient-text (배경 그라데이션 텍스트) | AI가 만든 SaaS 랜딩의 1번 특징 |
| "Powered by AI" 배지 | 기능이 아니라 장식. 사용자에게 가치 없음 |
| box-shadow 글로우 애니메이션 | 네온 글로우 = AI 슬롭 |
| 보라/인디고 브랜드 색상 | "AI = 보라색" 클리셰 |
| 모든 카드에 동일한 rounded-2xl | 균일한 둥근 모서리는 템플릿 느낌 |
| 배경 gradient orb (blur-3xl 원형) | 모든 AI 랜딩 페이지에 있는 장식 |

## 색상 (CSS 변수 — `:root`에 정의됨, 직접 hex 사용 금지)
### 배경
| 용도 | 값 |
|------|------|
| 페이지 | `--bg: #f5f7fa` |
| 카드 | `--card: #ffffff` |
| 헤더 | `--header-bg: #1a365d` (남색, sticky) |
| 테두리 | `--border: #e2e8f0` |

### 텍스트
| 용도 | 값 |
|------|------|
| 주 텍스트 | `--text: #1a202c` |
| 보조 | `--text-muted: #718096` |
| 헤더 위 텍스트 | white / rgba(255,255,255,0.55) |

### 데이터/시맨틱 색상
| 용도 | 값 |
|------|------|
| 성공(골든 passed, 승인) | `--success: #38a169` |
| 에러(failed, 거절) | `--danger: #e53e3e` |
| 경고(미검증 등) | `--warning: #d69e2e` |
| 주 액션 | `--primary: #3182ce` (hover `--primary-hover: #2b6cb0`) |

## 컴포넌트
### 카드
```
background: var(--card); border: 1px solid var(--border); border-radius 6~8px 수준
```

### 버튼 (.btn)
```
padding: 7px 16px; border-radius: 6px; font-size: 13px; font-weight: 500;
Primary: background var(--primary), hover var(--primary-hover)
transition은 background/opacity 0.15s만
```

## 레이아웃
- 헤더: sticky, 높이 60px, 남색(`--header-bg`), 제목 17px/700 + 부제 11px.
- 본문 패딩 24px 기준, 좌측 정렬. 중앙 정렬 히어로 금지.

## 타이포그래피
- 시스템 폰트 스택: `-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif` — 웹폰트 로드 금지 (사내망 전제).
- 본문/버튼 13px, 헤더 제목 17px, 부제·캡션 11px.

## 애니메이션
- 허용: `transition: background/opacity 0.15s` (버튼 hover 수준)
- 그 외 모든 애니메이션 금지.

## 아이콘
- 이모지/텍스트 기호 수준만 사용 중 (예: 📚). 아이콘 라이브러리 도입 금지 (단일 파일·오프라인 전제).
