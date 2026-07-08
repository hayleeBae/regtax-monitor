package com.example.salary;

/**
 * 최저임금법·근로기준법에 따른 급여/근로시간 검증.
 * 최저임금 고시(매년 8월경) 반영 시 MINIMUM_HOURLY_WAGE 수정 필요.
 */
public class MinimumWageValidator {

    // 최저임금 고시: 2025년 적용 시간급 (매년 갱신)
    private static final long MINIMUM_HOURLY_WAGE = 10030L;  // 최저임금 시간급

    // 근로기준법 제50조: 1주 법정근로시간 / 제53조: 연장 포함 한도
    private static final int MAX_WEEKLY_HOURS = 40;          // 주당 법정근로시간
    private static final int MAX_EXTENDED_WEEKLY_HOURS = 52; // 주당 연장근로 포함 한도

    // 근로기준법 제60조: 연차 유급휴가 기본 일수
    private static final int BASE_ANNUAL_LEAVE_DAYS = 15;    // 연차휴가 일수

    public boolean isWageCompliant(long hourlyWage) {
        return hourlyWage >= MINIMUM_HOURLY_WAGE;
    }

    public boolean isWorkHoursCompliant(int weeklyHours) {
        return weeklyHours <= MAX_EXTENDED_WEEKLY_HOURS;
    }

    public int baseAnnualLeaveDays() {
        return BASE_ANNUAL_LEAVE_DAYS;
    }
}
