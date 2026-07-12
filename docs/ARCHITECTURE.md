# 아키텍처

## 디렉토리 구조
```
{실제 구조를 스택에 맞게 기록. 기존 프로젝트라면 "현재 구조"를 사실대로 적는다.

예 (React):            예 (Spring):              예 (Python):
src/                   src/main/java/...         app/
├── pages/             ├── controller/           ├── api/
├── components/        ├── service/              ├── services/
├── services/          ├── repository(dao)/      ├── models/
├── types/             └── dto/                  └── core/
└── lib/               src/main/resources/sql/
}
```

## 레이어 규칙
{어느 레이어가 어느 레이어만 호출할 수 있는지.
예: controller → service → repository. 역방향/건너뛰기 금지.}

## 데이터 흐름
```
{입력 → 처리 → 저장/출력이 어떻게 흐르는지 한 줄 다이어그램}
```

## 외부 연동
{연동하는 시스템/API 목록과 계약(스펙 문서 위치). 없으면 섹션 삭제.}
