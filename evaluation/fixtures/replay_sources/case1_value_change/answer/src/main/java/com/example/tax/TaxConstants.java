package com.example.tax;

/**
 * 세법 상수 — 법령 개정 시 이 파일의 값을 수정한다.
 * replay fixture 전용 합성 코드 (regtax-monitor evaluation)
 */
public class TaxConstants {

    // 소득세법 제59조의2: 자녀세액공제 (자녀 1인당)
    public static final long CHILD_TAX_CREDIT = 250000L;

    // 소득세법 제50조: 기본공제 (부양가족 1인당)
    public static final long DEPENDENT_DEDUCTION = 1500000L;

    // 소득세법 제59조의3: 연금계좌 세액공제 한도
    public static final long PENSION_CREDIT_LIMIT = 9000000L;

    private TaxConstants() {
    }
}
