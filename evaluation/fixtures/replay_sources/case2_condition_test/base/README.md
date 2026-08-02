# case2_condition_test

연차유급휴가 부여 요건을 담은 합성 mock repo.

- `src/main/java/com/example/hr/AnnualLeaveService.java` — 연차 부여 판정
- `src/main/java/com/example/hr/HrConstants.java` — 노동법 상수
- `src/test/java/com/example/hr/AnnualLeaveServiceTest.java` — 골든 테스트

## 시나리오

근로기준법 제60조 개정으로 연차유급휴가 부여 **요건**(계속근로기간)이 바뀐다.
정답 commit 은 서비스의 조건문과 대응하는 테스트를 **함께** 고친다.
