# Home Mock Retrieval Benchmark

- 실행일: 2026-07-24
- 목적: `#0010` benchmark CLI와 실제 bge-m3/Chroma 연결 검증
- 데이터: 공개 synthetic core dataset 중 retrieval 평가 11건
- 저장소: `evaluation/fixtures/repositories/mock_tax` (파일 2개)
- 모델: `BAAI/bge-m3`
- 전용 index: `evaluation/chroma_mock_tax` (2 chunks, gitignore)

## 실행 명령

```bash
python -m app.evaluation.retrieval_benchmark \
  --dataset evaluation/datasets/core.yaml \
  --result-dir evaluation/results \
  --run-name home-mock-baseline \
  --repo-root evaluation/fixtures/repositories/mock_tax \
  --persist-dir evaluation/chroma_mock_tax \
  --build-index \
  --refresh-caches
```

이미 전용 index가 있으면 `--build-index`를 생략한다. 저장소를 바꾸면 새 persist
directory를 사용하고 `--refresh-caches`를 반드시 지정한다.

## 검증 결과

| Experiment | Recall@1 | Recall@5 | MRR | Precision@5 | Provider failures |
|---|---:|---:|---:|---:|---:|
| rag_only | 1.000 | 1.000 | 1.000 | 0.500 | 0 |
| rag_dict | 1.000 | 1.000 | 1.000 | 0.500 | 0 |
| rag_const | 1.000 | 1.000 | 1.000 | 0.500 | 0 |
| hybrid_all | 1.000 | 1.000 | 1.000 | 0.500 | 0 |
| verified_hybrid | 1.000 | 1.000 | 1.000 | 0.500 | 0 |

## 해석 제한

- 파일이 2개뿐인 fixture라 RAG만으로도 모든 정답 파일이 1위다.
- 따라서 이 결과로 결합 검색의 성능 향상을 주장할 수 없다.
- 첫 실험 지연에는 bge-m3 cold start가 포함되므로 후속 warm latency와 직접 비교하지 않는다.
- verified fixture가 없어 `verified_hybrid`의 추가 효과는 측정되지 않는다.
- 실제 효과 판단은 회사 private dataset과 실제 eHR index에서 다시 수행한다.

## 실행 중 발견·수정한 문제

동일 파일의 서로 다른 symbol 후보를 File Recall에서 중복 집계해 Recall이 1을
초과하던 문제를 발견했다. 파일 경로를 중복 제거하고 metric도 집합 교집합으로
계산하도록 수정했으며 회귀 테스트를 추가했다.
