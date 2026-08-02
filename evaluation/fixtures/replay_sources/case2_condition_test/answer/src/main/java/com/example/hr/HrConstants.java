package com.example.hr;

/**
 * 노동법 상수 — 법령·고시 개정 시 이 파일의 값을 수정한다.
 * replay fixture 전용 합성 코드 (regtax-monitor evaluation)
 */
public class HrConstants {

    // 근로기준법 제60조 제1항: 연차 부여 최소 출근율
    public static final double MIN_ATTENDANCE_RATE = 0.8;

    // 근로기준법 제60조 제1항: 연차 기본 일수
    public static final int BASE_ANNUAL_LEAVE_DAYS = 15;

    // 근로기준법 제60조 제2항: 1년 미만 근로자 월별 연차 (개월당)
    public static final int LEAVE_DAYS_PER_MONTH = 1;

    private HrConstants() {
    }
}
