from itertools import permutations

SUPPORTED_CURRENCIES = ["AUD", "CAD", "TWD", "USD"]
SUPPORTED_EXCHANGES = ["US", "TW", "TWO", "AX", "TO"]

all_pairs = list(permutations(SUPPORTED_CURRENCIES, 2))
SUPPORTED_FX_PAIRS = [f"{base}{quote}=X" for base, quote in all_pairs if base != quote]

EXCHANGE_CURRENCY_MAP = {
    "US": "USD",
    "TW": "TWD",
    "TWO": "TWD",
    "AX": "AUD",
    "TO": "CAD"
}