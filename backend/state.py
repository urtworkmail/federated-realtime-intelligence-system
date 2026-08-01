"""
Process-wide singleton handles, set once in main.py's lifespan and read by
every router module. Exists so routers (api/*.py) can reach the shared
services without importing main.py itself (which would be circular, since
main.py is the one that includes the routers).
"""

from typing import Optional

fusion_engine = None
connector_manager = None
logging_service = None
embedding_service = None
account_service = None
llm_credential_service = None
llm_service = None
metering_service = None
storage_service = None
context_service = None
