package com.example.tax;

/**
 * 세법 관련 상수 — 법령 개정 시 이 파일의 값을 수정한다.
 * 평가 fixture 전용 (regtax-monitor evaluation)
 */
public class TaxConstants {

    // 소득세법 제59조의2: 자녀세액공제 (자녀 1인당)
    public static final long CHILD_TAX_CREDIT = 150000L;

    // 소득세법 제50조: 기본공제 (부양가족 1인당)
    public static final long DEPENDENT_DEDUCTION = 1500000L;

    // 소득세법 제47조의2: 근로소득세액공제 한도
    public static final long EARNED_INCOME_CREDIT_LIMIT = 500000L;

    // 소득세법 제59조의3: 연금보험료 세액공제 한도
    public static final long PENSION_CREDIT_LIMIT = 9000000L;

    // 소득세법 제87조: 주택청약종합저축 소득공제 한도
    public static final long HOUSING_SUBSCRIPTION_DEDUCTION_LIMIT = 2400000L;

    // 소득세법 제104조 제1항: 단기 양도소득세율 (1년 미만 보유)
    public static final double CAPITAL_GAINS_RATE_SHORT = 0.40;

    // 소득세법 제104조 제1항: 중기 양도소득세율 (2년 미만 보유)
    public static final double CAPITAL_GAINS_RATE_MID = 0.20;

    // 소득세법 제140조: 연말정산 제출기한 (MM-dd)
    public static final String YEAR_END_SUBMISSION_DEADLINE = "02-28";
}
