package com.example.tax;

/**
 * 일부러 깨뜨린 합성 파일 (regtax-monitor Issue #0019).
 *
 * 중괄호가 닫히지 않는다 — 레거시 트리에 섞여 있는 파싱 불가 파일을 흉내낸다.
 * harvest 는 이 파일만 건너뛰고(skipped_files) 나머지는 정상 추출해야 한다.
 */
public class Broken {

    public long unclosed(long amount) {
        if (amount > 0) {
            return amount;
    }
