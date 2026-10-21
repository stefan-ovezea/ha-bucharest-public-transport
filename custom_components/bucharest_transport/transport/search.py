"""Permissive station search that never merges distinct stop identifiers."""

import re
import unicodedata
from difflib import SequenceMatcher


def normalize(value: str) -> str:
    value = "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c)
    )
    value = re.sub(r"\bb[.\s-]*dul\b", "bulevardul", value)
    value = re.sub(r"[^\w\s]", " ", value)
    aliases = {"pta": "piata", "pt": "piata", "bd": "bulevardul", "str": "strada"}
    return " ".join(aliases.get(token, token) for token in value.split())


def search(stops: list[dict], query: str, limit: int = 20) -> list[dict]:
    query = normalize(query[:120])
    if len(query) < 2:
        return []
    ranked = []
    for stop in stops:
        name = stop["normalized"]
        if query == name or query == normalize(stop["stop_id"]):
            score = 4.0
        elif name.startswith(query):
            score = 3.0
        elif query in name or all(
            any(t.startswith(q) for t in name.split()) for q in query.split()
        ):
            score = 2.0
        else:
            score = max(
                SequenceMatcher(None, query, name).ratio(),
                max((SequenceMatcher(None, query, t).ratio() for t in name.split()), default=0),
                SequenceMatcher(
                    None, " ".join(sorted(query.split())), " ".join(sorted(name.split()))
                ).ratio(),
            )
            if score < 0.65:
                continue
        ranked.append((score, stop))
    ranked.sort(key=lambda x: (-x[0], x[1]["name"], x[1]["stop_id"]))
    return [{k: v for k, v in s.items() if k != "normalized"} for _, s in ranked[:limit]]
