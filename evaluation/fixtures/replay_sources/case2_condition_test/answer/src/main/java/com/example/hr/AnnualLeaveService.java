package com.example.hr;

/**
 * 연차유급휴가 부여 판정 (근로기준법 제60조).
 * 요건 개정 시 조건문과 골든 테스트를 함께 수정한다.
 * replay fixture 전용 합성 코드 (regtax-monitor evaluation)
 */
public class AnnualLeaveService {

    /**
     * 연차유급휴가(15일) 부여 대상인지 판정한다.
     * 근로기준법 제60조 제1항: 계속근로기간 6개월 이상 + 출근율 80% 이상.
     */
    public boolean isEligibleForAnnualLeave(int monthsWorked, double attendanceRate) {
        if (monthsWorked < 6) {
            return false;
        }
        return attendanceRate >= HrConstants.MIN_ATTENDANCE_RATE;
    }

    /**
     * 부여 연차 일수를 계산한다.
     * 요건 미달이면 근로기준법 제60조 제2항의 월별 연차(개월당 1일)로 대체한다.
     */
    public int grantedLeaveDays(int monthsWorked, double attendanceRate) {
        if (isEligibleForAnnualLeave(monthsWorked, attendanceRate)) {
            return HrConstants.BASE_ANNUAL_LEAVE_DAYS;
        }
        return monthsWorked * HrConstants.LEAVE_DAYS_PER_MONTH;
    }
}
