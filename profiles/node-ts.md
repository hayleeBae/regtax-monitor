# 프로파일: Node.js + TypeScript (React / Next.js / Express 등)

## 기술 스택 (CLAUDE.md에 복사 후 수정)

- {프레임워크: React 18 / Next.js 15 / Express 등}
- TypeScript (strict mode)
- {스타일링: Tailwind CSS 등 — 해당 없으면 삭제}
- {상태관리 / 데이터 페칭: 해당 없으면 삭제}

## verify.sh 블록 (scripts/verify.sh에 복사)

**quick** (Stop hook — 수 초 내):

```bash
npx tsc --noEmit
```

**full** (step AC / 리뷰 — quick 이후 실행됨):

```bash
npm run lint
npm test -- --watchAll=false
```

테스트가 아직 없는 프로젝트나 tsc-only 검증 정책(마이그레이션 초기 등)이면 full에서 테스트 줄을 빼고 lint만 남긴다.


## 개별 명령어 (CLAUDE.md "명령어" 섹션에 추가)

```bash
npm run dev      # 개발 서버
npm run build    # 프로덕션 빌드
```

## CRITICAL 규칙 후보

- 클라이언트 컴포넌트에서 직접 외부 API를 호출하지 말 것 (API 라우트/서비스 레이어 경유)
- `any` 타입 사용 금지. 불가피하면 `unknown` + 타입 가드
- 환경변수는 `.env`로만 관리, 코드에 하드코딩 금지

## 흔한 함정

- ESLint/Prettier 충돌 → eslint-config-prettier로 정리
- tsc는 통과하는데 next build만 실패하는 케이스 → verify.sh에 build 포함 여부를 프로젝트 초기에 결정

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
