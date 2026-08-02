package com.example.hr;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/**
 * 연차유급휴가 부여 요건 골든 테스트 (근로기준법 제60조).
 * replay fixture 전용 합성 코드 (regtax-monitor evaluation)
 */
class AnnualLeaveServiceTest {

    private final AnnualLeaveService service = new AnnualLeaveService();

    @Test
    void 계속근로_12개월_출근율_80퍼센트면_부여대상이다() {
        assertTrue(service.isEligibleForAnnualLeave(12, 0.8));
    }

    @Test
    void 계속근로_11개월이면_부여대상이_아니다() {
        assertFalse(service.isEligibleForAnnualLeave(11, 1.0));
    }

    @Test
    void 출근율_미달이면_부여대상이_아니다() {
        assertFalse(service.isEligibleForAnnualLeave(24, 0.79));
    }

    @Test
    void 요건_미달이면_월별_연차로_대체한다() {
        assertEquals(11, service.grantedLeaveDays(11, 1.0));
    }

    @Test
    void 요건_충족이면_기본_15일을_부여한다() {
        assertEquals(15, service.grantedLeaveDays(12, 0.9));
    }
}
