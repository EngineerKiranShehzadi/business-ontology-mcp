business ontology mcp

this is a small project where you describe a business domain (customers, orders, products, or whatever you have) in a json file and it turns that into a knowledge graph. on top of that there is an mcp server, so an agent can ask what the concepts and relationships are and query the facts, and a simple web page to click around in.

the example domain is customers, orders and products, and it lives in data/ontology.json. entities, their properties, the relationships between them and a few rules (like customer -> order -> product means the customer purchased that product) are all defined there, nothing about the domain is hardcoded in the python files.

to set it up you need python 3.11 or newer:

python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env

then python server.py starts the mcp server (stdio only, so a client has to launch it) and python webui.py opens the web page at http://127.0.0.1:8000. python -m pytest runs the tests.

where the facts come from is set with SOURCE_ADAPTER in .env. it can be json (default), csv, http, document (json, txt, md, pdf, docx) or postgresql. the graph is kept in memory with networkx, or in neo4j if you set GRAPH_STORE=neo4j and start it with docker compose up -d. i have only tested json, csv and document properly, postgresql and neo4j have not been run against real servers yet.

the reload_data tool (or the reload button in the web page) re-reads the source without restarting, and REFRESH_INTERVAL_SECONDS does it on a timer.

The **Import** tab builds an ontology from tables in CSV, JSON, Word, PDF, and text files. Save XLSX files as CSV first. To use an imported ontology in the MCP server, set `ONTOLOGY_PATH=data/generated/ontology.json` and `SOURCE_ADAPTER=json` in `.env`.

### PDF prose extraction

Upload searchable PDFs and select **PDF text (Ollama)**. Analyze the files and review the proposed entity types and relationships before applying. Extracted links retain their source filename and a supporting quote. Each analysis accepts up to 10,000 characters of PDF text. Run OCR on scanned documents before uploading them.

- For local Ollama, set `PDF_LLM_PROVIDER=local` and set `OLLAMA_MODEL` to a model from `ollama list`. The backend uses `127.0.0.1:11434`.
- For Ollama Cloud, set `PDF_LLM_PROVIDER=cloud`, `OLLAMA_MODEL` to a supported Cloud model, and `OLLAMA_API_KEY` in the backend environment or private `.env`. The backend sends extracted text to Ollama Cloud. Teammates only need a browser to use a shared instance.

To share the app, run it behind an authenticated HTTPS reverse proxy on the same host and set `WEBUI_PUBLIC_ORIGIN` to its public HTTPS origin. The app binds to loopback and the proxy controls access. One instance shares its uploads, pending proposal, and applied ontology across users, so use it as a shared group workspace.

the mcp tools are list_concepts, get_concept, list_entities, query_facts, get_neighbors, derive_facts, get_system_status, reload_data and sync_graph.
