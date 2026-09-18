# DFG Global Project & Work Portfolio Architecture

## 1. Overview & Constitutional Purpose

The DFG Universe establishes a clear operational principle:

> **NO RUNNABLE P0 WORK DOES NOT MEAN NO DFG WORK.**

The DFG Universe encompasses multiple projects, products, commercial ventures, research initiatives, engineering frameworks, and future opportunities at varying maturity levels. Priority orders eligible work; it does not freeze the Universe when a higher-priority task is temporarily waiting on resources, dependencies, or decisions.

Lucius utilizes available execution capacity to advance lower-priority or preparatory work **without**:
- Displacing runnable higher-priority work.
- Inventing artificial business or product decisions.
- Crossing authority gates (`CLASS_A`, `CLASS_B`, `CLASS_C`).
- Fabricating evidence or missing citations.
- Creating code changes merely to keep compute busy.

---

## 2. Progressive Project Readiness Taxonomy

Every accepted DFG project progressively accumulates canonical structure to advance safely when capacity becomes available:

```
IDEA
  ↓
ACCEPTED_CONCEPT
  ↓
STRUCTURED_CONCEPT
  ↓
RESEARCH_REQUIRED / RESEARCHING
  ↓
DESIGN_PARTIAL
  ↓
DESIGN_READY
  ↓
ENGINEERING_READY
  ↓
ACTIVE
  ↓
OPERATIONAL
  ↓
MONETIZING
```

### Alternative & Terminal States
- `WAITING`: Dependent on resources, external inputs, or scheduled wake times.
- `PARKED`: Intentionally on hold per operator direction.
- `BLOCKED`: Held by legal, regulatory, capital, or decision constraints.
- `REJECTED`: Formally discarded.
- `SUPERSEDED`: Replaced by a newer canonical architecture or project.
- `COMPLETED`: Finished and fully operational/archived.

---

## 3. Project Design Coverage & Design Debt

### Design Coverage Levels (D0–D5)
- **D0 — `IDEA_ONLY`**: Concept recorded, no formal structure or requirements.
- **D1 — `CONCEPT_DEFINED`**: Problem, outcome, and initial scope stated.
- **D2 — `PARTIAL_DESIGN`**: Key requirements and dependencies identified; DDB Lite.
- **D3 — `STRUCTURED_DESIGN`**: Architecture, business rules, and acceptance criteria specified; DDB Standard.
- **D4 — `ENGINEERING_READY`**: Detailed contract, plan, and test strategy frozen; DDB Full.
- **D5 — `OPERATIONAL_DESIGN`**: Live/production operating specifications.

### Project Design Debt
*Project Design Debt* indicates that a project exists in the accepted portfolio, but lacks sufficient canonical design documentation for safe autonomous software engineering. Lower-priority projects (P3/P4) may legitimately accumulate design debt; the portfolio layer renders this debt visible so spare execution capacity can improve design readiness without prematurely writing un-designed code.

---

## 4. Priority Model & Minimum Sufficient Resource Scheduling

### Priority Hierarchy (P0–P4)
- **P0 (`CRITICAL`)**: Core production/runtime bottlenecks (e.g. Lucius core, Billy Unified Runtime, Binance Executor safety).
- **P1 (`HIGH`)**: Revenue services & primary platform capabilities (e.g. FP Labs, Cinema Collection, Alfred Orchestration).
- **P2 (`NORMAL`)**: Standard validation & community services (e.g. Commercial Photography, FP CriptoClub, Andy/Ledger).
- **P3 (`LOW`)**: Exploratory tech scouts & secondary pipelines (e.g. DealHunter, Commerce Affiliate, Social Engine).
- **P4 (`SPECULATIVE`)**: Early-stage conceptual designs & future feature ideas (e.g. Post Assistant).

### Minimum Sufficient Resource Principle
1. When P0 is `WAITING_RESOURCE` or `WAITING_DEPENDENCY`, the mission supervisor retains ownership of P0 while lower-priority ready work (e.g., P1/P2/P3/P4) executes using idle local compute.
2. Local Tier 1 compute (`qwen3:8b`) executes deterministic research and preparation without consuming scarce frontier model quota.
3. When P0 wakes or becomes eligible, the scheduler re-evaluates priority at cycle boundaries without duplicate execution or unsafe preemption.

---

## 5. Idea-to-Portfolio Ingestion Lifecycle & Role Boundaries

### Ingestion Flow
```
IDEA → CAPTURE → PORTFOLIO EVALUATION → ACCEPT / PARK / REJECT → PRIORITY & DESIGN STATE → CANONICAL PROJECT RECORD → WORK PACKAGES
```

### System & Agent Role Boundaries
- **ALFRED**: Primary operator/butler coordination layer and user interface.
- **DARWIN**: Research, source acquisition, intelligence, and evidence extraction.
- **LUCIUS**: Autonomous engineering, execution runtime, dispatcher, and worktree isolation.
- **ANDY**: Financial governance, administration, and risk control agent.
- **LEDGER**: Double-entry financial system of record and database.
- **PROJECT ENGINES**: Domain engines owning their specific canonical documentation and roadmaps.

---

## 6. Canonical Global Project Inventory Summary (22 Projects)

| Project ID | Canonical Name | Type | Priority | Portfolio State | Design Coverage | Next Gate |
|---|---|---|---|---|---|---|
| `lucius-engineering` | Lucius Engineering | DFG_INTERNAL | P1 | ACTIVE | D4 | 4_HOUR_TRAVEL_RUN_PILOT_VERIFICATION |
| `darwin-research-engine` | Darwin Research Engine | DFG_INTERNAL | P1 | OPERATIONAL | D5 | MONETIZATION_EVIDENCE_PROPOSAL_DISPATCH |
| `billy-production-engine` | Billy Production Engine | DFG_INTERNAL | P0 | ACTIVE | D4 | PHASE_6_5_ASSET_LIFECYCLE_ACCEPTANCE |
| `dfg-binance-executor` | DFG Binance Executor | DFG_INTERNAL | P1 | OPERATIONAL | D5 | PAPER_VALIDATION_BRIDGE_HARNESS |
| `fpcc-trade-dashboard` | FPCC Trade Dashboard | DFG_INTERNAL | P2 | ACTIVE | D4 | METRICS_ENGINE_NORMALIZATION_ACCEPTANCE |
| `alfred-orchestration` | Alfred Orchestration Layer | DFG_INTERNAL | P1 | DESIGN_PARTIAL | D3 | ALFRED_DDB_FREEZE |
| `andy-finance` | Andy Financial & Admin Agent | DFG_INTERNAL | P2 | DESIGN_PARTIAL | D2 | ANDY_OPERATING_BRIEF_V1 |
| `ledger-bookkeeping` | Ledger Financial Data Layer | DFG_INTERNAL | P2 | DESIGN_PARTIAL | D2 | LEDGER_SCHEMA_V1_FROZEN |
| `fp-labs` | FP Labs External Services | COMMERCIAL | P1 | RESEARCHING | D2 | OPERATOR_RATE_CARD_CONFIRMATION |
| `cinema-collection` | Cinema Collection Equipment Rental | COMMERCIAL | P1 | RESEARCHING | D2 | OPERATOR_GEAR_MANIFEST_CONFIRMATION |
| `commercial-photography` | Commercial Product Photography | COMMERCIAL | P2 | RESEARCHING | D2 | SAMPLE_CLIENT_PROPOSAL_DISPATCH |
| `locations-concierge` | Locations Concierge CDMX | COMMERCIAL | P2 | RESEARCHING | D2 | INITIAL_COMMISSION_STRUCTURE_DOSSIER |
| `dealhunter` | DealHunter Opportunity Scout | R_AND_D | P3 | RESEARCH_REQUIRED | D1 | TECH_FEASIBILITY_REPORT |
| `social-engine` | Social Engine Distribution Pipeline | DFG_INTERNAL | P3 | ACCEPTED_CONCEPT | D0 | SOCIAL_DISTRIBUTION_BRIEF_V1 |
| `commerce-affiliate-engine` | Commerce & Affiliate Intelligence | COMMERCIAL | P3 | RESEARCH_REQUIRED | D1 | AFFILIATE_PROGRAM_FEASIBILITY_DOSSIER |
| `fp-criptoclub` | FP CriptoClub | COMMERCIAL | P2 | OPERATIONAL | D5 | SUBSCRIBER_GROWTH_REPORT |
| `billy-the-trader` | Billy the Trader Media Brand | COMMERCIAL | P2 | RESEARCHING | D2 | SPONSORSHIP_AND_MERCH_BRIEF |
| `dfg-prediction-engine` | DFG Prediction Markets LATAM | R_AND_D | P3 | BLOCKED | D1 | LEGAL_COMPLIANCE_CLEARANCE |
| `arbitrage-engine` | DFG Crypto Arbitrage Engine | R_AND_D | P3 | BLOCKED | D1 | MINIMUM_CAPITAL_ALLOCATION_APPROVAL |
| `section-8-real-estate` | Section 8 Real Estate Engine | COMMERCIAL | P3 | BLOCKED | D1 | JURISDICTIONAL_LEGAL_CLEARANCE |
| `post-assistant` | Production Post Assistant | DFG_INTERNAL | P4 | ACCEPTED_CONCEPT | D0 | POST_ASSISTANT_FEATURE_BRIEF |
| `universe-core` | DFG Universe Core Standards | DFG_INTERNAL | P1 | OPERATIONAL | D5 | REGRESSION_GUARD_INTEGRATION |
