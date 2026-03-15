"""Surfshark VPN account manager for the shopping agent.

Each "account" maps a named profile to a Surfshark SOCKS5 proxy server so
that different store accounts appear to come from different IP addresses.

Setup
-----
1. Log in to surfshark.com → VPN → Manual setup → click "Service credentials"
   (these are different from your Surfshark login email/password)
2. Pick a server location from SURFSHARK_LOCATIONS or supply any full hostname.
3. Add accounts:

    python -m shopping_agent.main account add alice us-nyc
    # enter your Surfshark service credentials when prompted

4. Run the agent with an account:

    python -m shopping_agent.main get --url "https://..." --account alice

Storage: ~/.shopping_agent/accounts.json
"""
import json
import os
from dataclasses import dataclass
from typing import Optional


_DEFAULT_ACCOUNTS_FILE = os.path.join(
    os.path.expanduser("~"), ".shopping_agent", "accounts.json"
)

# Common Surfshark SOCKS5 hostnames.
# Full list via: curl -s https://api.surfshark.com/v3/server/clusters | python -m json.tool
SURFSHARK_LOCATIONS = {
    # United States
    "us-nyc": "us-nyc.socks.surfshark.com",
    "us-lax": "us-lax.socks.surfshark.com",
    "us-chi": "us-chi.socks.surfshark.com",
    "us-mia": "us-mia.socks.surfshark.com",
    "us-dal": "us-dal.socks.surfshark.com",
    "us-sea": "us-sea.socks.surfshark.com",
    "us-atl": "us-atl.socks.surfshark.com",
    "us-den": "us-den.socks.surfshark.com",
    "us-phx": "us-phx.socks.surfshark.com",
    "us-hou": "us-hou.socks.surfshark.com",
    # United Kingdom
    "uk-lon": "uk-lon.socks.surfshark.com",
    "uk-man": "uk-man.socks.surfshark.com",
    # Europe
    "de-fra": "de-fra.socks.surfshark.com",
    "de-ber": "de-ber.socks.surfshark.com",
    "fr-par": "fr-par.socks.surfshark.com",
    "nl-ams": "nl-ams.socks.surfshark.com",
    "es-mad": "es-mad.socks.surfshark.com",
    "it-mil": "it-mil.socks.surfshark.com",
    "se-sto": "se-sto.socks.surfshark.com",
    "ch-zur": "ch-zur.socks.surfshark.com",
    "no-osl": "no-osl.socks.surfshark.com",
    "pl-war": "pl-war.socks.surfshark.com",
    # Canada / Americas
    "ca-tor": "ca-tor.socks.surfshark.com",
    "ca-van": "ca-van.socks.surfshark.com",
    "br-sao": "br-sao.socks.surfshark.com",
    # Asia-Pacific
    "au-syd": "au-syd.socks.surfshark.com",
    "au-mel": "au-mel.socks.surfshark.com",
    "jp-tok": "jp-tok.socks.surfshark.com",
    "sg-sin": "sg-sin.socks.surfshark.com",
    "in-mum": "in-mum.socks.surfshark.com",
    "hk-hkg": "hk-hkg.socks.surfshark.com",
    "kr-seo": "kr-seo.socks.surfshark.com",
}

SURFSHARK_SOCKS5_PORT = 1080


@dataclass
class ProxyConfig:
    host: str
    port: int
    username: str
    password: str
    scheme: str = "socks5"

    def to_dict(self):
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "scheme": self.scheme,
        }


class AccountManager:
    """Persists named account → Surfshark proxy mappings.

    Accounts are stored as JSON at ~/.shopping_agent/accounts.json.
    Each account name is also used to namespace browser cookies so that
    different accounts maintain separate login sessions.
    """

    def __init__(self, accounts_file: Optional[str] = None):
        self._path = accounts_file or _DEFAULT_ACCOUNTS_FILE
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._data = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save(self):
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, name: str, location: str, username: str, password: str) -> ProxyConfig:
        """Add or update an account → Surfshark proxy mapping.

        Args:
            name:     Friendly account name, e.g. "alice" or "work"
            location: Surfshark location key (e.g. "us-nyc") or a full
                      SOCKS5 hostname (e.g. "us-nyc.socks.surfshark.com")
            username: Surfshark *service* username (from VPN dashboard)
            password: Surfshark *service* password (from VPN dashboard)

        Returns:
            ProxyConfig for the newly created account.
        """
        host = SURFSHARK_LOCATIONS.get(location, location)
        self._data[name] = {
            "host": host,
            "port": SURFSHARK_SOCKS5_PORT,
            "username": username,
            "password": password,
            "scheme": "socks5",
        }
        self._save()
        print(f"[VPN] Account '{name}' → {host}:{SURFSHARK_SOCKS5_PORT} saved.")
        return self.get(name)

    def get(self, name: str) -> Optional[ProxyConfig]:
        """Return ProxyConfig for the named account, or None if not found."""
        entry = self._data.get(name)
        if not entry:
            return None
        return ProxyConfig(
            host=entry["host"],
            port=entry["port"],
            username=entry["username"],
            password=entry["password"],
            scheme=entry.get("scheme", "socks5"),
        )

    def remove(self, name: str) -> bool:
        """Delete an account. Returns True if it existed."""
        if name in self._data:
            del self._data[name]
            self._save()
            print(f"[VPN] Account '{name}' removed.")
            return True
        print(f"[VPN] Account '{name}' not found.")
        return False

    def list_all(self) -> dict:
        """Return all accounts with passwords masked."""
        return {
            name: {
                "host": v["host"],
                "port": v["port"],
                "scheme": v.get("scheme", "socks5"),
                "username": v["username"],
                "password": "***",
            }
            for name, v in self._data.items()
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def list_locations() -> dict:
        """Return the built-in Surfshark location shortcodes → hostnames."""
        return dict(SURFSHARK_LOCATIONS)
