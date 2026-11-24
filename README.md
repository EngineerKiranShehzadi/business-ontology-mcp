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

the import tab in the web page lets you upload csv, json, word, pdf or text files that contain tables and it builds an ontology from them: one entity per table, column types, ids, references between tables and relationships. there is no ai in there, it just follows fixed rules in inference.py, so a pdf with normal sentences gives you nothing. scanned pdfs do not work either, and xlsx files need to be saved as csv first. the mcp server cannot import files, only the web page can, and it needs ONTOLOGY_PATH=data/generated/ontology.json and SOURCE_ADAPTER=json in .env if you want the mcp server to use what you imported.

the mcp tools are list_concepts, get_concept, list_entities, query_facts, get_neighbors, derive_facts, get_system_status, reload_data and sync_graph.
