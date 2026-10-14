"""Diagram type capability matrix.

Single source of truth for which renderer backs each of the 14 UML diagram
types, which DSL keyword opens that diagram's source, and whether Mermaid is
only approximating the UML semantics (no native diagram for that type).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts import DiagramType, Renderer

__all__ = ["DiagramCapability", "CAPABILITY", "DEFAULT_TYPES", "renderer_for"]


@dataclass(frozen=True)
class DiagramCapability:
    """Everything needed to generate and render one diagram type."""

    renderer: Renderer
    dsl_keyword: str
    blurb: str
    approximated: bool = False


CAPABILITY: dict[DiagramType, DiagramCapability] = {
    # --- Mermaid-native ----------------------------------------------------
    DiagramType.CLASS: DiagramCapability(
        renderer=Renderer.MERMAID,
        dsl_keyword="classDiagram",
        blurb="Use to show the static structure of classes, their attributes, methods, and relationships.",
    ),
    DiagramType.SEQUENCE: DiagramCapability(
        renderer=Renderer.MERMAID,
        dsl_keyword="sequenceDiagram",
        blurb="Use to show the order of messages exchanged between objects over time.",
    ),
    DiagramType.STATE_MACHINE: DiagramCapability(
        renderer=Renderer.MERMAID,
        dsl_keyword="stateDiagram-v2",
        blurb="Use to show the states an object can be in and the events that transition it between them.",
    ),
    DiagramType.ACTIVITY: DiagramCapability(
        renderer=Renderer.MERMAID,
        dsl_keyword="flowchart TD",
        blurb="Use to show the step-by-step control flow of a business process or algorithm.",
    ),
    DiagramType.COMPONENT: DiagramCapability(
        renderer=Renderer.MERMAID,
        dsl_keyword="graph LR",
        blurb="Use to show how high-level components are wired together through their interfaces.",
    ),
    # --- Mermaid-approximated (no native UML diagram) -----------------------
    DiagramType.USE_CASE: DiagramCapability(
        renderer=Renderer.MERMAID,
        dsl_keyword="flowchart LR",
        blurb="Use to show how actors interact with the system's use cases.",
        approximated=True,
    ),
    DiagramType.PACKAGE: DiagramCapability(
        renderer=Renderer.MERMAID,
        dsl_keyword="graph TD",
        blurb="Use to show how the system is organised into packages and their dependencies.",
        approximated=True,
    ),
    # --- PlantUML-native -----------------------------------------------------
    DiagramType.OBJECT: DiagramCapability(
        renderer=Renderer.PLANTUML,
        dsl_keyword="@startuml",
        blurb="Use to show a concrete snapshot of object instances and their links at runtime.",
    ),
    DiagramType.COMPOSITE_STRUCTURE: DiagramCapability(
        renderer=Renderer.PLANTUML,
        dsl_keyword="@startuml",
        blurb="Use to show the internal parts, ports, and connectors of a single class or component.",
    ),
    DiagramType.DEPLOYMENT: DiagramCapability(
        renderer=Renderer.PLANTUML,
        dsl_keyword="@startuml",
        blurb="Use to show how software artifacts are deployed onto physical or virtual nodes.",
    ),
    DiagramType.PROFILE: DiagramCapability(
        renderer=Renderer.PLANTUML,
        dsl_keyword="@startuml",
        blurb="Use to define custom stereotypes and tagged values that extend the UML metamodel.",
    ),
    DiagramType.COMMUNICATION: DiagramCapability(
        renderer=Renderer.PLANTUML,
        dsl_keyword="@startuml",
        blurb="Use to show the messages exchanged between objects, organised around their links rather than time.",
    ),
    DiagramType.INTERACTION_OVERVIEW: DiagramCapability(
        renderer=Renderer.PLANTUML,
        dsl_keyword="@startuml",
        blurb="Use to show how several interaction diagrams are chained together with control flow.",
    ),
    DiagramType.TIMING: DiagramCapability(
        renderer=Renderer.PLANTUML,
        dsl_keyword="@startuml",
        blurb="Use to show how an object's state or value changes along a linear time axis.",
    ),
}

assert set(CAPABILITY) == set(DiagramType), (
    "CAPABILITY must cover every DiagramType member: "
    f"missing={set(DiagramType) - set(CAPABILITY)}, "
    f"extra={set(CAPABILITY) - set(DiagramType)}"
)


def renderer_for(t: DiagramType) -> Renderer:
    """Return which rendering backend produces the DSL for diagram type `t`."""
    return CAPABILITY[t].renderer


DEFAULT_TYPES: list[DiagramType] = [
    DiagramType.USE_CASE,
    DiagramType.COMPONENT,
    DiagramType.CLASS,
    DiagramType.SEQUENCE,
]
