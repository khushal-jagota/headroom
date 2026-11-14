"""Compare JSON values without losing exact numbers or merging booleans with numbers."""

import json
from decimal import Decimal
from typing import Any


def json_values(source: str) -> Any:
    def number(token: str) -> tuple[str, Decimal]:
        return "number", Decimal(token)

    return json.loads(source, parse_int=number, parse_float=number)
