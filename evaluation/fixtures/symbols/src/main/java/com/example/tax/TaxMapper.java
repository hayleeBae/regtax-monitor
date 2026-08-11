package com.example.tax;

/**
 * 합성 fixture 전용 매퍼 인터페이스 — TaxMapper.xml 의 namespace 와 짝을 이룬다.
 * 메서드에 본문이 없으므로 심볼로는 클래스(인터페이스) 하나만 나온다.
 */
public interface TaxMapper {

    long findCredit(int taxYear);

    int updateCredit(int taxYear, long amount);
}
