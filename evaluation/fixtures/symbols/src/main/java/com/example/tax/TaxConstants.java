package com.example.tax;

/**
 * 합성 fixture 전용 상수 (regtax-monitor Issue #0019).
 * 실제 eHR 코드가 아니다 — com.example.* 로만 구성한다.
 */
public final class TaxConstants {

    /** 자녀세액공제 (자녀 1인당). */
    public static final long CHILD_CREDIT = 150000L;

    /** 근로소득세액공제 한도. */
    public static final long EARNED_INCOME_LIMIT = 500000L;

    private TaxConstants() {
    }
}
