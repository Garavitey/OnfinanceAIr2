"""PlantUML system-prompt fragments, one per PlantUML-backed diagram type.

These fragments are concatenated into a cached system prefix on every generator
call, so they are deliberately terse: only the opener/closer, the syntax rules
this specific diagram type gets wrong most often, and one short worked example.
Every embedded example is verified to render with
``java -jar vendor/plantuml.jar -tsvg -pipe -failfast2``.
"""

from __future__ import annotations

from app.contracts import DiagramType, Renderer
from app.diagram_types import CAPABILITY

__all__ = ["PLANTUML_TEMPLATES", "get_template"]


# Appended to every fragment. Keep it to a few lines — it is paid for once per
# diagram type in the cached prefix.
_COMMON = """
Output rules:
- Emit raw PlantUML only: first line `@startuml`, last line `@enduml`.
- No markdown code fences, no ```plantuml, no prose, no commentary, no headings
  before or after the source.
- If you must annotate, use a PlantUML comment line starting with `'`. Never
  leave a bare sentence in the output.
"""


_BODIES: dict[DiagramType, str] = {
    DiagramType.OBJECT: """\
Produce a UML object diagram (a runtime snapshot of instances) in PlantUML.
Open with `@startuml`, close with `@enduml`.
Syntax rules:
- Declare every instance as `object <name> { ... }`. Slots inside the braces are
  assignments — `field = value`, never `field: Type` and never `+field`.
- Quote string values (`status = "paid"`); leave numbers and booleans unquoted.
- Instance names must be single tokens (`order1`, `customer7`). For a display
  label use `object "Order #1042" as order1`.
- Links between instances are plain associations: `a --> b : label`. Do not use
  class-diagram arrowheads (`<|--`, `*--`, `o--`) — objects are not classified.
- Do not declare `class`; every node is an `object` (or a `map` for key/value
  data). Omit methods entirely.
Example:
@startuml
object order1 {
  id = 1042
  status = "paid"
}
object customer7 {
  name = "Ada Lovelace"
}
customer7 --> order1 : places
@enduml
""",
    DiagramType.COMPOSITE_STRUCTURE: """\
Produce a UML composite structure diagram (the internals of ONE classifier) in
PlantUML. Open with `@startuml`, close with `@enduml`.
Syntax rules:
- Emit exactly one outer container: `component "<Classifier>" as <alias> { ... }`.
  Everything else nests inside its braces. Two top-level containers is the most
  common mistake — a composite structure has a single owner.
- Internal parts are nested `component` declarations; boundary points are
  `port "<name>" as <alias>` (or `portin` / `portout`) declared inside the same
  braces as the parts.
- Connectors are drawn between the nested aliases (`http --> AU`), inside the
  container body. Declare a part before you reference it.
- Aliases are single tokens; quote any multi-word label and bind it with `as`.
- Do not use `node`, `database`, or `package` here, and do not add `interface`
  lollipops — use `port` for the boundary.
Example:
@startuml
component "PaymentService" as PS {
  port "http" as http
  component "Authorizer" as AU
  component "LedgerWriter" as LW
  http --> AU
  AU --> LW
}
@enduml
""",
    DiagramType.DEPLOYMENT: """\
Produce a UML deployment diagram in PlantUML. Open with `@startuml`, close with
`@enduml`.
Syntax rules:
- Physical/virtual hosts are `node "<Name>" as <alias> { ... }`. Nest what is
  deployed on a host inside its `{ }` block.
- Deployed units are `artifact` (jars, images, bundles); stores are `database`;
  group related hosts with `node` nesting. Use `cloud`/`queue` only for genuine
  infrastructure boundaries.
- Every element needs an `as <alias>` single-token alias if its label is quoted,
  and relationships must reference the alias, never the quoted label.
- Communication paths go between aliases and carry a protocol label:
  `api --> pg : SQL`. Keep them outside the `{ }` blocks when they cross hosts.
- Do not use `component`-diagram `[brackets]` or `interface` here.
Example:
@startuml
node "WebTier" as web {
  artifact "api.jar" as api
}
node "DataTier" as data {
  database "Postgres" as pg
}
api --> pg : SQL
@enduml
""",
    DiagramType.PROFILE: """\
Produce a UML profile diagram (stereotypes extending the metamodel) in PlantUML.
Open with `@startuml`, close with `@enduml`.
Syntax rules:
- A stereotype is applied by writing it after the classifier name:
  `class Order <<Entity>> {`. The `<<...>>` goes on the declaration line, never
  on its own line and never inside the braces.
- Use `<< >>` (double angle brackets), not `«»` and not `@Stereotype`.
- Tagged values are modelled as class members: `+id: UUID` inside the braces —
  here the `name: Type` form is correct (unlike object diagrams).
- Extension/dependency between stereotyped classes uses ordinary class-diagram
  arrows: `-->` for dependency, `<|--` for extends.
- Keep stereotype names single tokens in PascalCase and reuse them consistently.
Example:
@startuml
class Order <<Entity>> {
  +id: UUID
}
class PaymentGateway <<Service>> {
  +charge()
}
Order --> PaymentGateway : uses
@enduml
""",
    DiagramType.COMMUNICATION: """\
Produce a UML communication diagram in PlantUML. Open with `@startuml`, close
with `@enduml`.
Syntax rules:
- PlantUML has no native communication diagram: write it as a sequence diagram
  whose messages carry explicit sequence numbers. The numbering IS the diagram.
- Declare collaborators first with `participant <Name>` (one per line), then the
  messages.
- Every message label starts with its number: `Browser -> API : 1. submitOrder()`.
  Use dotted nesting for nested calls (`1.1.`, `1.2.`). Numbers must be unique
  and monotonically ordered.
- Use `->` for a call and `-->` for the reply. Do not add `activate`,
  `deactivate`, lifeline boxes, `autonumber`, or `@0`-style time markers — a
  communication diagram is link-oriented, not a timeline.
- Do not use `object`/`node` declarations.
Example:
@startuml
participant Browser
participant API
participant DB
Browser -> API : 1. submitOrder()
API -> DB : 1.1. insertOrder()
DB --> API : 1.2. orderId
API --> Browser : 2. confirmation
@enduml
""",
    DiagramType.INTERACTION_OVERVIEW: """\
Produce a UML interaction overview diagram in PlantUML. Open with `@startuml`,
close with `@enduml`.
Syntax rules:
- Build it as a SEQUENCE diagram skeleton, not an activity diagram. `ref over`
  is only legal in a sequence diagram — `start`/`stop`/`:action;` will make the
  parser assume an activity diagram and fail.
- Each referenced sub-interaction is one line: `ref over A, B : <name>`, where
  `A`/`B` are declared participants.
- Control flow comes from sequence fragments wrapping those refs:
  `alt <cond>` / `else <cond>` / `end`, `opt`, `loop`, `par`. Every opened
  fragment needs a matching `end`.
- Declare all participants with `participant <Name>` before the first `ref`.
- An optional `mainframe <title>` line may frame the overview. Keep concrete
  messages out — the body is refs plus control flow only.
Example:
@startuml
mainframe **sd** Checkout overview
participant Browser
participant API
participant DB
ref over Browser, API : Login sequence
alt authenticated
  ref over API, DB : Place order sequence
else rejected
  ref over Browser : Show error page
end
@enduml
""",
    DiagramType.TIMING: """\
Produce a UML timing diagram in PlantUML. Open with `@startuml`, close with
`@enduml`.
Syntax rules:
- Declare every timeline BEFORE any time marker, as
  `robust "<Label>" as <alias>` (discrete named states),
  `concise "<Label>" as <alias>` (compact state bar),
  `binary "<Label>" as <alias>`, or `analog "<Label>" as <alias>`.
  The order is label-then-alias: `robust "Session" as S`, never
  `robust S as "Session"`.
- `concurrent` is NOT a timing keyword — use `concise`. A clock is
  `clock <alias> with period <n>`.
- Time markers are their own lines: `@0`, `@100`, `@200` — absolute numbers with
  no units, strictly increasing.
- State changes use `is`: `S is Idle`. State names are single tokens (quote them
  if they contain spaces). Never use `->` or `:` for a state change.
- Under each `@` marker list only the timelines that change at that instant.
Example:
@startuml
clock clk with period 50
robust "Session" as S
concise "User" as U
@0
S is Idle
U is Away
@100
S is Active
U is Typing
@200
S is Idle
@enduml
""",
}


PLANTUML_TEMPLATES: dict[DiagramType, str] = {
    t: body + _COMMON for t, body in _BODIES.items()
}

_EXPECTED = {t for t, cap in CAPABILITY.items() if cap.renderer is Renderer.PLANTUML}

assert set(PLANTUML_TEMPLATES) == _EXPECTED, (
    "PLANTUML_TEMPLATES must cover every PlantUML-backed DiagramType: "
    f"missing={_EXPECTED - set(PLANTUML_TEMPLATES)}, "
    f"extra={set(PLANTUML_TEMPLATES) - _EXPECTED}"
)


def get_template(t: DiagramType) -> str:
    """Return the PlantUML system-prompt fragment for diagram type `t`.

    Raises `ValueError` if `t` is not one of the PlantUML-backed types.
    """
    try:
        return PLANTUML_TEMPLATES[t]
    except KeyError:
        raise ValueError(
            f"No PlantUML template for diagram type {t.value!r}: it is rendered by "
            f"{CAPABILITY[t].renderer.value if t in CAPABILITY else 'an unknown renderer'}. "
            f"Supported here: {sorted(x.value for x in PLANTUML_TEMPLATES)}."
        ) from None
