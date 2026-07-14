# regtax-monitor Documentation Guide

이 디렉터리는 **regtax-monitor** 프로젝트의 공식 설계 문서를 관리한다.

모든 설계와 구현은 이 문서를 시작점으로 한다.

---

# 문서 읽기 순서

새로운 개발자 또는 AI 에이전트는 반드시 아래 순서대로 문서를 읽는다.

1. product/PRD.md
2. architecture/ARCHITECTURE.md
3. architecture/ARCHITECTURE_V2.md
4. architecture/ADR.md
5. specifications/*
6. roadmap/IMPLEMENTATION_ROADMAP.md

operations 문서는 필요한 경우에만 참고한다.

---

# 문서 구조

```
docs/

architecture/
    ARCHITECTURE.md
    ARCHITECTURE_V2.md
    ADR.md
    OPTIMIZER_DESIGN.md

product/
    PRD.md

roadmap/
    IMPLEMENTATION_ROADMAP.md

specifications/
    EVALUATION_SPEC.md
    CHANGE_CLASSIFICATION_SPEC.md
    RETRIEVAL_EXPERIMENT_SPEC.md
    AUDIT_AND_TRACEABILITY_SPEC.md
    VERIFIED_MAPPING_SPEC.md
    HISTORICAL_REPLAY_SPEC.md
    CODE_GRAPH_SPEC.md

operations/
    OBSERVABILITY.md
    UI_GUIDE.md
```

---

# 문서 역할

## Product

프로젝트 목표와 요구사항을 정의한다.

## Architecture

프로젝트 구조와 설계 원칙을 정의한다.

- 기존 구조
- V2 확장 구조
- ADR
- Optimizer 설계

Architecture는 프로젝트의 기술적 기준이다.

---

## Specifications

각 기능의 구현 계약이다.

현재 포함된 Spec은 다음과 같다.

- Evaluation Framework
- Change Classification
- Retrieval Experiment
- Audit & Traceability
- Verified Mapping
- Historical Replay
- Code Graph

모든 구현은 Spec을 기준으로 수행한다.

---

## Roadmap

Roadmap은 구현 순서를 정의한다.

Issue는 반드시 아래 순서대로 진행한다.

```
#0003
↓
#0004
↓
#0005
↓
...
↓
#0021
```

여러 Issue를 동시에 구현하지 않는다.

---

# V2 설계 원칙

V2는 기존 프로젝트를 대체하지 않는다.

기존 ARCHITECTURE.md를 유지하면서 기능을 확장한다.

ADR에서 결정된 사항을 우선한다.

---

# Claude Code 구현 규칙

Claude Code는 다음 원칙을 따른다.

- 문서를 모두 읽은 후 구현한다.
- Spec 없는 기능은 구현하지 않는다.
- Roadmap 순서를 변경하지 않는다.
- Issue 하나만 구현한다.
- 테스트를 통과한 후 다음 Issue로 진행한다.
- 기존 테스트를 깨지 않는다.
- 기존 동작을 변경하지 않는다.

---

# 구현 순서

```
#0003 → #0004 → #0005 → #0006 → #0007
→ #0008 → #0009 → #0010 → #0011 → #0012
→ #0013 → #0014 → #0015 → #0016
→ #0017 → #0018 → #0019 → #0020 → #0021
```

---

# V2 구현 단계

1. Foundation Contracts
2. Evaluation Framework
3. Change Classification
4. Retrieval Integration
5. Automation Policy
6. Audit & Traceability
7. Verified Mapping
8. Historical Replay
9. Code Graph
10. Release

---

# 참고

operations 문서는 구현이 아닌 운영을 위한 문서이다.

루트의 README.md는 GitHub 프로젝트 소개용이며,
이 문서는 프로젝트 내부 설계 및 구현을 위한 문서 인덱스 역할을 한다.