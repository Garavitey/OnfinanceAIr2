"""Mermaid system-prompt fragments, one per Mermaid-backed diagram type.

These fragments are concatenated into a cached system prefix on every generator
call, so they are deliberately terse: only the opener, the syntax rules this
specific diagram type gets wrong most often, and one short worked example.
Every embedded example is verified to parse with ``node validator/validate.mjs``.

Two of the seven types are approximations — Mermaid has no native use-case or
package diagram (see `CAPABILITY[t].approximated`), so those fragments say
which flowchart shapes stand in for the UML notation instead of pretending a
dedicated syntax exists.
"""

from __future__ import annotations

from app.contracts import DiagramType, Renderer
from app.diagram_types import CAPABILITY

__all__ = ["MERMAID_TEMPLATES", "get_template"]


# Appended to every fragment. Keep it to a few lines — it is paid for once per
# diagram type in the cached prefix.
_COMMON = """
Output rules:
- Emit raw Mermaid only: the first line is the opener named above.
- No markdown code fences, no ```mermaid, no prose, no commentary, no headings
  before or after the source.
- No HTML in labels: no <br>, no <b>, no &nbsp;. Keep labels to plain text and
  quote any label containing a space, parenthesis, comma or colon.
- If you must annotate, use a Mermaid comment line starting with `%%`. Never
  leave a bare sentence in the output.
"""


_BODIES: dict[DiagramType, str] = {
    DiagramType.CLASS: """\
Produce a UML class diagram (static structure) in Mermaid.
Open with `classDiagram` on its own first line.
Syntax rules:
- Members go inside `class Name { ... }`, one per line, with a visibility
  prefix: `+` public, `-` private, `#` protected. Attributes are
  `+name: Type`; methods carry parentheses: `+charge(amount: Money) Receipt`.
  The return type follows the parentheses with a space — never `-> Type`.
- Relationship arrows are two characters of line plus a head:
  `--|>` inheritance, `..|>` realization, `--*` composition, `--o` aggregation,
  `-->` directed association, `--` plain association, `..>` dependency.
  Write them between class names: `Order --* OrderLine`. Never use `->`, `=>`
  or UML-ish `<|-` with one dash.
- Cardinality is quoted on each side: `Order "1" --* "0..*" OrderLine`.
  A relationship label follows a colon: `Order --> Customer : placed by`.
- Class names are single tokens in PascalCase. Generics use the tilde form,
  `List~Clause~`, not angle brackets.
- Declare a class before you reference it in a relationship.
Example:
classDiagram
    class Order {
        +String orderId
        +Money total
        +submit() Receipt
    }
    class OrderLine {
        +int quantity
    }
    class Customer {
        +String name
    }
    Order "1" --* "1..*" OrderLine
    Customer "1" --> "0..*" Order : places
""",
    DiagramType.SEQUENCE: """\
Produce a UML sequence diagram (messages ordered in time) in Mermaid.
Open with `sequenceDiagram` on its own first line.
Syntax rules:
- Declare every lifeline first, one per line: `participant API` or
  `actor Officer` for a human. A name used in a message but never declared is
  the single most common failure here.
- Use an alias when the label has spaces: `participant GW as Payment Gateway`.
  Messages then reference the alias (`GW`), never the display label.
- Arrows: `->>` solid call, `-->>` dashed reply, `-x` lost message. Every
  message needs a colon and a label: `Officer->>API: submitOrder()`.
  Bare `->` is legal but reads as a line without an arrowhead — prefer `->>`.
- Fragments (`alt`, `else`, `opt`, `loop`, `par`, `and`, `critical`) each open a
  block that MUST be closed by its own `end` line. Count your `end`s: one per
  `alt`/`opt`/`loop`/`par`, and `else`/`and` are continuations, not openers.
- `activate X` requires a matching `deactivate X`; prefer the `+`/`-` suffix
  form (`API->>+DB: query`) or omit activation entirely.
- Notes are `Note over A,B: text` — never a bare sentence on its own line.
Example:
sequenceDiagram
    actor Officer
    participant API
    participant DB
    Officer->>API: submitOrder()
    API->>DB: insertOrder()
    DB-->>API: orderId
    alt accepted
        API-->>Officer: confirmation
    else rejected
        API-->>Officer: validation errors
    end
""",
    DiagramType.STATE_MACHINE: """\
Produce a UML state machine diagram in Mermaid.
Open with `stateDiagram-v2` on its own first line (the `-v2` suffix is
required; plain `stateDiagram` is the deprecated v1 grammar).
Syntax rules:
- The initial transition comes from the pseudo-state `[*]`:
  `[*] --> Discovered`. A terminal transition goes back to it:
  `Acknowledged --> [*]`. `[*]` is never declared with `state`.
- Transitions use `-->` with the trigger after a colon:
  `Parsing --> Parsed : parseSucceeded`. Never `->`.
- State names are single tokens in PascalCase. For a display label use
  `state "Awaiting approval" as AwaitingApproval` and then reference the alias.
- Composite states nest with braces: `state Active { ... }`, and each nested
  region needs its own `[*] -->` entry point.
- Concurrency inside a composite state is separated by a line of exactly `--`.
- Choice/fork pseudo-states must be declared before use:
  `state c1 <<choice>>`, `state f1 <<fork>>`.
Example:
stateDiagram-v2
    [*] --> Discovered
    Discovered --> Parsing : syncTriggered
    Parsing --> Parsed : parseSucceeded
    Parsing --> Quarantined : parseRejected
    Parsed --> Acknowledged : officerSignedOff
    Acknowledged --> [*]
""",
    DiagramType.ACTIVITY: """\
Produce a UML activity diagram (control flow of a process) in Mermaid.
Mermaid has no native activity diagram, so model it as a flowchart: open with
`flowchart TD` on its own first line.
Syntax rules:
- Every node is `id[shape text]`, where `id` is a single token reused in edges.
  Use `([Start])` for the initial node, `([End])` for the final node,
  `[Action]` rectangles for actions, and `{Decision?}` diamonds for branches.
- Edges are `-->`, with a guard label in pipes after the arrow:
  `D -->|yes| A3`. The `|...|` form is the reliable one — prefer it over
  `-- yes -->`. Never `->`.
- A decision node needs at least two outgoing guarded edges, and every guard
  must be mutually exclusive and labelled.
- Quote any label containing a parenthesis, comma, colon or the word `end`:
  `A1["Fetch circular (v2)"]`. A bare lowercase `end` as a node id breaks the
  parser.
- Parallelism: fan out from one node to several, then fan them back into a
  single join node. Do not invent `fork`/`join` keywords.
- Declare each node the first time it appears in an edge; do not reuse one id
  with two different labels.
Example:
flowchart TD
    S([Start]) --> A1[Poll circular feed]
    A1 --> D{Feed reachable?}
    D -->|yes| A2[Download circulars]
    D -->|no| A3[Schedule retry]
    A3 --> A1
    A2 --> A4[Parse into clauses]
    A4 --> E([End])
""",
    DiagramType.COMPONENT: """\
Produce a UML component diagram (components wired through interfaces) in
Mermaid. Open with `graph LR` on its own first line.
Syntax rules:
- One node per component: `id[Component Name]`. Ids are single tokens;
  the bracketed text is the display label. Reuse the id in every edge.
- Show provided/required interfaces as the edge label, in pipes:
  `UI -->|HTTP /orders| API`. That label is where the interface name belongs —
  do not invent lollipop or `-()-` notation, which Mermaid has no syntax for.
- Use `subgraph <id>[<label>]` ... `end` to group components into a boundary
  (a system, a tier, a bounded context). Every `subgraph` needs its own `end`.
- Datastores and external systems are nodes too — mark them with shape, not
  prose: `[(Postgres)]` for a database, `{{Queue}}` for a broker,
  `[/External SEBI portal/]` for an outside party.
- Quote labels containing a parenthesis, comma or colon: `API["API (v2)"]`.
- Edges are `-->` or `-.->` for an asynchronous/optional dependency. Never `->`.
Example:
graph LR
    subgraph platform[Compliance Platform]
        UI[Officer Console]
        API[Compliance API]
        ING[Ingestion Worker]
    end
    DB[(Postgres)]
    SEBI[/SEBI Portal/]
    UI -->|HTTPS| API
    API -->|SQL| DB
    ING -.->|poll RSS| SEBI
    ING -->|enqueue clauses| API
""",
    DiagramType.USE_CASE: """\
Produce a UML use case diagram in Mermaid.
Mermaid has no native use case diagram, so approximate it with a left-to-right
flowchart: open with `flowchart LR` on its own first line.
Syntax rules:
- Actors are stadium nodes: `OFF([Compliance Officer])`. Use cases are rounded
  rectangles: `UC1(Review gap report)`. Keep that shape split — it is the only
  thing distinguishing an actor from a use case here.
- An actor's association to a use case is a plain undirected edge: `OFF --- UC1`.
  Use `---`, not `-->`; a use case association has no direction.
- `<<include>>` / `<<extend>>` relationships are dashed labelled edges between
  use cases: `UC1 -.->|include| UC2`. Write the stereotype as the edge label,
  in pipes, without angle brackets.
- Wrap the use cases (not the actors) in `subgraph sys[System Name]` ... `end`
  to draw the system boundary. The subgraph needs a matching `end`.
- Secondary/external actors go on the right-hand side of the boundary; they are
  still stadium nodes.
- Quote labels containing a parenthesis, comma or colon.
Example:
flowchart LR
    OFF([Compliance Officer])
    AUD([Internal Auditor])
    subgraph sys[Compliance Platform]
        UC1(Sync SEBI circulars)
        UC2(Review extracted requirements)
        UC3(Export impact report)
    end
    OFF --- UC1
    OFF --- UC2
    AUD --- UC3
    UC2 -.->|include| UC3
""",
    DiagramType.PACKAGE: """\
Produce a UML package diagram (modules and their dependencies) in Mermaid.
Mermaid has no native package diagram, so approximate it with a top-down graph:
open with `graph TD` on its own first line.
Syntax rules:
- One node per package, labelled with its dotted or slashed path:
  `ING[compliance.ingestion]`. Ids are single tokens; the label carries the
  path. Quote a label containing a parenthesis, comma or colon.
- Dependencies point from the dependent package to the one it imports, as a
  dashed edge labelled with the stereotype:
  `ING -.->|import| DOM`. Dashed is the UML dependency notation — use `-.->`
  for imports/access and reserve solid `-->` for containment.
- Nesting (a package inside a package) is `subgraph <id>[<label>]` ... `end`,
  one `end` per `subgraph`. Do not draw a containment edge AND nest the same
  pair; pick one.
- Keep the dependency graph acyclic: if two packages would point at each other,
  introduce a shared third package instead. A cycle here is a modelling bug,
  not a syntax one, and reviewers look for it.
- Do not add classes, methods or interfaces — packages only.
Example:
graph TD
    subgraph app[compliance.app]
        API[compliance.app.api]
        WRK[compliance.app.workers]
    end
    DOM[compliance.domain]
    INF[compliance.infra]
    API -.->|import| DOM
    WRK -.->|import| DOM
    DOM -.->|import| INF
""",
}


MERMAID_TEMPLATES: dict[DiagramType, str] = {
    t: body + _COMMON for t, body in _BODIES.items()
}

_EXPECTED = {t for t, cap in CAPABILITY.items() if cap.renderer is Renderer.MERMAID}

assert set(MERMAID_TEMPLATES) == _EXPECTED, (
    "MERMAID_TEMPLATES must cover every Mermaid-backed DiagramType: "
    f"missing={_EXPECTED - set(MERMAID_TEMPLATES)}, "
    f"extra={set(MERMAID_TEMPLATES) - _EXPECTED}"
)

# The opener named in each fragment must be the one the capability matrix
# advertises, or the generated source will not match what the UI dispatches on.
for _t, _body in _BODIES.items():
    assert f"`{CAPABILITY[_t].dsl_keyword}`" in _body, (
        f"template for {_t.value!r} must name its opener "
        f"{CAPABILITY[_t].dsl_keyword!r} in backticks"
    )


def get_template(t: DiagramType) -> str:
    """Return the Mermaid system-prompt fragment for diagram type `t`.

    Raises `ValueError` if `t` is not one of the Mermaid-backed types.
    """
    try:
        return MERMAID_TEMPLATES[t]
    except KeyError:
        raise ValueError(
            f"No Mermaid template for diagram type {t.value!r}: it is rendered by "
            f"{CAPABILITY[t].renderer.value if t in CAPABILITY else 'an unknown renderer'}. "
            f"Supported here: {sorted(x.value for x in MERMAID_TEMPLATES)}."
        ) from None
