package com.example.tax;

/**
 * 합성 fixture 전용 서비스 (regtax-monitor Issue #0019).
 *
 * Service → Mapper(statement 호출) → Constant(참조) 관계를 한 파일에 담는다.
 * 실제 eHR 코드가 아니다.
 */
public class TaxService {

    private final TaxMapper taxMapper;

    public TaxService(TaxMapper taxMapper) {
        this.taxMapper = taxMapper;
    }

    public long calculateCredit(int taxYear, int childCount) {
        long stored = taxMapper.findCredit(taxYear);
        if (stored > 0) {
            return stored * childCount;
        }
        return TaxConstants.CHILD_CREDIT * childCount;
    }

    public int applyCredit(int taxYear, long amount) {
        long capped = Math.min(amount, TaxConstants.EARNED_INCOME_LIMIT);
        return taxMapper.updateCredit(taxYear, capped);
    }
}
