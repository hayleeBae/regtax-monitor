package com.example.hr;

/**
 * 최저임금 미달 여부 판정 (최저임금법 제6조).
 * 고시 개정 시 MINIMUM_HOURLY_WAGE 를 수정한다.
 * replay fixture 전용 합성 코드 (regtax-monitor evaluation)
 */
public class MinimumWageValidator {

    // 최저임금 고시: 시간급 최저임금 (원)
    public static final long MINIMUM_HOURLY_WAGE = 10320L;

    // 최저임금법 시행령 제5조: 월 환산 기준 시간 (주 40시간 + 주휴)
    public static final int MONTHLY_STANDARD_HOURS = 209;

    /**
     * 시간급이 최저임금 이상인지 판정한다.
     */
    public boolean isCompliant(long hourlyWage) {
        return hourlyWage >= MINIMUM_HOURLY_WAGE;
    }

    /**
     * 월 환산 최저임금액 (최저임금법 시행령 제5조).
     */
    public long monthlyMinimumWage() {
        return MINIMUM_HOURLY_WAGE * MONTHLY_STANDARD_HOURS;
    }
}
