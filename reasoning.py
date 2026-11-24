"""Small deterministic semantic reasoning layer.

Rules come from the ontology (``rules`` in the definition). A rule composes a chain
of relationships, for example PLACES then CONTAINS, into a derived relationship such
as PURCHASES. Nothing here is specific to one domain.
"""

from graph import OntologyGraph
from models import OntologyDefinition, RuleDefinition


class SemanticReasoner:
    """Derives facts through explicit ontology relationship composition."""

    def __init__(self, ontology: OntologyDefinition, graph: OntologyGraph | None = None):
        self.ontology = ontology
        self.graph = graph or OntologyGraph(ontology)

    def rule_names(self) -> list[str]:
        return [rule.name for rule in self.ontology.rules]

    def find_rule(self, name: str | None) -> RuleDefinition | None:
        if name is None:
            return self.ontology.rules[0] if self.ontology.rules else None
        return next((rule for rule in self.ontology.rules if rule.name.lower() == name.lower()), None)

    def derive_facts(self, entity_id: str | None = None, rule: str | None = None) -> dict:
        """Apply one rule (default: the first) and return its derived facts."""
        selected = self.find_rule(rule)
        if selected is None:
            error = f"Rule not found: {rule}" if rule else "The ontology defines no reasoning rules"
            return {"error": error, "available_rules": self.rule_names()}

        relationships = [self.ontology.relationship(step) for step in selected.path]
        start_type = relationships[0].from_entity
        derived: dict[tuple[str, str], dict] = {}

        # Start at nodes of the first relationship's source type and follow each
        # relationship in the path. The derived edge exists only if the whole chain does.
        for start_id, attributes in self.graph.graph.nodes(data=True):
            if attributes.get("entity_type") != start_type:
                continue
            if entity_id and entity_id != start_id:
                continue

            for end_id, via in self._follow(start_id, selected.path):
                fact = derived.setdefault(
                    (start_id, end_id),
                    {"source": start_id, "relationship": selected.name, "target": end_id, "via": set()},
                )
                fact["via"].update(via)

        facts = []
        for fact in derived.values():
            facts.append(
                {
                    **fact,
                    "via": sorted(fact["via"]),
                    "source_attributes": self.graph.graph.nodes[fact["source"]],
                    "target_attributes": self.graph.graph.nodes[fact["target"]],
                }
            )

        return {
            "rule": self._describe(selected, relationships),
            "filter": entity_id,
            "base_relationships": list(selected.path),
            "derived_relationship": selected.name,
            "fact_count": len(facts),
            "facts": facts,
            "available_rules": self.rule_names(),
        }

    def derive_all(self, entity_id: str | None = None) -> dict:
        """Apply every rule and return the results keyed by rule name."""
        return {"rules": {rule.name: self.derive_facts(entity_id, rule.name) for rule in self.ontology.rules}}

    def _follow(self, start_id: str, path: list[str]) -> list[tuple[str, list[str]]]:
        """Walk ``path`` from ``start_id``; return (end node, intermediate nodes) pairs."""
        frontier = [(start_id, [])]
        for index, step in enumerate(path):
            next_frontier = []
            for node_id, via in frontier:
                for _, target, data in self.graph.graph.out_edges(node_id, data=True):
                    if data.get("relationship") == step:
                        is_last = index == len(path) - 1
                        next_frontier.append((target, via if is_last else [*via, target]))
            frontier = next_frontier
        return frontier

    @staticmethod
    def _describe(rule: RuleDefinition, relationships: list) -> str:
        if rule.description:
            return rule.description
        chain = " ".join(
            f"{rel.name} {rel.to_entity}" for rel in relationships
        )
        return f"{relationships[0].from_entity} {chain} => {relationships[0].from_entity} {rule.name} {relationships[-1].to_entity}"
