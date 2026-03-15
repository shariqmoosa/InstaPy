"""FastAPI wrapper for the shopping agent.

Exposes the agent over HTTP so it can be hosted on Railway, Render, Fly.io, etc.

Environment variables:
    ANTHROPIC_API_KEY   — Claude API key  (at least one is required)
    OPENAI_API_KEY      — OpenAI API key
    GEMINI_API_KEY      — Google Gemini API key
    SURFSHARK_USERNAME  — Surfshark service username (optional default)
    SURFSHARK_PASSWORD  — Surfshark service password (optional default)
    API_TOKEN           — Bearer token to protect your endpoints (optional but recommended)

Endpoints:
    GET  /              — health check
    POST /pricing       — get full fee breakdown for one product URL
    POST /compare       — compare fees across multiple stores
    GET  /accounts      — list saved Surfshark account profiles
    POST /accounts      — add / update an account profile
    DELETE /accounts/{name} — remove an account profile
    GET  /locations     — list all Surfshark server shortcodes
"""
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .agent import ShoppingAgent
from .vpn import AccountManager, SURFSHARK_LOCATIONS

app = FastAPI(
    title="Shopping Agent API",
    description="Extract and compare delivery fees across stores",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_bearer = HTTPBearer(auto_error=False)
_API_TOKEN = os.getenv("API_TOKEN")


def _check_token(credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer)):
    """If API_TOKEN env var is set, require a matching Bearer token."""
    if _API_TOKEN:
        if not credentials or credentials.credentials != _API_TOKEN:
            raise HTTPException(status_code=401, detail="Invalid or missing Bearer token")


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------

class PricingRequest(BaseModel):
    url: str
    zip_code: Optional[str] = None
    address: Optional[str] = None
    account: Optional[str] = None
    # Inline Surfshark credentials (used when no saved account is specified)
    surfshark_location: Optional[str] = None
    surfshark_username: Optional[str] = None
    surfshark_password: Optional[str] = None
    provider: str = "auto"
    model: Optional[str] = None


class CompareRequest(BaseModel):
    stores: dict  # {"label": "url", ...}
    zip_code: Optional[str] = None
    address: Optional[str] = None
    account: Optional[str] = None
    surfshark_location: Optional[str] = None
    surfshark_username: Optional[str] = None
    surfshark_password: Optional[str] = None
    provider: str = "auto"
    model: Optional[str] = None


class AddAccountRequest(BaseModel):
    name: str
    location: str
    username: Optional[str] = None  # falls back to SURFSHARK_USERNAME env var
    password: Optional[str] = None  # falls back to SURFSHARK_PASSWORD env var


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _resolve_account(req_account, req_location, req_username, req_password) -> Optional[str]:
    """
    If the request names a saved account, use it.
    If inline location/credentials are given, create a temporary account called '__inline__'.
    Returns the account name to pass to ShoppingAgent, or None.
    """
    if req_account:
        return req_account

    location = req_location
    username = req_username or os.getenv("SURFSHARK_USERNAME")
    password = req_password or os.getenv("SURFSHARK_PASSWORD")

    if location and username and password:
        mgr = AccountManager()
        mgr.add("__inline__", location, username, password)
        return "__inline__"

    return None


def _make_agent(provider, model, account):
    return ShoppingAgent(
        provider=provider,
        model=model,
        headless=True,
        account=account,
        verbose=False,
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@app.get("/", tags=["health"])
def health():
    return {"status": "ok", "service": "shopping-agent"}


@app.post("/pricing", tags=["agent"], dependencies=[Depends(_check_token)])
def get_pricing(req: PricingRequest):
    """Get the full fee breakdown for a single product URL."""
    account = _resolve_account(
        req.account, req.surfshark_location,
        req.surfshark_username, req.surfshark_password,
    )
    try:
        agent = _make_agent(req.provider, req.model, account)
        result = agent.get_pricing(req.url, zip_code=req.zip_code, address=req.address)
        return result.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/compare", tags=["agent"], dependencies=[Depends(_check_token)])
def compare(req: CompareRequest):
    """Compare fees across multiple stores."""
    account = _resolve_account(
        req.account, req.surfshark_location,
        req.surfshark_username, req.surfshark_password,
    )
    try:
        agent = _make_agent(req.provider, req.model, account)
        results = agent.compare(req.stores, zip_code=req.zip_code, address=req.address)
        return [
            {"store": r["store"], "url": r["url"], "pricing": r["result"].to_dict()}
            for r in results
        ]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/accounts", tags=["accounts"], dependencies=[Depends(_check_token)])
def list_accounts():
    """List all saved Surfshark account profiles."""
    return AccountManager().list_all()


@app.post("/accounts", tags=["accounts"], dependencies=[Depends(_check_token)])
def add_account(req: AddAccountRequest):
    """Add or update a named Surfshark account profile."""
    username = req.username or os.getenv("SURFSHARK_USERNAME")
    password = req.password or os.getenv("SURFSHARK_PASSWORD")
    if not username or not password:
        raise HTTPException(
            status_code=400,
            detail="username and password are required (or set SURFSHARK_USERNAME / SURFSHARK_PASSWORD env vars)",
        )
    if req.location not in SURFSHARK_LOCATIONS and "." not in req.location:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown location '{req.location}'. Call GET /locations for valid shortcodes.",
        )
    cfg = AccountManager().add(req.name, req.location, username, password)
    return {"name": req.name, "host": cfg.host, "port": cfg.port, "scheme": cfg.scheme}


@app.delete("/accounts/{name}", tags=["accounts"], dependencies=[Depends(_check_token)])
def remove_account(name: str):
    """Remove a saved account profile."""
    removed = AccountManager().remove(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Account '{name}' not found")
    return {"removed": name}


@app.get("/locations", tags=["accounts"])
def list_locations():
    """List all available Surfshark server shortcodes."""
    return SURFSHARK_LOCATIONS
