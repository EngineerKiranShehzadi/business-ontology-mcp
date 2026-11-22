import csv
from pathlib import Path

from .adapter import OntologyDefinition
from .sources import OntologySource, build_ontology_from_payload


class CSVAdapter(OntologySource):
    """Read customer, order, product, and order-item data from CSV files.

    The CSV adapter creates PLACES and CONTAINS relationships from the row keys.

    The CSV files use stable IDs. Orders build PLACES edges and order items build CONTAINS edges.
    """

    name = "csv"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def load(self, ontology: OntologyDefinition) -> OntologyDefinition:
        if not self.directory.exists():
            raise FileNotFoundError(f"CSV source directory not found: {self.directory}")

        customers = self._read_rows("customers.csv")
        orders = self._read_rows("orders.csv")
        products = self._read_rows("products.csv")
        order_items = self._read_rows("order_items.csv")

        edges = []
        for order in orders:
            edges.append(
                {
                    "from": order["customer_id"],
                    "to": order["order_id"],
                    "relationship": "PLACES",
                }
            )
        for item in order_items:
            edges.append(
                {
                    "from": item["order_id"],
                    "to": item["product_id"],
                    "relationship": "CONTAINS",
                }
            )

        return build_ontology_from_payload(
            ontology,
            {
                "customers": customers,
                "orders": orders,
                "products": products,
                "edges": edges,
            },
        )

    def _read_rows(self, filename: str) -> list[dict]:
        path = self.directory / filename
        if not path.exists():
            raise FileNotFoundError(f"CSV source file not found: {path}")

        with path.open("r", encoding="utf-8", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]
