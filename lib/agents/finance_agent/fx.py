"""finance_agent private helper: ISO-4217 currency-code validation. Moved here from
the shared lib/tools_utils grab-bag — single consumer (get_exchange_rate)."""

import pycountry


def validate_currency_code(code: str) -> bool:
    return pycountry.currencies.get(alpha_3 = code) is not None
