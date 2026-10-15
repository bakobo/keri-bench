"""The one credential schema the bench issues on a real stack.

The smallest schema imbu's simple issuance accepts: a single string attribute, so the numbers
measure issuance and not attribute processing. Shaped like the summit harness's Utah agent
stand-in (bakobo/sedi-summit-nov26 harness/src/sedi_harness/schemas/__init__.py).
"""

from functools import cache


@cache
def raw() -> bytes:
    from keri.core import scheming
    return scheming.Schemer(sed={
        "$id": "", "title": "keri-bench credential", "type": "object",
        "properties": {"a": {"type": "object",
                             "properties": {"benchCredential": {"type": "string"}},
                             "required": ["benchCredential"]}},
        "required": ["a"]}).raw


@cache
def said() -> str:
    from keri.core import scheming
    return scheming.Schemer(raw=raw()).said
