package com.example.tax.generated;

/**
 * 빌드 산출물을 흉내낸 합성 파일 (regtax-monitor Issue #0019).
 *
 * `build/` 아래에 있으므로 adapter 의 EXCLUDED_DIRS 에서 걸러져야 한다 — 이 파일의
 * 심볼(JunkGenerated·generatedCredit·GENERATED_CREDIT)이 그래프에 나타나면
 * 빌드 산출물이 인덱스를 오염시키고 있다는 뜻이다(ADR-013).
 */
public class JunkGenerated {

    public static final long GENERATED_CREDIT = 999999L;

    public long generatedCredit(int taxYear) {
        return GENERATED_CREDIT + taxYear;
    }
}
