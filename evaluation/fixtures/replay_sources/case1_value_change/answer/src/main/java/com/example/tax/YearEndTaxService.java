package com.example.tax;

/**
 * 연말정산 세액공제 합산 — 상수는 {@link TaxConstants} 에서만 읽는다.
 * replay fixture 전용 합성 코드 (regtax-monitor evaluation)
 */
public class YearEndTaxService {

    /**
     * 자녀세액공제 합계 (소득세법 제59조의2).
     */
    public long childTaxCredit(int childCount) {
        if (childCount <= 0) {
            return 0L;
        }
        return TaxConstants.CHILD_TAX_CREDIT * childCount;
    }

    /**
     * 부양가족 기본공제 합계 (소득세법 제50조).
     */
    public long dependentDeduction(int dependentCount) {
        if (dependentCount <= 0) {
            return 0L;
        }
        return TaxConstants.DEPENDENT_DEDUCTION * dependentCount;
    }

    /**
     * 연금계좌 세액공제 — 한도로 절사 (소득세법 제59조의3).
     */
    public long pensionCredit(long paidAmount) {
        return Math.min(paidAmount, TaxConstants.PENSION_CREDIT_LIMIT);
    }
}
