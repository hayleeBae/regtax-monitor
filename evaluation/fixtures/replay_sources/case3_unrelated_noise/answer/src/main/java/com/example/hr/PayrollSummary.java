package com.example.hr;

/**
 * 월 급여 집계 — 최저임금 판정은 {@link MinimumWageValidator} 에 위임한다.
 * replay fixture 전용 합성 코드 (regtax-monitor evaluation)
 */
public class PayrollSummary {

    private final MinimumWageValidator validator = new MinimumWageValidator();

    /**
     * 통상임금(시간급) 기준 월 급여액.
     */
    public long monthlyPay(long hourlyWage, int workedHours) {
        if (workedHours <= 0) {
            return 0L;
        }
        return hourlyWage * workedHours;
    }

    /**
     * 최저임금 미달 시 보전해야 하는 월 차액.
     */
    public long shortfall(long hourlyWage) {
        if (validator.isCompliant(hourlyWage)) {
            return 0L;
        }
        return validator.monthlyMinimumWage() - hourlyWage * MinimumWageValidator.MONTHLY_STANDARD_HOURS;
    }
}
