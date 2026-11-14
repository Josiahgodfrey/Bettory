"""Team-name normalisation for alias matching (DATA-MODEL.md section 8). Pure.

Deliberately conservative: only removes accents, punctuation and common club-type tokens.
No fuzzy matching - names that still differ go to the review queue.
"""

import re
import unicodedata

# Club-type tokens that carry no identity. Kept short on purpose.
_NOISE_TOKENS = frozenset({"fc", "afc", "cf", "fk", "sc", "ac", "cfc", "ssc"})


def normalize_team_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t not in _NOISE_TOKENS]
    result = " ".join(tokens)
    if not result:
        raise ValueError(f"team name normalises to nothing: {name!r}")
    return result
