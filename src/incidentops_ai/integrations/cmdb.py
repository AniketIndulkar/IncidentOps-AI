"""The legacy CMDB adapter (AIENG-58): SOAP 1.1 in, a modern `ServiceRecord` out.

It never raises for the CMDB's failures. Every lookup ends in a status the workflow can route on:
`ok`, `unknown_service` (a valid "not found"), `unavailable` (no answer, 5xx, timeout) or
`invalid` (unparseable XML, a SOAP fault, a missing field, or XML that tries DTD or entity tricks,
which defusedxml refuses). One retry on connection errors and 5xx.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal
from xml.etree.ElementTree import Element, ParseError
from xml.sax.saxutils import escape

import httpx
from defusedxml import DefusedXmlException, ElementTree
from pydantic import BaseModel, ValidationError

SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
CMDB = "urn:incidentops:cmdb"
LookupStatus = Literal["ok", "unknown_service", "unavailable", "invalid"]


class ServiceRecord(BaseModel):
    service: str
    owner: str
    tier: int
    maintenance_window: str


class CmdbLookup(BaseModel):
    status: LookupStatus
    record: ServiceRecord | None = None
    detail: str = ""


def _request(service: str) -> str:
    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="{SOAP}" xmlns:cmdb="{CMDB}"><soap:Body>'
        f"<cmdb:GetServiceRecord><cmdb:Service>{escape(service)}</cmdb:Service>"
        f"</cmdb:GetServiceRecord></soap:Body></soap:Envelope>"
    )


def _text(node: Element, name: str) -> str | None:
    found = node.find(f"{{{CMDB}}}{name}")
    return found.text.strip() if found is not None and found.text else None


def parse_response(body: bytes) -> CmdbLookup:
    try:
        root = ElementTree.fromstring(body)
    except (ParseError, DefusedXmlException) as error:
        return CmdbLookup(status="invalid", detail=f"unreadable XML ({type(error).__name__})")
    if root.tag != f"{{{SOAP}}}Envelope":
        return CmdbLookup(status="invalid", detail="not a SOAP envelope")
    if root.find(f".//{{{SOAP}}}Fault") is not None:
        return CmdbLookup(status="invalid", detail="SOAP fault")
    response = root.find(f".//{{{CMDB}}}GetServiceRecordResponse")
    if response is None:
        return CmdbLookup(status="invalid", detail="no GetServiceRecordResponse")
    if _text(response, "Found") == "false":
        return CmdbLookup(status="unknown_service")
    record = response.find(f"{{{CMDB}}}Record")
    if record is None:
        return CmdbLookup(status="invalid", detail="no Record")
    try:
        return CmdbLookup(
            status="ok",
            record=ServiceRecord(
                service=_text(record, "Name"),
                owner=_text(record, "Owner"),
                tier=_text(record, "Tier"),
                maintenance_window=_text(record, "MaintenanceWindow"),
            ),
        )
    except ValidationError as error:
        missing = ", ".join(str(e["loc"][0]) for e in error.errors())
        return CmdbLookup(status="invalid", detail=f"missing or bad fields: {missing}")


class CmdbClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 3.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._url = url.rstrip("/") + "/cmdb"
        self._http = httpx.AsyncClient(
            headers={
                "X-Api-Key": api_key,
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "GetServiceRecord",
            },
            transport=transport,
            timeout=timeout_s,
        )
        self._timeout_s = timeout_s
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._http.aclose()

    async def service_record(self, service: str) -> CmdbLookup:
        for attempt in (1, 2):
            try:
                async with asyncio.timeout(self._timeout_s):
                    response = await self._http.post(self._url, content=_request(service))
            except (httpx.TransportError, TimeoutError) as error:
                detail = f"no answer ({type(error).__name__})"
            else:
                if response.status_code < 400:
                    return parse_response(response.content)
                detail = f"HTTP {response.status_code}"
                if response.status_code < 500:
                    return CmdbLookup(status="unavailable", detail=detail)
            if attempt == 1:
                await self._sleep(0.2)
        return CmdbLookup(status="unavailable", detail=detail)
