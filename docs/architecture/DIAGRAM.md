# V2 Architecture Flow

## Overall Flow

```text
          Law / Regulation
                  │
                  ▼
        Change Classification
                  │
                  ▼
      Retrieval Optimizer (RAG)
      ├─ Verified Mapping
      ├─ Code Graph
      ├─ Historical Replay
      └─ Term Dictionary
                  │
                  ▼
          Local LLM Inference
                  │
                  ▼
      Candidate Patch Generation
                  │
                  ▼
      Evaluation Framework
      ├─ Recall@K
      ├─ MRR
      ├─ Patch Validation
      └─ Confidence Score
                  │
                  ▼
          Human Review Gate
                  │
                  ▼
            Approved Patch
                  │
                  ▼
            Audit & Traceability
```

## Layer Responsibilities

### 1. Change Classification

법령 변경을 구조화하고 영향 범위를 추정한다.

### 2. Retrieval Optimizer

LLM에게 필요한 최소 Context를 구성한다.

- Verified Mapping
- Code Graph
- Historical Replay
- Dictionary

### 3. Local LLM

Patch 후보 생성.

### 4. Evaluation

자동 평가.

### 5. Human Review

최종 승인.

### 6. Audit

모든 실행을 재현 가능하게 저장.