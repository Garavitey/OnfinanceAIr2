# Build Plan — POC → PLAN → MVP

Companion to `HLD.md`. The HLD is the *design*; this is the *build order*, cut into 24 steps that each fit one cheap agent invocation.

**Decisions this plan is built on:** Postgres + Redis + Kroki via docker-compose (so all 14 diagram types are in scope) · single-file HTML frontend, no npm/React · ART built to the trainer boundary (feedback → reward → JSONL export → stub trainer, no GPU) · non-functional requirements (auth hardening, retries, metrics, rate limits) explicitly out of scope everywhere.

---

## How this plan stays cheap

The expensive failure mode is an agent reading 700 lines of HLD to write 40 lines of code. Four rules prevent it:

1. **No step reads `HLD.md`.** Every step prompt below is self-contained. `AGENT_RULES.md` enforces this.
2. **One keystone contract file.** Step **S2** writes `app/contracts.py` — every Pydantic model, enum, and SSE event shape. After S2, steps read *only* that file to understand each other. No step ever reads another step's implementation.
3. **One step, one or two files.** Steps are sized so an agent never needs to hold more than ~3 files in context.
4. **Model floor by step type.** Mechanical steps (schema, scaffolds, wiring) run on `sonnet`. Only the genuinely reasoning-heavy steps need `opus` — marked in the tables.

### Dispatching a step

Each step's **Prompt** block is copy-paste ready. Prepend this line to it:

```
Read AGENT_RULES.md first. Then do only this step:
```

Then dispatch — in a fresh Conductor workspace, or in-session:

```
Agent(subagent_type="claude", model="<sonnet|opus>", prompt="<the block>")
```

Steps in the same **wave** have no dependency on each other and can run in parallel.

---

## Dependency graph

```mermaid
graph LR
    C1[C1 skeleton] --> C2[C2 one-shot gen]
    C1 --> C3[C3 mermaid validator]
    C2 --> C4[C4 two-phase + repair]
    C3 --> C4

    C1 --> S1[S1 docker-compose]
    C1 --> S2[S2 contracts]
    S1 --> S3[S3 db models]
    S2 --> S3
    S2 --> S4[S4 type matrix]
    S2 --> S5[S5 api skeleton]
    S4 --> S5
    S5 --> S6[S6 static UI vs stubs]
    S2 --> S6

    C4 --> M1[M1 claude client]
    S2 --> M1
    M1 --> M2[M2 spec extractor]
    S4 --> M3a[M3a mermaid templates]
    S4 --> M3b[M3b plantuml templates]
    C3 --> M4[M4 validator svc]
    S1 --> M4
    M1 --> M5[M5 generator + repair]
    M3a --> M5
    M3b --> M5
    M4 --> M5
    M2 --> M6[M6 orchestrator + SSE]
    M5 --> M6
    S5 --> M6
    S3 --> M7[M7 sessions + versions]
    S5 --> M8[M8 auth stub]
    S3 --> M9[M9 feedback endpoint]
    S5 --> M9
    M9 --> M10[M10 reward + JSONL + stub trainer]
    S6 --> M11[M11 wire UI to real API]
    M6 --> M11
    M9 --> M11
    M6 --> M12[M12 redis cache]
    M6 --> M13[M13 demo seed]
    M7 --> M13
    M9 --> M13
```

### Execution waves

| Wave | Steps | Parallel |
|---|---|---|
| 1 | C1 | — |
| 2 | C2, C3 | 2 |
| 3 | C4 | — |
| **— POC GATE —** | | |
| 4 | S1, S2 | 2 |
| 5 | S3, S4 | 2 |
| 6 | S5 | — |
| 7 | S6 | — |
| **— PLAN GATE —** | | |
| 8 | M1, M3a, M3b, M4, M7, M8 | **6** |
| 9 | M2, M5, M9 | 3 |
| 10 | M6, M10 | 2 |
| 11 | M11, M12 | 2 |
| 12 | M13 | — |
| **— MVP GATE —** | | |

---

# Phase 0 — POC

**Purpose:** retire the one risk that invalidates the whole design if it fails — *can Claude reliably emit parseable diagram DSL, and does the two-phase spec approach actually work?* Throwaway code in `poc/`, deleted after Phase 2.

**Gate to exit:** `python poc/two_phase.py` run 10× produces valid, parseable diagrams ≥9 times.

| Step | Depends | Model | Est. |
|---|---|---|---|
| C1 | — | sonnet | ~10k |
| C2 | C1 | sonnet | ~20k |
| C3 | C1 | sonnet | ~25k |
| C4 | C2, C3 | **opus** | ~45k |

### C1 — Repo skeleton

**Files:** `requirements.txt`, `.env.example`, `.gitignore`, `poc/__init__.py`

```
Create the minimal Python project skeleton. No application logic.

1. requirements.txt with: fastapi, uvicorn[standard], anthropic, sqlalchemy,
   psycopg[binary], redis, pydantic, pydantic-settings, httpx, python-dotenv
2. .env.example with these keys and placeholder values:
   ANTHROPIC_API_KEY, DATABASE_URL (postgresql+psycopg://uml:uml@localhost:5432/uml),
   REDIS_URL (redis://localhost:6379/0), KROKI_URL (http://localhost:8000)
3. .gitignore covering: .env, __pycache__/, *.pyc, .venv/, node_modules/, *.db
4. Empty poc/__init__.py

VERIFY: `pip install -r requirements.txt` completes without error.
```

### C2 — One-shot generation

**Files:** `poc/generate.py`

```
Write poc/generate.py — a single script proving we can get structured diagram
DSL out of Claude.

- Load ANTHROPIC_API_KEY from .env via python-dotenv.
- Hardcode this prompt as a module constant:
  "I am building a compliance monitoring solution which pulls the latest
   circulars from SEBI, parses them into a table of clauses, then extracts new
   compliance requirements, performs gap analysis against the existing
   compliance setup, and reports IT and operational impact."
- One call to client.messages.create with:
    model="claude-opus-5"
    max_tokens=16000
    thinking={"type": "adaptive"}
    output_config={"effort": "medium", "format": {"type": "json_schema",
      "schema": {"type":"object","properties":{
        "diagram_type":{"type":"string"},
        "renderer":{"type":"string","enum":["mermaid","plantuml"]},
        "source":{"type":"string"},
        "notes":{"type":"string"}},
      "required":["diagram_type","renderer","source","notes"],
      "additionalProperties": False}}}
    system = instructions to emit a Mermaid `sequenceDiagram` for the described
      system, source field containing ONLY valid Mermaid DSL, no code fences.
- Because output_config.format is set, the first text block is valid JSON.
  Parse it with json.loads and print the `source` field.
- Do NOT pass temperature or budget_tokens — both return 400 on this model.

VERIFY: `python poc/generate.py` prints text whose first line is "sequenceDiagram".
```

### C3 — Mermaid validator sidecar

**Files:** `validator/package.json`, `validator/validate.mjs`, `poc/validate.py`

```
Build the deterministic Mermaid parse gate. Two parts.

PART 1 — validator/validate.mjs (Node):
- package.json with deps: mermaid, jsdom. Type "module".
- validate.mjs reads Mermaid source from stdin, calls mermaid.parse(), prints
  {"ok":true} or {"ok":false,"error":"<message>"} to stdout, exits 0 either way.
- mermaid needs a DOM. Set up jsdom globals: window, document, Element,
  SVGElement, HTMLElement, getComputedStyle.
- CRITICAL GOTCHA: `global.navigator = dom.window.navigator` throws
  "Cannot set property navigator of #<Object> which has only a getter" on
  modern Node. You MUST use:
    Object.defineProperty(global, "navigator",
      { value: dom.window.navigator, configurable: true });
- Call mermaid.initialize({ startOnLoad: false, securityLevel: "strict" }).

PART 2 — poc/validate.py:
- async function validate_mermaid(source: str) -> tuple[bool, str | None]
- Shells out to `node validator/validate.mjs` via asyncio.create_subprocess_exec,
  pipes source to stdin, parses the JSON result.
- __main__ block: validate a known-good diagram and a known-broken one, print both.

VERIFY: `cd validator && npm install` then `python poc/validate.py` →
prints ok for the good diagram and an error message for the broken one.
```

### C4 — Two-phase generation with repair loop

**Files:** `poc/two_phase.py`

```
Prove the core intelligence pipeline end to end. Read poc/generate.py and
poc/validate.py first and reuse their patterns.

poc/two_phase.py does, in order:

1. SPEC EXTRACTION — one Claude call, effort="high", structured output with
   schema: {entities[], actors[], services[], flows[], states[]} (each a list
   of strings or shallow objects; keep the schema small). Input is the same
   hardcoded SEBI prompt from poc/generate.py. Print the spec.

2. CONCURRENT GENERATION — for diagram types ["sequence","component"], run
   asyncio.gather over one Claude call each. Each call gets effort="medium" and
   receives the SPEC JSON as its user message, NOT the original prose prompt.
   That is the whole point of two-phase: comprehension happens once.

3. VALIDATION + REPAIR — pass each result through validate_mermaid. On failure,
   re-call Claude with effort="low", passing the broken source AND the exact
   parser error, asking for a corrected version. Max 3 total attempts per
   diagram. Track attempt count.

4. Print a summary table: diagram_type, status (valid/repaired/failed),
   attempts, source line count.

Use the async anthropic client (anthropic.AsyncAnthropic) throughout.

VERIFY: `python poc/two_phase.py` → both diagrams reach status valid or
repaired. Run it 3 times; report the attempt counts you saw.
```

---

# Phase 1 — PLAN

**Purpose:** stand up the real structure — infra, frozen contracts, schema, routes, UI shell — with **stubbed** intelligence. After this phase the app runs end to end on fixtures, which is what lets Phase 2's many steps run in parallel without touching each other.

**Gate to exit:** `docker compose up -d` + `uvicorn app.main:app` → open the UI, click generate, a fixture diagram streams in over SSE and renders.

| Step | Depends | Model | Est. |
|---|---|---|---|
| S1 | C1 | sonnet | ~20k |
| S2 | C1 | **opus** | ~40k |
| S3 | S1, S2 | sonnet | ~30k |
| S4 | S2 | sonnet | ~20k |
| S5 | S2, S4 | sonnet | ~40k |
| S6 | S2, S5 | sonnet | ~45k |

### S1 — Infrastructure

**Files:** `docker-compose.yml`, `scripts/health.sh`

```
Write docker-compose.yml with three services, no application container:

- postgres:16 — user/password/db all "uml", port 5432:5432, named volume,
  healthcheck using pg_isready.
- redis:7-alpine — port 6379:6379, healthcheck using redis-cli ping.
- yuzutech/kroki — port 8000:8000. This renders the 7 UML types Mermaid cannot.
  Add the companion yuzutech/kroki-mermaid service only if Kroki requires it
  for your chosen tag; otherwise omit.

Also scripts/health.sh: curl/ping each of the three and print a pass/fail line
per service, exit non-zero if any fail.

VERIFY: `docker compose up -d && sleep 15 && bash scripts/health.sh` → all three pass.
```

### S2 — Contracts (KEYSTONE — freeze after this)

**Files:** `app/contracts.py`, `app/__init__.py`

```
Write app/contracts.py — the single shared interface for every other step in
this project. Every later step imports from here and never edits it. Make it
complete and self-explanatory; no business logic, only types.

Pydantic v2 models + StrEnums:

1. DiagramType(StrEnum) — exactly these 14 members:
   CLASS, OBJECT, COMPONENT, COMPOSITE_STRUCTURE, DEPLOYMENT, PACKAGE, PROFILE,
   USE_CASE, ACTIVITY, STATE_MACHINE, SEQUENCE, COMMUNICATION,
   INTERACTION_OVERVIEW, TIMING
   (values are the lowercase names, e.g. "state_machine")

2. Renderer(StrEnum): MERMAID="mermaid", PLANTUML="plantuml"

3. DiagramStatus(StrEnum): VALID, REPAIRED, FAILED

4. GenerateOptions: effort: str = "high", stream: bool = True

5. GenerateRequest: session_id: UUID|None, parent_version: int|None,
   prompt: str (max_length 8000), diagram_types: list[DiagramType]|None,
   options: GenerateOptions = default

6. SystemSpec: entities, actors, services, flows, states, deployment_nodes
   (all list[str], default empty), suggested_diagram_types: list[DiagramType]
   = empty. Keep it flat — it is sent to the LLM as JSON on every generator call.

7. DiagramResult: diagram_type, renderer, source, notes: str = "",
   status: DiagramStatus, repair_attempts: int = 0, parse_error: str|None = None

8. SSE event models, each with a literal `event` field:
   JobStarted(job_id, version, expected: list[DiagramType])
   SpecReady(spec: SystemSpec)
   DiagramComplete(result: DiagramResult)
   DiagramFailed(diagram_type, error, attempts)
   JobComplete(version, succeeded: int, failed: int, latency_ms: int)
   Give each a `to_sse()` method returning the exact wire string:
   "event: <name>\ndata: <json>\n\n"

9. SignalType(StrEnum): RATING, EDIT, REGENERATE, EXPORT, ACCEPT

10. FeedbackRequest: diagram_version_id: UUID, signal_type: SignalType,
    rating: int|None (-1..1), comment: str|None, corrected_dsl: str|None,
    implicit: dict = empty

11. RewardBreakdown: parse_success, lint_score, human_rating, edit_distance,
    implicit_accept (all float, default 0.0), plus a `total` property computing:
    0.30*parse_success + 0.20*lint_score + 0.30*human_rating
    + 0.15*(1 - edit_distance) + 0.05*implicit_accept

12. TrajectoryRecord: trajectory_id: str, stage: str, inputs: dict,
    output: dict, reward: RewardBreakdown, human_correction: str|None

VERIFY: `python -c "import app.contracts as c; print(len(list(c.DiagramType)))"`
prints 14.
```

### S3 — Database models

**Files:** `app/db.py`, `app/models.py`, `scripts/init_db.py`

```
Read app/contracts.py first. Write the persistence layer with SQLAlchemy 2.x
async (create_async_engine, AsyncSession). No Alembic — a create_all script.

app/db.py: engine + async_sessionmaker from DATABASE_URL in env, plus a
get_session() async dependency for FastAPI.

app/models.py — 7 tables, all UUID primary keys defaulting to uuid4,
all with created_at timestamptz default now():

- User(id, external_sub unique, email, training_consent bool default True)
- Session(id, user_id FK, title, head_version int default 0)
- PromptVersion(id, session_id FK, version_no int, parent_id FK self-ref
  nullable, prompt_text Text, diagram_types_requested JSONB, model_id,
  model_params JSONB)
- SystemSpecRow(id, prompt_version_id FK, spec JSONB, input_tokens,
  output_tokens, latency_ms)  [table name "system_spec"]
- Diagram(id, session_id FK, diagram_type, head_version int default 0)
- DiagramVersion(id, diagram_id FK, prompt_version_id FK, version_no,
  renderer, source_dsl Text, status, repair_attempts int default 0,
  parse_error Text nullable, latency_ms, input_tokens, output_tokens,
  cache_read_tokens)
- Feedback(id, diagram_version_id FK, user_id FK, signal_type, rating int
  nullable, comment Text nullable, corrected_dsl Text nullable,
  implicit JSONB, training_status default 'pending')

Versioning is append-only: nothing is ever UPDATEd except the head_version
pointers on Session and Diagram.

Indexes: Session(user_id, created_at desc), PromptVersion(session_id,
version_no), DiagramVersion(diagram_id, version_no),
Feedback(training_status, created_at).

scripts/init_db.py: run create_all against DATABASE_URL.

VERIFY: with docker compose up, `python scripts/init_db.py` succeeds and
`docker compose exec postgres psql -U uml -d uml -c '\dt'` lists 7 tables.
```

### S4 — Diagram type capability matrix

**Files:** `app/diagram_types.py`

```
Read app/contracts.py first. Write app/diagram_types.py — the single source of
truth for which renderer backs each of the 14 UML types.

CAPABILITY: dict[DiagramType, DiagramCapability] where DiagramCapability is a
frozen dataclass of: renderer: Renderer, dsl_keyword: str, blurb: str
(one line on when to use this diagram type), approximated: bool = False.

Routing:
  MERMAID  → CLASS ("classDiagram"), SEQUENCE ("sequenceDiagram"),
             STATE_MACHINE ("stateDiagram-v2"), ACTIVITY ("flowchart TD"),
             COMPONENT ("graph LR"), USE_CASE ("flowchart LR", approximated),
             PACKAGE ("graph TD", approximated)
  PLANTUML → OBJECT, COMPOSITE_STRUCTURE, DEPLOYMENT, PROFILE, COMMUNICATION,
             INTERACTION_OVERVIEW, TIMING
             (dsl_keyword is the PlantUML opener, e.g. "@startuml")

Also:
- renderer_for(t: DiagramType) -> Renderer
- DEFAULT_TYPES: the 4-type minimal set [USE_CASE, COMPONENT, CLASS, SEQUENCE]
  used when a request omits diagram_types.
- A module-level assert that CAPABILITY covers all 14 members of DiagramType.

VERIFY: `python -c "from app.diagram_types import CAPABILITY; print(len(CAPABILITY))"`
prints 14.
```

### S5 — API skeleton with stub handlers

**Files:** `app/main.py`, `app/routes.py`, `app/fixtures.py`

```
Read app/contracts.py and app/diagram_types.py first. Build the FastAPI app with
every route present but NO real logic — handlers return fixtures. Later steps
replace the stub bodies; the shapes must be final now.

app/fixtures.py: FIXTURE_SPEC (a SystemSpec with a few plausible SEBI-domain
entries) and FIXTURE_DIAGRAMS: dict[DiagramType, str] with hand-written valid
Mermaid source for SEQUENCE, COMPONENT, and CLASS. Keep each under 15 lines.

app/routes.py:
- POST /v1/generate → StreamingResponse(media_type="text/event-stream").
  Async generator yields, with asyncio.sleep(0.4) between each:
  JobStarted → SpecReady(FIXTURE_SPEC) → one DiagramComplete per requested
  type (fall back to DEFAULT_TYPES if diagram_types is None; use the fixture
  source if present, else a 2-line placeholder) → JobComplete.
  Use the to_sse() methods from contracts.
- GET  /v1/sessions                     → []
- GET  /v1/sessions/{id}                → 404 for now
- GET  /v1/diagrams/{id}/versions       → []
- POST /v1/feedback                     → 202, {"status":"accepted"}
- GET  /v1/diagram-types                → the CAPABILITY matrix as JSON
- GET  /healthz                         → {"ok": true}

app/main.py: FastAPI app, include the router, CORS allow-all, and mount
StaticFiles at "/" serving the ./static directory with html=True.

VERIFY: `uvicorn app.main:app --port 8080` then
`curl -N -X POST localhost:8080/v1/generate -H 'content-type: application/json'
 -d '{"prompt":"test","diagram_types":["sequence","component"]}'`
streams job_started, spec_ready, 2× diagram_complete, job_complete.
```

### S6 — Frontend shell

**Files:** `static/index.html`

```
Read app/contracts.py (for the SSE event shapes) and app/fixtures.py first.
Write static/index.html — ONE file: inline CSS, inline vanilla JS, mermaid.js
from CDN. No npm, no build step, no React, no frameworks.

Layout — split pane:
- LEFT: chat transcript (user prompts + assistant summaries), a textarea, a
  multi-select of diagram types fetched from GET /v1/diagram-types, Send button.
- RIGHT: a tab bar (one tab per diagram in the current result) and a canvas
  area. Each tab has two sub-views toggled by a small switch: "Rendered" and
  "Source" (a <textarea> showing the DSL).

Behaviour:
- Send → POST /v1/generate, read the response body as a stream and parse SSE
  frames manually (fetch + ReadableStream; EventSource cannot do POST).
- On job_started: create one skeleton tab per expected type.
- On diagram_complete: fill that tab, call mermaid.render() for renderer
  "mermaid"; for renderer "plantuml" just show the source with a note that
  server rendering arrives in a later step.
- On diagram_failed: mark the tab with an error badge and show parse_error.
- On job_complete: enable the Send button again.
- mermaid.initialize({ startOnLoad: false, securityLevel: "strict" }).

Keep it plain and legible — this is a demo surface, not a product UI.

VERIFY: with uvicorn running, open http://localhost:8080, send a prompt, and
confirm fixture diagrams render as tabs. Report what you saw.
```

---

# Phase 2 — MVP

**Purpose:** replace every stub with real logic and deliver the brief's three flows plus its four technical answers.

**Gate to exit:** all three flows demonstrable from the UI — (1) new user gets streamed diagrams, (2) a revised prompt returns an updated set with history preserved, (3) feedback is captured and appears in the JSONL training export.

| Step | Depends | Model | Est. |
|---|---|---|---|
| M1 | C4, S2 | sonnet | ~30k |
| M2 | M1, S2 | sonnet | ~30k |
| M3a | S4, S2 | **opus** | ~50k |
| M3b | S4, S2 | **opus** | ~50k |
| M4 | C3, S1, S2 | sonnet | ~30k |
| M5 | M1, M3a, M3b, M4 | **opus** | ~45k |
| M6 | M2, M5, S5 | **opus** | ~50k |
| M7 | S3, S2 | sonnet | ~40k |
| M8 | S5 | sonnet | ~15k |
| M9 | S3, S2, S5 | sonnet | ~25k |
| M10 | M9, S3 | sonnet | ~40k |
| M11 | S6, M6, M9 | sonnet | ~50k |
| M12 | M6, S1 | sonnet | ~20k |
| M13 | M6, M7, M9 | sonnet | ~25k |

### M1 — Claude client wrapper

**Files:** `app/llm.py`

```
Read app/contracts.py and poc/two_phase.py first — port the working patterns
from the POC into a reusable module.

app/llm.py exposing one async function:

  async def call_json(system: str, user: str, schema: dict,
                      effort: str = "medium",
                      cache_system: bool = True) -> tuple[dict, Usage]

- Module-level anthropic.AsyncAnthropic() client, reused across calls.
- model="claude-opus-5", max_tokens=16000, thinking={"type":"adaptive"}
- output_config={"effort": effort, "format": {"type":"json_schema","schema":schema}}
- When cache_system: pass system as
  [{"type":"text","text":system,"cache_control":{"type":"ephemeral"}}]
  so the stable prefix is cached across the concurrent generator calls.
  Order matters — system is rendered before messages, so the volatile user
  content must stay in messages.
- Because output_config.format is set, the first text block is valid JSON —
  json.loads it and return the dict.
- Usage: a small dataclass of input_tokens, output_tokens, cache_read_tokens,
  latency_ms. Read cache_read_tokens from response.usage.cache_read_input_tokens
  — it is how we tell whether caching is actually working.
- Check response.stop_reason BEFORE reading content; raise a clear
  RefusalError if it is "refusal".
- Never pass temperature or budget_tokens (400 on this model).

VERIFY: a __main__ block that calls call_json with a trivial schema and prints
the result plus the Usage. Run it.
```

### M2 — Spec extractor

**Files:** `app/spec.py`

```
Read app/contracts.py, app/llm.py, app/diagram_types.py first.

app/spec.py:
  async def extract_spec(prompt: str,
                         prior: SystemSpec | None = None) -> tuple[SystemSpec, Usage]

- One call_json at effort="high". This is the one genuinely hard reasoning step
  in the pipeline; everything downstream is mechanical translation.
- JSON schema mirrors SystemSpec exactly, including suggested_diagram_types
  constrained by enum to the 14 DiagramType values. Ask the model to suggest
  the types the prompt actually warrants (e.g. mention of order statuses →
  state_machine; mention of multi-region deploy → deployment).
- System prompt: a software architect extracting a normalised model of the
  described system. Be explicit that entity and actor names must be reused
  verbatim by downstream diagram generators, so they must be consistent and
  specific.
- When `prior` is passed (a revision), include it and instruct the model to
  PATCH it — preserve existing names, add only what the revision introduces.
- Keep the system prompt stable and above ~1024 tokens of content so the cache
  breakpoint is useful; put the user prompt in messages, never in system.

VERIFY: __main__ block extracting a spec from the SEBI prompt; print it and
confirm suggested_diagram_types is non-empty.
```

### M3a — Mermaid prompt templates (7 types)

**Files:** `app/templates/mermaid.py`

```
Read app/contracts.py and app/diagram_types.py first.

app/templates/mermaid.py: MERMAID_TEMPLATES: dict[DiagramType, str] covering
exactly the 7 Mermaid-backed types: CLASS, SEQUENCE, STATE_MACHINE, ACTIVITY,
COMPONENT, USE_CASE, PACKAGE.

Each value is a system-prompt fragment that:
- Names the exact Mermaid opener to use (from CAPABILITY[t].dsl_keyword).
- States the syntax rules the model most often gets wrong for THAT diagram
  type. Be specific and concrete — this file is the main lever on first-attempt
  parse rate, which is our headline quality metric. Examples of the kind of
  rule that belongs here: in classDiagram, relationship arrows are `--|>`,
  `--*`, `--o` and member syntax is `+name: type`; in stateDiagram-v2 the
  initial transition is `[*] --> First`; in sequenceDiagram every participant
  must be declared before use and `par`/`alt` blocks must be closed with `end`.
- Includes ONE short worked example (5-10 lines) of valid DSL for that type.
- Forbids code fences, prose, and HTML labels in the output.

Also: get_template(t: DiagramType) -> str, raising on an unsupported type.

Keep each template tight — these are concatenated into a cached system prefix,
so bloat costs money on every single call.

VERIFY: `python -c "from app.templates.mermaid import MERMAID_TEMPLATES as m; print(sorted(k.value for k in m))"`
lists exactly the 7 expected types.
```

### M3b — PlantUML prompt templates (7 types)

**Files:** `app/templates/plantuml.py`

```
Read app/contracts.py, app/diagram_types.py, and app/templates/mermaid.py
(for structure and tone — match it) first.

app/templates/plantuml.py: PLANTUML_TEMPLATES: dict[DiagramType, str] covering
exactly the 7 PlantUML-backed types: OBJECT, COMPOSITE_STRUCTURE, DEPLOYMENT,
PROFILE, COMMUNICATION, INTERACTION_OVERVIEW, TIMING.

Same structure as the Mermaid templates: exact opener (@startuml/@enduml),
type-specific syntax rules the model commonly gets wrong, one short worked
example each, no fences or prose.

Type-specific notes worth encoding: object diagrams use
`object Name { field = value }`; deployment uses `node`, `database`,
`artifact` with nesting; timing uses `robust`/`concurrent` with `@0`/`@100`
time markers; profile uses `<<stereotype>>` on classes; interaction overview
nests `ref over` fragments.

Also: get_template(t: DiagramType) -> str, raising on an unsupported type.

VERIFY: `python -c "from app.templates.plantuml import PLANTUML_TEMPLATES as p; print(sorted(k.value for k in p))"`
lists exactly the 7 expected types.
```

### M4 — Validation service

**Files:** `app/validate.py`

```
Read app/contracts.py and poc/validate.py first. Promote the POC validator into
a service that handles both renderers.

app/validate.py:
  async def validate(renderer: Renderer, source: str) -> tuple[bool, str | None]

- MERMAID → shell out to validator/validate.mjs exactly as poc/validate.py does.
- PLANTUML → POST the source to KROKI_URL (/plantuml/svg) via httpx. HTTP 200
  means valid; a 400 response body carries the parser error. Timeout 10s;
  treat a timeout or connection failure as (False, "renderer unavailable: ...")
  rather than raising — Kroki being down must degrade only the 7 PlantUML types.

Also a deterministic semantic lint, run only after a successful parse:
  def lint(diagram_type: DiagramType, source: str) -> tuple[float, list[str]]
Returns a 0.0-1.0 score and a list of warnings. Checks:
  - at least 2 nodes/participants/classes (a one-box diagram is a failed
    generation, even though it parses)
  - no orphan nodes with zero edges
  - sequence diagrams: every message target is a declared participant
  - state machines: at least one `[*] -->` initial transition
Lint failures are warnings, never hard failures.

VERIFY: __main__ block running valid Mermaid, broken Mermaid, and valid
PlantUML through validate(); print all three outcomes.
```

### M5 — Generator with repair loop

**Files:** `app/generate.py`

```
Read app/contracts.py, app/llm.py, app/validate.py, app/diagram_types.py,
app/templates/mermaid.py, app/templates/plantuml.py first.

app/generate.py:
  async def generate_diagram(diagram_type: DiagramType, spec: SystemSpec,
                             prior_source: str | None = None
                             ) -> tuple[DiagramResult, list[Usage]]

Flow:
1. Pick the renderer and template from CAPABILITY / get_template.
2. call_json at effort="medium" with:
   - system = SHARED_RULES + the type template  (SHARED_RULES first and
     byte-identical across all types so the cache prefix hits)
   - user = the SystemSpec as sorted-key JSON. Sorted keys matter: unsorted
     dict ordering silently invalidates the prompt cache.
   - schema = {diagram_type, renderer, source, notes}
   - When prior_source is given, include it and instruct "apply the revision,
     preserve everything unrelated" — users hate wholesale layout churn on a
     minor edit.
3. Validate. On success, lint, and return status VALID.
4. On parse failure, repair: re-call at effort="low" passing the broken source
   AND the exact parser error. Max 3 attempts total.
   - success on attempt >1 → status REPAIRED, repair_attempts set
   - attempts exhausted → status FAILED with parse_error populated. Still
     return the last source so the user can hand-fix it in the UI; a broken
     diagram they can edit beats nothing, and their fix becomes training data.
5. Accumulate one Usage per LLM call and return them all.

VERIFY: __main__ block generating SEQUENCE and DEPLOYMENT from a hardcoded
spec. Print status and repair_attempts for each.
```

### M6 — Orchestrator and real SSE

**Files:** `app/orchestrator.py`, and replace the stub body of POST /v1/generate in `app/routes.py`

```
Read app/contracts.py, app/spec.py, app/generate.py, app/routes.py,
app/diagram_types.py first. Replace the fixture stub with the real pipeline.
Do not change any route signature or event shape — only handler bodies.

app/orchestrator.py:
  async def run_generation(req: GenerateRequest, user_id: UUID
                           ) -> AsyncIterator[str]   # yields SSE frames

1. Resolve diagram types: req.diagram_types, else DEFAULT_TYPES (replaced by
   spec.suggested_diagram_types once the spec comes back, if the request
   omitted them).
2. yield JobStarted.
3. await extract_spec(...) → yield SpecReady.
4. Fan out generate_diagram over all requested types CONCURRENTLY. Use
   asyncio.gather with an asyncio.Semaphore(6) cap. Yield each
   DiagramComplete / DiagramFailed as it finishes — use
   asyncio.as_completed or a queue, NOT gather-then-yield. Streaming the
   first diagram early is the entire latency strategy; batching defeats it.
5. yield JobComplete with counts and total latency_ms.
6. One diagram failing must NOT fail the request. 4-of-5 succeeding means the
   user gets 4 diagrams plus one explicit error.

Then rewire POST /v1/generate in app/routes.py to stream from run_generation.
Leave app/fixtures.py in place — M13 still uses it.

Persistence is NOT this step's job; M7 adds it. Take user_id as a parameter.

VERIFY: `curl -N -X POST localhost:8080/v1/generate -d
'{"prompt":"<the SEBI prompt>","diagram_types":["sequence","component","deployment"]}'`
→ real generated diagrams stream in, first one arriving well before the last.
Report the wall-clock time to first diagram_complete.
```

### M7 — Sessions, versioning, and Flow 2

**Files:** `app/sessions.py`, and the session/diagram read handlers in `app/routes.py`

```
Read app/contracts.py, app/models.py, app/db.py, app/routes.py first.

app/sessions.py — the persistence layer for conversation history. Append-only:
nothing is ever UPDATEd except head_version pointers.

  async def get_or_create_user(external_sub, email) -> User
  async def create_session(user_id, title) -> Session
  async def next_prompt_version(session_id, prompt, types, parent_version)
        -> PromptVersion            # version_no = head+1, parent_id wired
  async def save_spec(prompt_version_id, spec, usage) -> SystemSpecRow
  async def save_diagram_version(prompt_version_id, result, usages)
        -> DiagramVersion           # creates the Diagram row on first sight
                                    # of this (session, diagram_type)
  async def load_session(session_id, user_id) -> full history
  async def prior_source(session_id, diagram_type) -> str | None
        # feeds generate_diagram's prior_source for Flow 2
  async def carry_forward(session_id, new_prompt_version, requested_types)
        # diagram types NOT in the new request are copied to the new version
        # unchanged. Tweaking the sequence diagram must not delete the class
        # diagram.

Every read is scoped by user_id — a session belonging to another user must
return 404, not 403, and never leak content.

Then implement the real bodies for GET /v1/sessions, GET /v1/sessions/{id},
GET /v1/diagrams/{id}/versions. Wire save_* calls into app/orchestrator.py at
the obvious points (after spec, after each diagram).

VERIFY: generate twice in one session with a revised prompt the second time,
then `curl localhost:8080/v1/sessions/<id>` → two prompt versions, correct
parent_id linkage, and diagram versions at version_no 1 and 2.
```

### M8 — Auth stub

**Files:** `app/auth.py`, plus a dependency added to the routes in `app/routes.py`

```
Read app/routes.py and app/sessions.py first.

app/auth.py: a FastAPI dependency `current_user` that reads the X-User-Id
header, defaults to a fixed demo UUID when absent, and calls
get_or_create_user so a row always exists. Return the User.

Add this dependency to every /v1 route except /healthz and /v1/diagram-types.
user_id must come from the dependency ONLY — never from a request body field.
Accepting it from the body would be a trivial IDOR.

This is deliberately a stub. Real OIDC/JWT is out of scope for the MVP; the
seam is here so it can be swapped later without touching any handler.

VERIFY: `curl -H 'X-User-Id: <uuid>' localhost:8080/v1/sessions` returns that
user's sessions; omitting the header returns the demo user's.
```

### M9 — Feedback capture (Flow 3)

**Files:** `app/feedback.py`, and the real body of POST /v1/feedback in `app/routes.py`

```
Read app/contracts.py, app/models.py, app/db.py, app/routes.py first.

app/feedback.py:
  async def record(req: FeedbackRequest, user_id: UUID) -> UUID

- Verify the diagram_version exists and belongs to a session owned by user_id.
- Check the user's training_consent. Store the row regardless, but if consent
  is false set training_status='rejected' so the M10 export skips it. Consent
  filtering happens here and in the export query, not as an afterthought.
- INSERT a Feedback row with training_status='pending'. Append-only.
- Push the feedback id onto a Redis list/stream "feedback_events" as a
  best-effort buffer. If Redis is down, log and continue — Postgres is the
  system of record and M10 rebuilds from the pending rows.
- Return 202 immediately. Nothing about training is synchronous; a trainer
  outage must degrade nothing user-facing.

Then implement POST /v1/feedback to call it.

VERIFY: POST a RATING and an EDIT (with corrected_dsl) for a real
diagram_version_id, then confirm both rows are present with
training_status='pending'.
```

### M10 — Reward computation and ART export

**Files:** `app/training/reward.py`, `app/training/export.py`, `app/training/trainer_stub.py`

```
Read app/contracts.py, app/models.py, app/db.py first.

Three files. This is the ART boundary: we build everything up to the trainer's
input contract, and stop. No GPU, no real RL step.

app/training/reward.py:
  def compute(feedback_row, diagram_version_row) -> RewardBreakdown
- parse_success: 1.0 if status VALID, 0.5 if REPAIRED, 0.0 if FAILED
- lint_score: from the stored lint result if present, else 1.0
- human_rating: the rating normalised to 0..1, or None-handling that
  RENORMALISES the remaining weights rather than treating absent feedback as
  negative. Missing feedback is not negative feedback.
- edit_distance: normalised Levenshtein between source_dsl and corrected_dsl
  when corrected_dsl is present, else 0.0. Use difflib — do not add a dep.
- implicit_accept: 1.0 if implicit has did_export true, else 0.0
RewardBreakdown.total (already in contracts.py) combines them.

app/training/export.py:
  async def export_pending(out_path: str, min_samples: int = 1) -> int
- SELECT feedback WHERE training_status='pending', joined to its
  diagram_version → prompt_version → system_spec, so every trajectory is
  reconstructed EXACTLY from stored rows. Never re-derive inputs by calling
  the LLM again; that would train on a different prompt than the one that
  produced the output.
- Build a TrajectoryRecord per row, write JSONL to out_path.
- Mark exported rows training_status='exported' in the same transaction, so
  the cursor is idempotent and records are never double-consumed.
- Skip rows whose user has training_consent false.
- Return the count written.

app/training/trainer_stub.py:
- A CLI: reads a JSONL file, prints record count, mean total reward, reward
  distribution by diagram_type, and the count with human corrections.
- Then prints the exact LangChain ART handoff point as a comment block: this
  is where art.TrainableModel / the RL step would consume the trajectories.
  Implement no training.

VERIFY: `python -m app.training.export /tmp/traj.jsonl` writes a file, then
`python -m app.training.trainer_stub /tmp/traj.jsonl` prints the summary.
Re-running export writes 0 new records.
```

### M11 — Wire the UI to real APIs

**Files:** `static/index.html`

```
Read static/index.html, app/contracts.py, app/routes.py first. Extend the
existing page — do not rewrite it, and do not introduce any framework or
build step.

Add:
1. Session list in a left rail from GET /v1/sessions; clicking one loads it
   via GET /v1/sessions/{id} and rebuilds the tabs.
2. Send X-User-Id on every request (a value held in localStorage, generated
   once on first load).
3. Version switcher per diagram from GET /v1/diagrams/{id}/versions, with a
   simple two-pane before/after source comparison.
4. Feedback controls on each diagram tab: thumbs up/down (→ RATING), a comment
   box, and an "Export" button that fires an EXPORT signal with
   implicit {did_export: true}.
5. EDITABLE SOURCE — the highest-value feature on this list. The Source view
   textarea becomes editable; on change, re-render the Mermaid live, and on
   blur POST an EDIT signal with corrected_dsl. This is the strongest training
   signal the system can collect, which is why it is in the MVP at all.
6. PlantUML tabs render server-side: GET the Kroki SVG via the backend and
   inject it, instead of the "later step" placeholder note.
7. Show repair_attempts and any lint warnings as a small badge per tab.

VERIFY: in a browser, run all three flows — fresh generate, revised prompt
(confirm version switcher appears), and submit one of each feedback signal
type. Report what you observed for each.
```

### M12 — Caching

**Files:** `app/cache.py`, plus a lookup in `app/orchestrator.py`

```
Read app/contracts.py, app/orchestrator.py first.

app/cache.py:
  key(prompt, diagram_type, model_id, template_version) -> str
    # sha256 of the normalised (whitespace-collapsed, lowercased) prompt plus
    # the other three fields
  async def get(k) -> DiagramResult | None
  async def put(k, result, ttl=86400) -> None
Redis from REDIS_URL. Every Redis failure path must be caught and treated as a
miss — degrading to no caching is slower and costlier but still correct.

Wire into app/orchestrator.py: check the cache per diagram type before calling
generate_diagram; on hit, yield DiagramComplete immediately. Populate on
success. Never cache a FAILED result.

VERIFY: run the same generate request twice; the second returns in well under
a second. Report both wall-clock times.
```

### M13 — Demo seed

**Files:** `scripts/demo.py`

```
Read app/contracts.py, app/sessions.py, app/feedback.py first.

scripts/demo.py — one script that exercises all three flows against a running
server via httpx, printing a labelled trace of each. This is the thing you run
in front of a reviewer.

FLOW 1: new user → POST /v1/generate with the SEBI prompt and
["sequence","component","class","deployment"]. Print each SSE event as it
arrives with a wall-clock delta from request start, so time-to-first-diagram
is visible.

FLOW 2: same session → POST /v1/generate with session_id set and a revised
prompt ("also show the retry path when SEBI's circular feed is unavailable").
Print the new version numbers and confirm the untouched diagram types were
carried forward rather than dropped.

FLOW 3: POST /v1/feedback three times — a RATING, an EDIT with corrected_dsl,
and an EXPORT. Then run the M10 export and print the resulting JSONL summary,
closing the loop from user feedback to trainer-ready data.

Finish with a summary table: total latency per flow, diagrams generated,
repair attempts, cache hits, feedback rows exported.

VERIFY: `python scripts/demo.py` completes all three flows with no errors.
Paste the summary table in your final message.
```

---

## What is deliberately not in this plan

Per the "ignore non-functional requirements" instruction, these are designed in `HLD.md` but have no build step here: real OIDC/JWT auth (M8 is a stub with the right seam), retry/backoff policy, rate limiting, the metrics and alerting dashboards of HLD §12, load testing, actual ART GPU training (M10 stops at the trainer's input contract), and the HLD §10 failure-mode drills.

The two that are cheapest to add later and most likely to be asked about: real auth (swap the `current_user` dependency in `app/auth.py`, no handler changes) and metrics (the `Usage` dataclass from M1 already carries every token and latency number the dashboards need — only the emit call is missing).
