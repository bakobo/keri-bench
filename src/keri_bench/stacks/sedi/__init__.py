"""What the SEDI bulk ceremony issues against (this.i 4f987y88).

``sedi-iar``, ``sedi-core`` and ``sedi-residence`` are byte copies of bakobo/sedi-summit-nov26
harness/src/sedi_harness/schemas/ at commit ce3b91edc3b32ef0b12c03ec90924c3b1dbc9fed, which are
bakobo/schema 06b60f02, Sam Smith's bytes at keripy 927c0cb8. The Utah agent schema is that
harness's stand-in, since the State's real agent credential comes later. The citizen is Sam
Smith's Guy (keripy tests/sedi/test_sedi.py), as the summit harness has him, and the rules block
is Sam's, as heti's Core vectors use it.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

HERE = Path(__file__).parent
RULES = {"d": "EFPxq4WPl29szUqbrQIviOh_Ls_RlrYbp4L-fdQH0XrX", "l": ""}
AGENCY_ATTRIBUTES = {"agency": "Department of Government Operations"}
CORE = {"givenName": "Guy", "middleName": "Marty McFly", "familyName": "Brown",
        "nameSuffix": "Jr.", "birthDate": "2002-08-22T00:00:00.000000+00:00",
        "facialImageProof": "EIQw_2CqmmC96YYUFXTW8XSkLQU2-v9bDCyItazmKhTW",
        "legalPresenceStatus": "citizen"}
RESIDENCE = {"street": "157 E 300 N", "city": "Milford", "county": "Beaver", "state": "Utah",
             "postcode": "84751", "country": "United States"}
"""The fields a proofing agent attests, which imbu requires Core and Residence to repeat."""


@cache
def raw(name: str) -> bytes:
    """A schema's bytes: ``iar``, ``core``, ``residence`` or ``agency``."""
    if name == "agency":
        from keri.core import scheming
        return scheming.Schemer(sed={
            "$id": "", "title": "Utah agent authorization", "type": "object",
            "properties": {"a": {"type": "object", "properties": {"agency": {"type": "string"}},
                                 "required": ["agency"]}},
            "required": ["a"]}).raw
    return (HERE / f"sedi-{name}.schema.json").read_bytes()


@cache
def said(name: str) -> str:
    from keri.core import scheming
    return scheming.Schemer(raw=raw(name)).said


def iar_attributes() -> dict:
    """What the proofing agent attests about the citizen, at the moment of proofing."""
    return {**CORE, "residence": RESIDENCE, "sediURL": "https://trust.bakobo.com/demo/sedi",
            "proofingDatetime": datetime.now(timezone.utc).isoformat(timespec="microseconds")}


def _blocks(values: dict) -> dict:
    """Sam-shaped attribute blocks; heti fills each block's nonce and SAID at issuance."""
    return {label: {"d": "", "value": value} for label, value in values.items()}


def ceremony(*, copies: list[str], agent: dict, iar: bytes) -> dict:
    """One bulk request: a copy for each holder AID, Core naming the Utah agent credential as
    its far node, and the IAR that authorizes the ceremony."""
    core = {"attributes": _blocks(CORE), "rules": RULES,
            "edge": {"utahAgent": {"n": agent["said"], "s": said("agency"), "o": "DI2I"}},
            "far": [{"acdc": agent["acdc"], "tel": agent["chain"]}]}
    return {"copies": [{"holder_aid": aid} for aid in copies], "core": core,
            "residence": {"attributes": _blocks(RESIDENCE), "rules": RULES},
            "iar": base64.b64encode(iar).decode()}
