# 프로파일: Java + Spring (Boot / Legacy MVC)

## 기술 스택 (CLAUDE.md에 복사 후 수정)

- Java {버전: 8 / 11 / 17}
- Spring {Boot 3.x / Framework 5.x 레거시}
- {빌드: Gradle / Maven}
- {DB: Oracle / SQL Server} + {MyBatis / iBatis / JPA}
- {프론트: Nexacro / Thymeleaf / 별도 SPA — 해당 없으면 삭제}

## verify.sh 블록 (scripts/verify.sh에 복사)

**quick** (Stop hook — 수 초 내. Gradle 데몬이 떠 있으면 증분 컴파일이라 빠름):

```bash
./gradlew -q compileJava    # Maven: mvn -q -B compile -o
```

**full** (step AC / 리뷰 — quick 이후 실행됨):

```bash
./gradlew -q compileTestJava test    # Maven: mvn -q -B verify
```

테스트가 사실상 없는 레거시 프로젝트면 full을 quick과 동일하게 두거나 `compileTestJava`까지만 둔다.


## 개별 명령어 (CLAUDE.md "명령어" 섹션에 추가)

```bash
./gradlew bootRun    # 로컬 실행 (Boot)
```

## CRITICAL 규칙 후보

- SQL은 매퍼 XML(iBatis/MyBatis)에서만 관리. Java 코드에 SQL 문자열 금지
- 스토어드 프로시저 시그니처를 임의로 변경하지 말 것 — 다른 시스템이 공유 호출할 수 있음
- 트랜잭션 경계는 서비스 레이어에서만 선언
- 다계열사(multi-company) 분기는 하드코딩 대신 company_id 기반 설정/표준코드로 처리

## 흔한 함정

- iBatis: SQL 주석 안의 `?` 문자가 placeholder로 오인식됨 → 주석에 `?` 금지
- 레거시 인코딩(EUC-KR/MS949) 파일 존재 여부를 프로젝트 초기에 확인
- 로컬과 서버의 Java 버전 차이로 컴파일은 되는데 배포에서 깨지는 케이스

## security 블록 (verify.sh security에 추가)

```bash
./gradlew -q dependencyCheckAnalyze   # OWASP Dependency-Check 플러그인 필요
```

플러그인 도입 전이면 시크릿 스캔(기본 내장)만으로 시작하고, /secscan의 수동 점검(iBatis `$...$` 치환, IDOR 등)에 비중을 둔다.

## CI 블록 (.github/workflows/ci.yml 셋업 step)

```yaml
- uses: actions/setup-java@v4
  with: { distribution: temurin, java-version: '17', cache: gradle }
```
