"""BTP plumbing: Destination + Connectivity (Cloud Connector) from a Cloud Foundry app."""
import json
import os
import time

import requests


def _token_url(creds: dict) -> str:
    return creds.get("token_service_url") or creds["url"].rstrip("/") + "/oauth/token"


class BtpConnectivity:
    def __init__(self, vcap: dict | None = None, http=requests, location_id: str | None = None):
        vcap = vcap or json.loads(os.environ["VCAP_SERVICES"])
        self.dest = vcap["destination"][0]["credentials"]
        self.conn = vcap["connectivity"][0]["credentials"]
        self.http = http
        self.location_id = location_id or os.getenv("SCC_LOCATION_ID")
        self._tokens: dict[str, tuple[str, float]] = {}

    def token(self, creds: dict) -> str:
        cached = self._tokens.get(creds["clientid"])
        if cached and cached[1] > time.time() + 30:
            return cached[0]
        r = self.http.post(_token_url(creds), data={"grant_type": "client_credentials"},
                           auth=(creds["clientid"], creds["clientsecret"]), timeout=30)
        r.raise_for_status()
        body = r.json()
        self._tokens[creds["clientid"]] = (body["access_token"], time.time() + body.get("expires_in", 300))
        return body["access_token"]

    def destination(self, name: str) -> dict:
        r = self.http.get(f"{self.dest['uri'].rstrip('/')}/destination-configuration/v1/destinations/{name}",
                          headers={"Authorization": f"Bearer {self.token(self.dest)}"}, timeout=30)
        r.raise_for_status()
        return r.json()

    @property
    def proxies(self) -> dict:
        p = f"http://{self.conn['onpremise_proxy_host']}:{self.conn['onpremise_proxy_http_port']}"
        return {"http": p, "https": p}

    def proxy_headers(self) -> dict:
        h = {"Proxy-Authorization": f"Bearer {self.token(self.conn)}"}
        if self.location_id:
            h["SAP-Connectivity-SCC-Location_ID"] = self.location_id
        return h


class DestinationSession:
    """request(method, path, ...) against an on-premise destination routed via Cloud Connector."""

    def __init__(self, btp: BtpConnectivity, destination_name: str):
        cfg = btp.destination(destination_name)
        conf = cfg["destinationConfiguration"]
        self.btp = btp
        self.base = conf["URL"].rstrip("/")
        self.sap_client = conf.get("sap-client")
        self.auth = {t["http_header"]["key"]: t["http_header"]["value"]
                     for t in cfg.get("authTokens", []) if "http_header" in t}
        self.http = requests.Session()   # keeps ECC cookies needed for CSRF

    def request(self, method: str, path: str, params: dict | None = None, headers: dict | None = None, **kw):
        params = dict(params or {})
        if self.sap_client:
            params.setdefault("sap-client", self.sap_client)
        return self.http.request(method, self.base + path, params=params, proxies=self.btp.proxies,
                                 headers={**self.auth, **self.btp.proxy_headers(), **(headers or {})},
                                 timeout=30, **kw)
