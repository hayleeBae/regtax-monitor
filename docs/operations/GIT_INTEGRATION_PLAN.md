# eHR 연동 계획 — Git 브랜치 자동 생성

> 작성일: 2026-06-23  
> 상태: **검토 중 (컨펌 대기)**

---

## 배경

regtax-monitor는 법령 변경을 감지하고 Claude가 eHR 코드 수정 초안(unified diff)을 생성한다.  
이 초안을 사내 HR 시스템에 반영하는 방법으로 **Git 브랜치 자동 생성** 방식을 채택하기로 논의됨.

### 검토했던 선택지

| 방식 | 결론 |
|---|---|
| 1. Git 브랜치 자동 생성 | ✅ **채택** — 별도 서버 불필요, 기존 Git 워크플로 활용 |
| 2. 이메일 알림 | 보류 (배포 시점에 추가 예정, STATUS.md 기록) |
| 3. 사내망 별도 서버 운영 | 미채택 — 상시 서버 필요, 인프라 부담 |

---

## 현재 구현 상태

`app/codebase/real_adapter.py` — `apply_patch()` 메서드

| 단계 | 구현 여부 |
|---|---|
| 브랜치 생성 (`git checkout -b`) | ❌ 없음 |
| 코드 수정 (`git apply`) | ✅ 완료 |
| 커밋 (`git commit`) | ❌ 없음 |
| 푸시 (`git push`) | ❌ 없음 |

현재는 워킹트리 파일만 수정되고, 브랜치·커밋·푸시가 없는 상태.  
승인 후 eHR 폴더에 변경사항이 uncommitted 상태로 남음.

---

## 구현 계획

`apply_patch()` 에 아래 순서로 git 명령어 추가:

```
1. git checkout -b regtax/proposal-{proposal_id}
2. git apply {patch_file}          ← 이미 구현됨
3. git add -A
4. git commit -m "feat(tax): 법령 변경 반영 — proposal #{proposal_id}\n\n{law_name} {article_no}"
5. git push origin regtax/proposal-{proposal_id}   ← 옵션 (아래 논의 필요)
```

### 컨펌이 필요한 항목

- [ ] **push 자동화 여부** — 로컬 브랜치만 만들고 push는 수동으로 할지, 자동으로 push까지 할지
- [ ] **브랜치 base** — main 기준으로 분기할지, 다른 브랜치(develop 등) 기준으로 할지
- [ ] **커밋 메시지 형식** — 현재 팀의 커밋 컨벤션에 맞춰 조정 필요
- [ ] **git apply 실패 시 처리** — 파일 충돌 발생 시 승인 자체를 막을지, 충돌 파일만 스킵할지

---

## 변경 대상 파일

- `app/codebase/real_adapter.py` — `apply_patch()` 수정
- `app/codebase/base.py` — 시그니처 변경 없음, 수정 불필요
