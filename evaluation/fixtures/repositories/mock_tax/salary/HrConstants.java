package com.example.salary;

/**
 * 노동법 관련 상수 — 법령·고시 개정 시 이 파일의 값을 수정한다.
 * 평가 fixture 전용 (regtax-monitor evaluation)
 */
public class HrConstants {

    // 최저임금법: 시간급 최저임금 (2025년 적용)
    public static final long MINIMUM_HOURLY_WAGE = 10030L;

    // 근로기준법 제60조: 연차유급휴가 기준 연도 (YYYY)
    public static final int ANNUAL_LEAVE_BASE_YEAR = 2025;

    // 근로기준법 제60조: 1년 미만 근로자 월별 연차 (개월당)
    public static final int ANNUAL_LEAVE_PER_MONTH = 1;
}
