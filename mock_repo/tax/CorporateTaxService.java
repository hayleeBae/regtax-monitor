package com.example.tax;

/**
 * 법인세법 제55조에 따른 법인세 계산 서비스.
 */
public class CorporateTaxService {

    // 법인세법 제55조: 법인세율 구간
    private static final double[][] CORP_TAX_RATES = {
        {200_000_000L,      0.09},
        {20_000_000_000L,   0.19},
        {300_000_000_000L,  0.21},
        {Double.MAX_VALUE,  0.24},
    };

    /**
     * 과세표준에 법인세율 적용 → 산출세액 (법인세법 제55조).
     */
    public long calculateCorporateTax(long taxableIncome) {
        long tax = 0;
        long prev = 0;
        for (double[] rate : CORP_TAX_RATES) {
            long limit = (long) rate[0];
            if (taxableIncome <= limit) {
                tax += (long) ((taxableIncome - prev) * rate[1]);
                break;
            }
            tax += (long) ((limit - prev) * rate[1]);
            prev = limit;
        }
        return tax;
    }

    /**
     * 과세표준 구간에 해당하는 법인세율 반환.
     */
    public double getCorporateRate(long taxableIncome) {
        for (double[] rate : CORP_TAX_RATES) {
            if (taxableIncome <= (long) rate[0]) {
                return rate[1];
            }
        }
        return 0.24;
    }
}
