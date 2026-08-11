package com.example.tax;

import org.junit.jupiter.api.Test;

/**
 * 합성 fixture 전용 테스트 (regtax-monitor Issue #0019).
 * Test → Service 관계를 만들기 위한 최소 코드이며 실제로 실행되지 않는다.
 */
public class TaxServiceTest {

    @Test
    public void calculatesCreditFromMapper() {
        TaxService service = new TaxService(null);
        service.calculateCredit(2026, 2);
    }

    @Test
    public void appliesCappedCredit() {
        TaxService service = new TaxService(null);
        service.applyCredit(2026, 900000L);
    }
}
