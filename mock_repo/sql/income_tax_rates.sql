-- 소득세법 제55조 세율 테이블
-- 세율 개정 시 이 테이블을 UPDATE 하고 effective_from 을 시행일로 설정한다.
CREATE TABLE income_tax_rates (
    id              INTEGER PRIMARY KEY,
    bracket_no      INTEGER      NOT NULL,
    upper_limit     BIGINT,                      -- NULL = 최고 구간(상한 없음)
    tax_rate        DECIMAL(5,4) NOT NULL,
    deduction       BIGINT       DEFAULT 0,      -- 누진공제액
    effective_from  DATE         NOT NULL,
    law_reference   VARCHAR(100)                 -- 근거 조문 (예: 소득세법 제55조 제1항)
);

-- 2024년 기준 세율 (소득세법 제55조 제1항)
INSERT INTO income_tax_rates
    (bracket_no, upper_limit, tax_rate, deduction, effective_from, law_reference)
VALUES
    (1,    14000000, 0.0600,        0, '2024-01-01', '소득세법 제55조 제1항'),
    (2,    50000000, 0.1500,   840000, '2024-01-01', '소득세법 제55조 제1항'),
    (3,    88000000, 0.2400,  6240000, '2024-01-01', '소득세법 제55조 제1항'),
    (4,   150000000, 0.3500, 15360000, '2024-01-01', '소득세법 제55조 제1항'),
    (5,   300000000, 0.3800, 19860000, '2024-01-01', '소득세법 제55조 제1항'),
    (6,   500000000, 0.4000, 25860000, '2024-01-01', '소득세법 제55조 제1항'),
    (7,  1000000000, 0.4200, 35860000, '2024-01-01', '소득세법 제55조 제1항'),
    (8,         NULL, 0.4500, 65860000, '2024-01-01', '소득세법 제55조 제1항');
