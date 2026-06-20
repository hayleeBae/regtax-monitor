package com.example.tax;

/**
 * 소득세법 제55조에 따른 종합소득세 계산기.
 * 세율 변경 시 TAX_BRACKETS 배열과 getApplicableRate() 수정 필요.
 */
public class IncomeTaxCalculator {

    // 소득세법 제55조 제1항: 종합소득세율 구간 (2024년 기준)
    // [과세표준 상한, 세율]  — 상한 없는 최고 구간은 Double.MAX_VALUE
    private static final double[][] TAX_BRACKETS = {
        {14_000_000,    0.06},
        {50_000_000,    0.15},
        {88_000_000,    0.24},
        {150_000_000,   0.35},
        {300_000_000,   0.38},
        {500_000_000,   0.40},
        {1_000_000_000, 0.42},
        {Double.MAX_VALUE, 0.45},
    };

    /**
     * 과세표준에 세율 적용 → 산출세액 계산 (소득세법 제55조).
     * 세율 개정 시 TAX_BRACKETS 업데이트.
     */
    public long calculateTax(long taxBase) {
        long tax = 0;
        long prev = 0;
        for (double[] bracket : TAX_BRACKETS) {
            long limit = (long) bracket[0];
            double rate = bracket[1];
            if (taxBase <= limit) {
                tax += (long) ((taxBase - prev) * rate);
                break;
            }
            tax += (long) ((limit - prev) * rate);
            prev = limit;
        }
        return tax;
    }

    /**
     * 과세표준에 해당하는 세율 반환 (소득세법 제55조 세율표).
     */
    public double getApplicableRate(long taxBase) {
        for (double[] bracket : TAX_BRACKETS) {
            if (taxBase <= (long) bracket[0]) {
                return bracket[1];
            }
        }
        return 0.45;
    }

    /**
     * 세율 구간별 누진공제액 계산.
     * 소득세법 제55조 개정 시 공제액도 함께 재계산 필요.
     */
    public long getProgressiveDeduction(long taxBase) {
        long deduction = 0;
        long prev = 0;
        double prevRate = 0;
        for (double[] bracket : TAX_BRACKETS) {
            long limit = (long) bracket[0];
            double rate = bracket[1];
            if (taxBase <= limit) {
                break;
            }
            deduction += (long) ((limit - prev) * (rate - prevRate));
            prev = limit;
            prevRate = rate;
        }
        return deduction;
    }
}
