"""Protocol fixture for route nodes and literal HTTP caller evidence.

The answerability benchmark uses this module to prove that an agent can move
from a client function to the local route it calls without falling back to grep.
"""

import requests
from fastapi import FastAPI

app = FastAPI()


@app.get("/api/accounts/{account_id}/status")
def account_status(account_id: str) -> dict[str, str]:
    return {"account_id": account_id, "status": "active"}


def fetch_account_status(account_id: str) -> requests.Response:
    return requests.request("GET", "/api/accounts/{account_id}/status")
