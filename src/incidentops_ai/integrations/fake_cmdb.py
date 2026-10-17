"""A fake legacy CMDB speaking SOAP 1.1, with switches for the ways such a service fails."""

import asyncio
from xml.sax.saxutils import escape

from defusedxml import ElementTree
from fastapi import FastAPI, Request, Response

SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
CMDB = "urn:incidentops:cmdb"

RECORDS = {
    "orders-api": ("Amara Okafor", 1, "Sun 02:00-04:00 UTC"),
    "checkout-api": ("Amara Okafor", 1, "Sun 02:00-04:00 UTC"),
    "payments-gateway": ("Lena Fischer", 1, "Sat 23:00-01:00 UTC"),
    "billing-service": ("Lena Fischer", 1, "Sat 23:00-01:00 UTC"),
    "notification-worker": ("Kenji Watanabe", 2, "Wed 03:00-04:00 UTC"),
    "session-service": ("Priya Raman", 2, "Tue 03:00-04:00 UTC"),
    "internal-auth-proxy": ("Priya Raman", 2, "Tue 03:00-04:00 UTC"),
    "search-indexer": ("Diego Alvarez", 3, "Daily 05:00-05:30 UTC"),
    "web-dashboard": ("Diego Alvarez", 3, "Daily 05:00-05:30 UTC"),
}


def _envelope(body: str) -> str:
    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="{SOAP}" xmlns:cmdb="{CMDB}"><soap:Body>{body}'
        f"</soap:Body></soap:Envelope>"
    )


class FakeCmdb:
    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key
        self.calls = 0
        self._fault: str | None = None
        self._next: list[int] = []
        self.app = self._build()

    def set_fault(self, fault: str | None) -> None:
        """down | slow | malformed | soap_fault | missing_owner | entity_attack, or None."""
        self._fault = fault

    def fail_next(self, *statuses: int) -> None:
        self._next += list(statuses)

    def _answer(self, service: str) -> str:
        if self._fault == "soap_fault":
            return _envelope(
                "<soap:Fault><faultcode>soap:Server</faultcode>"
                "<faultstring>CMDB backend unavailable</faultstring></soap:Fault>"
            )
        if service not in RECORDS:
            return _envelope(
                "<cmdb:GetServiceRecordResponse><cmdb:Found>false</cmdb:Found>"
                "</cmdb:GetServiceRecordResponse>"
            )
        owner, tier, window = RECORDS[service]
        owner_xml = (
            "" if self._fault == "missing_owner" else f"<cmdb:Owner>{escape(owner)}</cmdb:Owner>"
        )
        return _envelope(
            "<cmdb:GetServiceRecordResponse><cmdb:Found>true</cmdb:Found><cmdb:Record>"
            f"<cmdb:Name>{escape(service)}</cmdb:Name>{owner_xml}<cmdb:Tier>{tier}</cmdb:Tier>"
            f"<cmdb:MaintenanceWindow>{escape(window)}</cmdb:MaintenanceWindow>"
            "</cmdb:Record></cmdb:GetServiceRecordResponse>"
        )

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.post("/cmdb")
        async def cmdb(request: Request):
            self.calls += 1
            if request.headers.get("x-api-key") != self.api_key:
                return Response("forbidden", status_code=403)
            if self._next:
                return Response("unavailable", status_code=self._next.pop(0))
            if self._fault == "down":
                return Response("unavailable", status_code=503)
            if self._fault == "slow":
                await asyncio.sleep(1.0)
            if self._fault == "malformed":
                return Response("<soap:Envelope><soap:Body><unclosed", media_type="text/xml")
            if self._fault == "entity_attack":
                bomb = (
                    '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
                    '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
                    f'<soap:Envelope xmlns:soap="{SOAP}"><soap:Body>&b;</soap:Body></soap:Envelope>'
                )
                return Response(bomb, media_type="text/xml")
            request_xml = ElementTree.fromstring(await request.body())
            service = request_xml.findtext(f".//{{{CMDB}}}Service") or ""
            return Response(self._answer(service), media_type="text/xml")

        return app
