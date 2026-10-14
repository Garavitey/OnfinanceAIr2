"""In-memory fixtures for stub route handlers.

Every handler in app/routes.py that hasn't been replaced by a later step
returns data from here instead of doing real work, so the route and SSE
event shapes can be finalised before the real spec extractor and generator
exist. FIXTURE_DIAGRAMS sources are hand-written valid Mermaid, checked
against validator/validate.mjs.
"""

from __future__ import annotations

from app.contracts import DiagramType, SystemSpec

FIXTURE_SPEC = SystemSpec(
    entities=["Order", "TradeReport", "ComplianceRule", "Trader", "AuditLog"],
    actors=["Retail Trader", "Compliance Officer", "SEBI Regulator"],
    services=[
        "Order Management System",
        "Risk Management Service",
        "SEBI Reporting Gateway",
        "Audit Service",
    ],
    flows=[
        "Trader places an order",
        "OMS validates the order against risk limits",
        "OMS submits a trade report to the SEBI reporting gateway",
        "SEBI gateway acknowledges receipt of the report",
    ],
    states=["Pending", "Validated", "Submitted", "Acknowledged", "Rejected"],
    deployment_nodes=[
        "Trading App Server",
        "Risk Engine Cluster",
        "SEBI Gateway Node",
        "Audit Database",
    ],
    suggested_diagram_types=[
        DiagramType.SEQUENCE,
        DiagramType.COMPONENT,
        DiagramType.CLASS,
    ],
)

FIXTURE_DIAGRAMS: dict[DiagramType, str] = {
    DiagramType.SEQUENCE: """\
sequenceDiagram
    participant Trader
    participant OMS as Order Mgmt System
    participant SEBI as SEBI Reporting Gateway
    Trader->>OMS: Place order
    OMS->>OMS: Validate against risk limits
    OMS->>SEBI: Submit trade report
    SEBI-->>OMS: Ack receipt
    OMS-->>Trader: Order confirmed""",
    DiagramType.COMPONENT: """\
graph LR
    Trader[Trader Portal] --> OMS[Order Management System]
    OMS --> RMS[Risk Management Service]
    RMS --> SEBI[SEBI Reporting Gateway]
    OMS --> LEDGER[Trade Ledger]
    LEDGER --> AUDIT[Audit Service]""",
    DiagramType.CLASS: """\
classDiagram
    class Order {
        +String orderId
        +Decimal quantity
        +validate()
    }
    class ComplianceRule {
        +String ruleCode
        +evaluate()
    }
    class TradeReport{+String reportId}
    Order --> ComplianceRule
    ComplianceRule --> TradeReport""",
}
