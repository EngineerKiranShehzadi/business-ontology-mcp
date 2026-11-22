# Ontology Layer Package

This folder mirrors the repository architecture requested for Task 2:

- `core/`: ontology models and validation exports
- `adapters/postgres/`: PostgreSQL source
- `adapters/rest_api/`: HTTP JSON source
- `adapters/csv/`: CSV source
- `adapters/documents/`: structured and free-text documents
- `graph/`: knowledge graph and stores
- `semantic/`: deterministic relationship reasoning
- `mcp_server/`: stdio MCP entry point
- `examples/`: sample ontology definitions
- `tests/`: package-level test location

The implementation currently remains in the existing `ontology/` and root `server.py` modules. The files in this folder expose those implementations through the requested architecture and keep compatibility simple.
