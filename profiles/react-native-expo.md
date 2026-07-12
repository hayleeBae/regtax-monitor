# 프로파일: React Native + Expo

## 기술 스택 (CLAUDE.md에 복사 후 수정)

- React Native + Expo {SDK 버전} ({managed / bare workflow + dev client})
- TypeScript (strict mode)
- {내비게이션: expo-router / react-navigation}
- {백엔드 연동: 기존 Spring Boot API 등}

## verify.sh 블록 (scripts/verify.sh에 복사)

**quick** (Stop hook — 수 초 내):

```bash
npx tsc --noEmit
```

**full** (step AC / 리뷰 — quick 이후 실행됨):

```bash
npm test -- --watchAll=false
npx expo-doctor || true    # 경고성 — 실패로 처리하지 않음
```

테스트 없는 초기 단계면 full을 quick과 동일하게 둔다.


## 개별 명령어 (CLAUDE.md "명령어" 섹션에 추가)

```bash
npx expo start            # 개발 서버
npx expo run:android      # 에뮬레이터 실행 (bare/dev client)
eas build --profile development --platform android
```

## CRITICAL 규칙 후보

- 네이티브 모듈(생체인증, GPS 등) 추가는 반드시 사용자 승인 후 진행 — dev client 재빌드가 필요함
- 디자인 토큰(색상/간격)은 단일 theme 파일에서만 정의. 컴포넌트에 hex 하드코딩 금지
- API base URL은 환경별 설정(app.config)으로 분리

## 흔한 함정

- 사내망 SSL 인증서 → Metro/EAS 통신 실패. NODE_TLS_REJECT_UNAUTHORIZED=0은 로컬 임시책으로만, 커밋 금지
- Expo SDK 업그레이드 시 네이티브 의존성 호환성 먼저 확인 (expo-doctor)
- 에뮬레이터와 실기기의 localhost 주소 차이 (10.0.2.2 vs 실제 IP)

## security 블록 (verify.sh security에 추가)

```bash
npm audit --audit-level=high
```

## CI 블록 (.github/workflows/ci.yml 셋업 step)

```yaml
- uses: actions/setup-node@v4
  with: { node-version: 20, cache: npm }
- run: npm ci
```
