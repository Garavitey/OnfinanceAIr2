"""Deterministic offline stand-in for the Anthropic JSON calls.

There is no ``ANTHROPIC_API_KEY`` on the local machine, so ``app/llm.py``
branches on ``MOCK_LLM`` and delegates every structured-output call here. This
is a first-class code path, not a test fixture: the whole pipeline — spec
extraction, generation, validation, repair, persistence, streaming, feedback
and training export — runs end to end against it, and every canned ``source``
below genuinely parses in the real validators (``validator/validate.mjs`` for
Mermaid, ``vendor/plantuml.jar`` for PlantUML).

Dispatch is on the *schema's property names*, because that is the only
reliable signal of which pipeline stage is calling: the spec schema has
``entities``/``actors``, and both the generate and the repair call share the
diagram schema (``source``/``renderer``), so a repair is told apart by the
parser error carried in its user message.
"""

from __future__ import annotations

import re
from typing import Any

from app.contracts import DiagramType
from app.diagram_types import CAPABILITY, DEFAULT_TYPES

__all__ = ["MOCK_NOTE", "MOCK_DIAGRAMS", "mock_json"]

MOCK_NOTE = (
    "Generated in mock mode (MOCK_LLM=1): this is a deterministic canned "
    "response from app/mock_llm.py, not a live model call. The DSL is "
    "hand-written for the SEBI compliance-monitoring domain and is verified "
    "against the same parsers the real pipeline uses."
)

# ---------------------------------------------------------------------------
# Canned diagrams — one per DiagramType, all in the SEBI compliance-monitoring
# domain (circular ingestion, clause parsing, requirement extraction, gap
# analysis, impact reporting) so a mock-mode demo reads as one system.
#
# Mermaid sources open with CAPABILITY[t].dsl_keyword; PlantUML sources are
# wrapped in @startuml/@enduml. Each has >= 4 nodes and real edges, so none of
# them trips the semantic lint on diagram size.
# ---------------------------------------------------------------------------

MOCK_DIAGRAMS: dict[DiagramType, str] = {
    # --- Mermaid -----------------------------------------------------------
    DiagramType.CLASS: """\
classDiagram
    class Circular {
        +String circularId
        +String title
        +Date issuedOn
        +parse() ClauseTable
    }
    class Clause {
        +String clauseRef
        +String text
        +String category
        +extractRequirements()
    }
    class Requirement {
        +String requirementId
        +String obligation
        +Date dueDate
        +isApplicable() bool
    }
    class Control {
        +String controlId
        +String owner
        +float coverage
    }
    class Gap {
        +String gapId
        +String severity
        +String remediation
    }
    class ImpactReport {
        +String reportId
        +String itImpact
        +String opsImpact
        +publish()
    }
    Circular "1" --> "*" Clause : contains
    Clause "1" --> "*" Requirement : yields
    Requirement "1" --> "0..1" Control : mappedTo
    Requirement "1" --> "*" Gap : raises
    Gap "*" --> "1" ImpactReport : summarisedIn""",
    DiagramType.SEQUENCE: """\
sequenceDiagram
    actor CO as Compliance Officer
    participant ING as Circular Ingestion Service
    participant PARSER as Clause Parser
    participant EXT as Requirement Extractor
    participant GAP as Gap Analysis Engine
    participant RPT as Impact Report Service
    CO->>ING: Trigger SEBI circular sync
    ING->>ING: Poll SEBI portal for new circulars
    ING->>PARSER: Submit circular document
    PARSER-->>ING: Clause table
    ING->>EXT: Clauses for extraction
    EXT-->>GAP: Extracted compliance requirements
    GAP->>GAP: Compare against control register
    GAP-->>RPT: Identified gaps
    RPT-->>CO: IT and operational impact report""",
    DiagramType.STATE_MACHINE: """\
stateDiagram-v2
    [*] --> Discovered
    Discovered --> Parsing : ingestion job claims circular
    Parsing --> Parsed : clause table built
    Parsing --> ParseFailed : malformed document
    ParseFailed --> Parsing : retry with OCR fallback
    ParseFailed --> Quarantined : retry budget exhausted
    Parsed --> Extracting : requirement extractor starts
    Extracting --> GapAnalysed : requirements persisted
    GapAnalysed --> ImpactReported : gaps scored
    ImpactReported --> Acknowledged : compliance officer signs off
    Acknowledged --> [*]
    Quarantined --> [*]""",
    DiagramType.ACTIVITY: """\
flowchart TD
    START([Scheduled SEBI sync]) --> FETCH[Poll SEBI portal for circulars]
    FETCH --> NEW{New circular found?}
    NEW -- no --> DONE([End of cycle])
    NEW -- yes --> PARSE[Parse circular into clause table]
    PARSE --> OK{Parse succeeded?}
    OK -- no --> RETRY[Retry with OCR fallback]
    RETRY --> BUDGET{Retry budget left?}
    BUDGET -- yes --> PARSE
    BUDGET -- no --> QUAR[Quarantine for manual review]
    QUAR --> DONE
    OK -- yes --> EXTRACT[Extract compliance requirements]
    EXTRACT --> GAP[Run gap analysis against control register]
    GAP --> SCORE[Score IT and operational impact]
    SCORE --> REPORT[Publish impact report]
    REPORT --> DONE""",
    DiagramType.COMPONENT: """\
graph LR
    subgraph Ingestion
        FEED[SEBI Circular Feed Adapter]
        STORE[(Circular Document Store)]
    end
    subgraph Analysis
        PARSER[Clause Parser]
        EXTRACT[Requirement Extractor]
        GAP[Gap Analysis Engine]
    end
    subgraph Reporting
        IMPACT[Impact Report Service]
        NOTIFY[Notification Service]
    end
    REG[(Control Register)]
    CO[Compliance Officer Portal]
    FEED -->|raw circular| STORE
    STORE -->|document| PARSER
    PARSER -->|clause table| EXTRACT
    EXTRACT -->|requirements| GAP
    REG -->|existing controls| GAP
    GAP -->|gap set| IMPACT
    IMPACT -->|report ready| NOTIFY
    CO -->|sync request| FEED
    IMPACT -->|impact report| CO""",
    DiagramType.USE_CASE: """\
flowchart LR
    CO(["Compliance Officer"])
    ITO(["IT Owner"])
    AUD(["Internal Auditor"])
    SEBI(["SEBI Portal"])
    subgraph PLATFORM["Compliance Monitoring Platform"]
        UC1((Ingest SEBI circular))
        UC2((Parse clauses))
        UC3((Extract requirements))
        UC4((Run gap analysis))
        UC5((Review impact report))
        UC6((Export audit evidence))
    end
    SEBI --> UC1
    CO --> UC1
    CO --> UC4
    CO --> UC5
    ITO --> UC5
    AUD --> UC6
    UC1 --> UC2
    UC2 --> UC3
    UC3 --> UC4
    UC4 --> UC5""",
    DiagramType.PACKAGE: """\
graph TD
    subgraph PLATFORM["compliance platform"]
        INGESTION[app.ingestion]
        PARSING[app.parsing]
        ANALYSIS[app.analysis]
        REPORTING[app.reporting]
    end
    CONTRACTS[app.contracts]
    PERSISTENCE[app.persistence]
    INGESTION --> PARSING
    PARSING --> ANALYSIS
    ANALYSIS --> REPORTING
    INGESTION --> CONTRACTS
    PARSING --> CONTRACTS
    ANALYSIS --> CONTRACTS
    REPORTING --> CONTRACTS
    ANALYSIS --> PERSISTENCE
    REPORTING --> PERSISTENCE""",
    # --- PlantUML ----------------------------------------------------------
    DiagramType.OBJECT: """\
@startuml
object "circular2026x14 : Circular" as circular {
  circularId = "SEBI/HO/MIRSD/2026/14"
  title = "Cyber resilience for market intermediaries"
  status = "parsed"
}
object "clause42 : Clause" as clause {
  clauseRef = "4.2"
  category = "cyber-security"
}
object "req0041 : Requirement" as requirement {
  obligation = "Quarterly VAPT of trading systems"
  dueDate = "2026-06-30"
}
object "ctlSec07 : Control" as control {
  owner = "IT Security"
  coverage = 0.6
}
object "gap118 : Gap" as gap {
  severity = "high"
  remediation = "Extend VAPT scope to the OMS estate"
}
object "impact2026q1 : ImpactReport" as report {
  itImpact = "high"
  opsImpact = "medium"
}
circular --> clause : contains
clause --> requirement : yields
requirement --> control : mappedTo
requirement --> gap : raises
gap --> report : summarisedIn
@enduml""",
    DiagramType.COMPOSITE_STRUCTURE: """\
@startuml
component "Gap Analysis Engine" as GAE {
  portin "requirements in" as PIN
  portout "gaps out" as POUT
  [Requirement Loader] as LOADER
  [Control Register Client] as REGCLIENT
  [Coverage Matcher] as MATCHER
  [Severity Scorer] as SCORER
  PIN --> LOADER
  LOADER --> MATCHER : requirement set
  REGCLIENT --> MATCHER : control snapshot
  MATCHER --> SCORER : unmatched obligations
  SCORER --> POUT
}
database "Control Register" as REG
queue "requirements.extracted" as Q
Q --> PIN
REG --> REGCLIENT : read controls
POUT --> [Impact Report Service]
@enduml""",
    DiagramType.DEPLOYMENT: """\
@startuml
node "Compliance Analyst Workstation" as WS {
  artifact "Browser UI" as UI
}
cloud "SEBI Public Portal" as SEBI {
  artifact "Circular feed and PDFs" as FEED
}
node "AWS ap-south-1 Mumbai" as AWS {
  node "EKS cluster compliance-prod" as EKS {
    artifact "ingestion-service" as INGEST
    artifact "clause-parser" as PARSER
    artifact "gap-engine" as GAP
    artifact "impact-reporter" as RPT
  }
  database "RDS Postgres compliance" as DB
  storage "S3 bucket sebi-circulars" as S3
}
UI --> INGEST : HTTPS
FEED --> INGEST : HTTPS poll
INGEST --> S3 : store document
INGEST --> PARSER : AMQP
PARSER --> GAP : AMQP
GAP --> DB : JDBC
GAP --> RPT : AMQP
RPT --> DB : JDBC
RPT --> UI : HTTPS report
@enduml""",
    DiagramType.PROFILE: """\
@startuml
package "UML Metamodel" <<Frame>> {
  class Classifier <<metaclass>>
  class Activity <<metaclass>>
  class Artifact <<metaclass>>
}
package "SEBI Compliance Profile" <<Frame>> {
  class RegulatedEntity <<stereotype>> {
    sebiRegistrationNo : String
    entityCategory : String
  }
  class ComplianceRequirement <<stereotype>> {
    circularRef : String
    dueDate : Date
    severity : Severity
  }
  class AuditedProcess <<stereotype>> {
    retentionYears : Integer
    evidenceStore : String
  }
  class EvidenceArtifact <<stereotype>> {
    immutable : Boolean
    hashAlgorithm : String
  }
  enum Severity <<enumeration>> {
    LOW
    MEDIUM
    HIGH
  }
}
RegulatedEntity ..> Classifier : extension
ComplianceRequirement ..> Classifier : extension
AuditedProcess ..> Activity : extension
EvidenceArtifact ..> Artifact : extension
ComplianceRequirement --> Severity : severity
@enduml""",
    DiagramType.COMMUNICATION: """\
@startuml
object "officer : ComplianceOfficer" as CO
object "ingestion : CircularIngestionService" as ING
object "parser : ClauseParser" as PAR
object "extractor : RequirementExtractor" as EXT
object "engine : GapAnalysisEngine" as GAP
object "reporter : ImpactReportService" as RPT
CO --> ING : "1: syncCirculars()"
ING --> PAR : "2: parse(circular)"
PAR --> ING : "2.1: clauseTable"
ING --> EXT : "3: extract(clauseTable)"
EXT --> GAP : "4: analyse(requirements)"
GAP --> RPT : "5: report(gaps)"
RPT --> CO : "6: impactReport"
@enduml""",
    DiagramType.INTERACTION_OVERVIEW: """\
@startuml
start
:interaction Ingest SEBI circular;
if (new clauses found?) then (yes)
  :interaction Parse circular into clauses;
  fork
    :interaction Extract compliance requirements;
  fork again
    :interaction Refresh control register snapshot;
  end fork
  :interaction Run gap analysis;
  if (gaps found?) then (yes)
    :interaction Publish IT and operational impact report;
    :interaction Notify compliance officer;
  else (no)
    :interaction Log clean assessment;
  endif
else (no)
  :interaction Skip this sync cycle;
endif
stop
@enduml""",
    DiagramType.TIMING: """\
@startuml
robust "Circular" as C
concise "Gap Analysis Engine" as G
binary "SEBI Feed Poll" as F
C has Discovered,Parsing,Parsed,Analysed,Reported
@0
C is Discovered
G is Idle
F is low
@2
F is high
C is Parsing
@5
F is low
C is Parsed
G is Loading
@7
C is Parsed
G is Matching
@9
C is Analysed
G is Scoring
@12
C is Reported
G is Idle
@enduml""",
}

assert set(MOCK_DIAGRAMS) == set(DiagramType), (
    "MOCK_DIAGRAMS must cover every DiagramType member: "
    f"missing={set(DiagramType) - set(MOCK_DIAGRAMS)}, "
    f"extra={set(MOCK_DIAGRAMS) - set(DiagramType)}"
)

# ---------------------------------------------------------------------------
# Canned SystemSpec
# ---------------------------------------------------------------------------

MOCK_SPEC: dict[str, Any] = {
    "entities": [
        "SEBI Circular",
        "Clause",
        "Compliance Requirement",
        "Control",
        "Gap",
        "Impact Report",
    ],
    "actors": [
        "Compliance Officer",
        "IT Owner",
        "Internal Auditor",
        "SEBI Portal",
    ],
    "services": [
        "Circular Ingestion Service",
        "Clause Parser",
        "Requirement Extractor",
        "Gap Analysis Engine",
        "Impact Report Service",
        "Control Register",
    ],
    "flows": [
        "Compliance Officer -> Circular Ingestion Service: trigger SEBI circular sync",
        "Circular Ingestion Service -> SEBI Portal: poll for new circulars",
        "Circular Ingestion Service -> Clause Parser: parse circular into a clause table",
        "Clause Parser -> Requirement Extractor: hand over parsed clauses",
        "Requirement Extractor -> Gap Analysis Engine: submit extracted requirements",
        "Gap Analysis Engine -> Control Register: compare against existing controls",
        "Gap Analysis Engine -> Impact Report Service: publish identified gaps",
        "Impact Report Service -> Compliance Officer: deliver IT and operational impact report",
    ],
    "states": [
        "Discovered",
        "Parsing",
        "Parsed",
        "Extracting",
        "GapAnalysed",
        "ImpactReported",
        "Acknowledged",
        "Quarantined",
    ],
    "deployment_nodes": [
        "Compliance Analyst Workstation",
        "EKS cluster compliance-prod",
        "RDS Postgres compliance",
        "S3 bucket sebi-circulars",
    ],
}

# Keyword -> extra diagram type the prompt implies. Checked against the lowered
# user text so mock mode still demonstrates "infer the types from the prompt".
_TYPE_HINTS: list[tuple[tuple[str, ...], DiagramType]] = [
    (("status", "state", "lifecycle"), DiagramType.STATE_MACHINE),
    (("deploy", "region", "infra"), DiagramType.DEPLOYMENT),
    (("retry", "failure", "error"), DiagramType.ACTIVITY),
]

# Markers that mean "this is a repair call, not a fresh generation". The repair
# prompt carries the parser's error text and the source that failed.
_REPAIR_MARKERS = (
    "parser error",
    "parse error",
    "failed to parse",
    "broken source",
    "broken mermaid",
    "broken plantuml",
    "syntax error",
    "repair",
)

# Longest value first, so "composite_structure" is never shadowed by a
# substring match and "state_machine" wins over a bare "state".
_TYPES_BY_SPECIFICITY: list[DiagramType] = sorted(
    DiagramType, key=lambda t: len(t.value), reverse=True
)


def mock_json(system: str, user: str, schema: dict) -> dict:
    """Return a deterministic dict satisfying `schema`, with no network call.

    `system` and `user` are the prompts the real call would have sent; they are
    only read for keyword signals (which diagram type, which stage). Dispatch
    is on `schema`'s property names, which is the one thing every caller sets
    unambiguously.
    """
    properties: dict[str, Any] = schema.get("properties") or {}

    if "entities" in properties and "actors" in properties:
        return _mock_spec(user, properties)

    if "source" in properties and "renderer" in properties:
        diagram_type = _detect_diagram_type(system, user)
        # Repair first: a repair reuses the diagram schema, and must return the
        # known-good source rather than falling through to a fresh generation.
        if _is_repair(user):
            return _mock_diagram(diagram_type, repaired=True)
        return _mock_diagram(diagram_type, repaired=False)

    return {name: _minimal_value(spec) for name, spec in properties.items()}


# ---------------------------------------------------------------------------
# Stage builders
# ---------------------------------------------------------------------------


def _mock_spec(user: str, properties: dict[str, Any]) -> dict:
    """Canned SEBI-compliance spec, trimmed to the schema's own properties."""
    spec = {name: value for name, value in MOCK_SPEC.items() if name in properties}

    if "suggested_diagram_types" in properties:
        spec["suggested_diagram_types"] = [t.value for t in _suggest_types(user)]

    # An unexpected extra property still gets a schema-valid value.
    for name, prop in properties.items():
        if name not in spec:
            spec[name] = _minimal_value(prop)

    return spec


def _suggest_types(user: str) -> list[DiagramType]:
    """Default diagram set plus whatever the prompt's keywords imply."""
    suggested = list(DEFAULT_TYPES)
    lowered = user.lower()
    for keywords, diagram_type in _TYPE_HINTS:
        if diagram_type not in suggested and any(k in lowered for k in keywords):
            suggested.append(diagram_type)
    return suggested


def _mock_diagram(diagram_type: DiagramType, *, repaired: bool) -> dict:
    """The canned diagram payload for one type, shaped like the real call."""
    note = MOCK_NOTE
    if repaired:
        note = (
            "Replaced the unparseable source with the canned-correct "
            f"{diagram_type.value} diagram. {MOCK_NOTE}"
        )
    return {
        "diagram_type": diagram_type.value,
        "renderer": CAPABILITY[diagram_type].renderer.value,
        "source": MOCK_DIAGRAMS[diagram_type],
        "notes": note,
    }


# ---------------------------------------------------------------------------
# Prompt sniffing
# ---------------------------------------------------------------------------


def _is_repair(user: str) -> bool:
    """True when the user text carries a parser error and a broken source."""
    lowered = user.lower()
    return any(marker in lowered for marker in _REPAIR_MARKERS)


def _detect_diagram_type(system: str, user: str) -> DiagramType:
    """Find the requested type by scanning the prompts for its wire value.

    The per-type instruction lives in the user message, so that is checked
    before the system prompt. Within each, explicit forms win over a bare
    mention, because a generate call pastes the whole spec JSON into the user
    message — an unordered scan would read the `deployment_nodes` key or a
    `suggested_diagram_types` entry as the request. Defaults to SEQUENCE,
    the most broadly useful diagram, if no type is named at all.
    """
    for text in (user, system):
        lowered = text.lower()
        for probe in (_explicit_field, _named_diagram, _bare_mention):
            found = probe(lowered)
            if found is not None:
                return found
    return DiagramType.SEQUENCE


def _explicit_field(lowered: str) -> DiagramType | None:
    """Match `diagram_type: sequence` / `"diagram type" = "sequence"`."""
    match = re.search(r"diagram[ _]?type[\"']?\s*[:=]\s*[\"']?([a-z_]+)", lowered)
    if match is None:
        return None
    try:
        return DiagramType(match.group(1))
    except ValueError:
        return None


def _named_diagram(lowered: str) -> DiagramType | None:
    """Match the prose form: "draw a UML state machine diagram"."""
    for diagram_type in _TYPES_BY_SPECIFICITY:
        for name in _spellings(diagram_type):
            if f"{name} diagram" in lowered:
                return diagram_type
    return None


def _bare_mention(lowered: str) -> DiagramType | None:
    """Match the type value anywhere, as a whole word.

    Word boundaries keep `deployment_nodes` from reading as `deployment`,
    since `_` counts as a word character.
    """
    for diagram_type in _TYPES_BY_SPECIFICITY:
        for name in _spellings(diagram_type):
            if re.search(rf"\b{re.escape(name)}\b", lowered):
                return diagram_type
    return None


def _spellings(diagram_type: DiagramType) -> tuple[str, ...]:
    """The wire value plus its spaced spelling ("use_case" / "use case")."""
    spaced = diagram_type.value.replace("_", " ")
    if spaced == diagram_type.value:
        return (diagram_type.value,)
    return (diagram_type.value, spaced)


# ---------------------------------------------------------------------------
# Fallback for schemas this module does not recognise
# ---------------------------------------------------------------------------

_MINIMAL_BY_TYPE: dict[str, Any] = {
    "string": "",
    "number": 0,
    "integer": 0,
    "boolean": False,
    "array": [],
    "object": {},
    "null": None,
}


def _minimal_value(prop: Any) -> Any:
    """Smallest value satisfying one JSON-schema property declaration."""
    if not isinstance(prop, dict):
        return None

    enum = prop.get("enum")
    if enum:
        return enum[0]

    declared = prop.get("type")
    if isinstance(declared, list):
        declared = declared[0] if declared else "null"

    if declared == "object":
        nested = prop.get("properties") or {}
        return {name: _minimal_value(spec) for name, spec in nested.items()}

    return _MINIMAL_BY_TYPE.get(declared, None)
