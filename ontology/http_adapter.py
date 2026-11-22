import json
from urllib.request import Request, urlopen

from .adapter import OntologyDefinition
from .sources import OntologySource, build_ontology_from_payload


class HTTPJSONAdapter(OntologySource):
    """Read ontology facts from a read-only HTTP JSON endpoint.

    The endpoint response must contain the existing ontology fact collections.
    """

    name = "http"

    def __init__(self, url: str, timeout_seconds: float = 10.0):
        if not url:
            raise ValueError("HTTP_JSON_URL is required")
        self.url = url
        self.timeout_seconds = timeout_seconds

    def load(self, ontology: OntologyDefinition) -> OntologyDefinition:
        request = Request(
            self.url,
            headers={"Accept": "application/json", "User-Agent": "business-ontology-mcp/1.0"},
        )

        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if not isinstance(payload, dict):
            raise ValueError("HTTP JSON source must return a JSON object")

        # This is a read-only GET request. The response is validated as ontology facts, not executed as code.

        return build_ontology_from_payload(ontology, payload)
