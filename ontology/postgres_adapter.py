import psycopg
from psycopg.rows import dict_row

from .adapter import (
    Customer,
    Edge,
    OntologyDefinition,
    Order,
    Product,
    SampleFacts,
)
from .sources import OntologySource, validate_sample_consistency


class PostgreSQLAdapter(OntologySource):
    """PostgreSQL source adapter for Customers, Orders, Products, and order_items.

    The adapter reads normalized rows and returns an ontology object with source facts.
    """

    name = "postgresql"

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("POSTGRES_DSN is required")
        self.dsn = dsn

    def load(self, ontology: OntologyDefinition) -> OntologyDefinition:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT customer_id, name, status FROM customers")
                customer_rows = cursor.fetchall()

                cursor.execute(
                    "SELECT order_id, customer_id, order_date::text AS order_date, status FROM orders"
                )
                order_rows = cursor.fetchall()

                cursor.execute("SELECT product_id, name, category, price FROM products")
                product_rows = cursor.fetchall()

                cursor.execute("SELECT order_id, product_id FROM order_items")
                item_rows = cursor.fetchall()

        customers = [Customer.model_validate(row) for row in customer_rows]
        orders = [Order.model_validate(row) for row in order_rows]
        products = [
            Product.model_validate(
                {
                    **row,
                    "price": float(row["price"]),
                }
            )
            for row in product_rows
        ]

        edges: list[Edge] = []
        for order in orders:
            edges.append(
                Edge.model_validate(
                    {
                        "from": order.customer_id,
                        "to": order.order_id,
                        "relationship": "PLACES",
                    }
                )
            )
        for item in item_rows:
            edges.append(
                Edge.model_validate(
                    {
                        "from": item["order_id"],
                        "to": item["product_id"],
                        "relationship": "CONTAINS",
                    }
                )
            )

        facts = SampleFacts.model_validate(
            {
                "customers": [item.model_dump() for item in customers],
                "orders": [item.model_dump() for item in orders],
                "products": [item.model_dump() for item in products],
                "edges": [item.model_dump(by_alias=True) for item in edges],
            }
        )

        validate_sample_consistency(ontology, facts)

        return OntologyDefinition.model_validate(
            {
                "ontology_name": ontology.ontology_name,
                "ontology_version": ontology.ontology_version,
                "entities": ontology.entities,
                "relationships": ontology.relationships,
                "sample_facts": facts,
            }
        )

# SQL rows are converted into Pydantic models before the graph can use them.
# PLACES edges are inferred from Orders; CONTAINS edges come from order_items.
