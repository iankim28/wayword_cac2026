"""Sound-distance matcher. No third-party libraries.

Phones come from one letter-to-sound function. A place may also store a
local respelling (local_as) and exact mishearings. The swap table makes
th/s and f/p cheap. Close vowels are cheap too. Distance is divided by
the heard length, so a short word does not match a much longer town.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOCAB_PATH = ROOT / "data" / "vocab.json"

VOWEL_DIGRAPHS = {
    "ai": ["EY"],
    "ay": ["EY"],
    "au": ["AO"],
    "aw": ["AO"],
    "ee": ["IY"],
    "ea": ["IY"],
    "ei": ["EY"],
    "oo": ["UW"],
    "ou": ["AW"],
    "ow": ["OW"],
    "oi": ["OY"],
    "oy": ["OY"],
    "er": ["ER"],
    "ir": ["ER"],
    "ur": ["ER"],
    "or": ["AO", "R"],
    "ar": ["AA", "R"],
}
CONS_DIGRAPHS = {
    "ch": ["CH"],
    "sh": ["SH"],
    "th": ["TH"],
    "ph": ["F"],
    "ng": ["NG"],
    "wh": ["W"],
    "ck": ["K"],
    "qu": ["K", "W"],
    "wr": ["R"],
    "kn": ["N"],
}
CONS = {
    "b": "B",
    "c": "K",
    "d": "D",
    "f": "F",
    "g": "G",
    "h": "HH",
    "j": "JH",
    "k": "K",
    "l": "L",
    "m": "M",
    "n": "N",
    "p": "P",
    "q": "K",
    "r": "R",
    "s": "S",
    "t": "T",
    "v": "V",
    "w": "W",
    "x": "KS",
    "y": "Y",
    "z": "Z",
}
VOWELS = {"a": "AE", "e": "EH", "i": "IH", "o": "AA", "u": "AH", "y": "IY"}
VOWEL_NEAR = {"AE", "AA", "AO", "AH", "UW"}

AUTO_SCORE = 0.84
AUTO_MARGIN = 0.15
SHOW_BAND = 0.12
SHOW_FLOOR = 0.45
WEAK_FLOOR = 0.40
MAX_CARDS = 4
MISHEAR_FLOOR = 0.80
MEMORY_BONUS = 0.4
INSERT_COST = 0.85


def load_vocab(path=VOCAB_PATH):
    data = json.loads(Path(path).read_text())
    swaps = {frozenset(pair) for pair in data["swaps"]}
    places = []
    for raw in data["places"]:
        match = raw["match"]
        place = {
            "id": raw["id"],
            "name": raw["name"],
            "match": match,
            "lat": raw["lat"],
            "lon": raw["lon"],
            "chain": raw.get("chain"),
            "spelling_label": raw.get("spelling_label"),
            "local_label": raw.get("local_label"),
            "local_as": raw.get("local_as") or "",
            "mishears": _mishear_keys(raw.get("mishears") or []),
            "spelling_phones": word_phones(match),
            "local_phones": word_phones(raw.get("local_as") or match),
        }
        places.append(place)
    return {
        "origin": data["origin"],
        "swaps": swaps,
        "places": places,
    }


def word_phones(text):
    cleaned = re.sub(r"[^a-z ]+", " ", text.lower())
    phones = []
    for word in cleaned.split():
        phones.extend(_word(word))
    return phones


def _word(word):
    phones = []
    i = 0
    while i < len(word):
        if i == len(word) - 1 and word[i] == "e" and phones:
            break
        pair = word[i : i + 2]
        if pair in CONS_DIGRAPHS:
            phones.extend(CONS_DIGRAPHS[pair])
            i += 2
            continue
        if pair in VOWEL_DIGRAPHS:
            phones.extend(VOWEL_DIGRAPHS[pair])
            i += 2
            continue
        ch = word[i]
        if i + 1 < len(word) and word[i + 1] == ch and ch not in VOWELS:
            i += 1
            continue
        if ch in VOWELS:
            phones.append(VOWELS[ch])
        elif ch in CONS:
            token = CONS[ch]
            phones.extend(token.split())
        i += 1
    return phones


def _mishear_keys(items):
    keys = set()
    for item in items:
        key = normalize_heard(item)
        if key:
            keys.add(key)
    return keys


def sub_cost(a, b, swaps):
    if a == b:
        return 0.0
    if frozenset((a, b)) in swaps:
        return 0.28
    if a in VOWEL_NEAR and b in VOWEL_NEAR:
        return 0.4
    return 1.0


def distance(a, b, swaps):
    if not a:
        return INSERT_COST * len(b)
    if not b:
        return INSERT_COST * len(a)
    prev = [INSERT_COST * j for j in range(len(b) + 1)]
    for i, left in enumerate(a, start=1):
        cur = [INSERT_COST * i]
        for j, right in enumerate(b, start=1):
            cur.append(
                min(
                    prev[j] + INSERT_COST,
                    cur[j - 1] + INSERT_COST,
                    prev[j - 1] + sub_cost(left, right, swaps),
                )
            )
        prev = cur
    return prev[-1]


def similarity(heard, target, swaps):
    if not heard or not target:
        return 0.0
    dist = distance(heard, target, swaps)
    return max(0.0, 1.0 - dist / len(heard))


def substitutions(a, b, swaps):
    """Pairs of phones that were swapped, from the best alignment."""
    n, m = len(a), len(b)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = INSERT_COST * i
    for j in range(1, m + 1):
        dp[0][j] = INSERT_COST * j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(
                dp[i - 1][j] + INSERT_COST,
                dp[i][j - 1] + INSERT_COST,
                dp[i - 1][j - 1] + sub_cost(a[i - 1], b[j - 1], swaps),
            )
    pairs = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            cost = sub_cost(a[i - 1], b[j - 1], swaps)
            if abs(dp[i][j] - (dp[i - 1][j - 1] + cost)) < 1e-6:
                if a[i - 1] != b[j - 1]:
                    pairs.append((a[i - 1], b[j - 1]))
                i -= 1
                j -= 1
                continue
        if i > 0 and abs(dp[i][j] - (dp[i - 1][j] + INSERT_COST)) < 1e-6:
            i -= 1
            continue
        j -= 1
    return pairs


def haversine_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


def strip_carrier(text):
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for prefix in ("take me to ", "take me ", "bring me to ", "go to "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = cleaned.strip()
    for prefix in ("the ", "a "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned.strip()


def normalize_heard(text):
    return strip_carrier(text)


def letter_similarity(query, name):
    a = re.sub(r"[^a-z]", "", query.lower())
    b = re.sub(r"[^a-z]", "", name.lower())
    if not a or not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        cur = [i]
        for j, right in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (left != right)))
        prev = cur
    return max(0.0, 1.0 - prev[-1] / max(len(a), len(b)))


def _letters(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def search_places(vocab, query, limit=6):
    """Rank known places by prefix, then by the letters they share."""
    needle = _letters(query or "")
    if len(needle) < 2:
        return []
    ranked = []
    for place in vocab["places"]:
        keys = {_letters(place["name"]), _letters(place.get("match") or "")}
        keys.discard("")
        best = None
        for key in keys:
            if key.startswith(needle):
                tier = 0
            elif needle in key:
                tier = 1
            else:
                shared = letter_similarity(needle, key)
                if shared < 0.45:
                    continue
                tier = 2
            shared = letter_similarity(needle, key)
            score = (tier, -shared, place["name"])
            if best is None or score < best:
                best = score
        if best is not None:
            ranked.append((best, place))
    ranked.sort(key=lambda item: item[0])
    return [
        {"id": place["id"], "name": place["name"], "lat": place["lat"], "lon": place["lon"]}
        for _, place in ranked[:limit]
    ]


def chain_prefix(transcript, vocab, location):
    """Stores in the area when the heard words start a chain name.

    "whole foods" is the start of "Whole Foods Market", but the missing word
    makes the sound score lose to shorter town names. Show the chain instead.
    """
    heard_words = strip_carrier(transcript).split()
    if len(heard_words) < 2:
        return []
    by_chain = {}
    for place in vocab["places"]:
        chain = place.get("chain")
        if not chain:
            continue
        match_words = strip_carrier(place["match"]).split()
        if match_words[: len(heard_words)] != heard_words:
            continue
        km = haversine_km(location["lat"], location["lon"], place["lat"], place["lon"])
        by_chain.setdefault(chain, []).append((km, place))
    if not by_chain:
        return []
    chain = min(by_chain, key=lambda name: min(km for km, _place in by_chain[name]))
    nearest = sorted(by_chain[chain], key=lambda item: item[0])
    if len(nearest) < 2:
        return []
    return [{"place": place, "score": 1.0} for _km, place in nearest[:MAX_CARDS]]


def spoken_prefix_place(transcript, vocab, location):
    """Town whose name starts with the heard word, shortest extension first."""
    query = re.sub(r"[^a-z]", "", strip_carrier(transcript))
    if len(query) < 3:
        return None
    hits = []
    for place in vocab["places"]:
        name = re.sub(r"[^a-z]", "", (place.get("match") or "").lower())
        if not name.startswith(query) or name == query:
            continue
        km = haversine_km(location["lat"], location["lon"], place["lat"], place["lon"])
        hits.append((len(name) - len(query), km, place))
    if not hits:
        return None
    hits.sort(key=lambda item: (item[0], item[1]))
    return hits[0][2]


def plain_lookup(transcript, vocab, location):
    query = strip_carrier(transcript)
    best = None
    best_score = -1.0
    for place in vocab["places"]:
        score = letter_similarity(query, place["match"])
        km = haversine_km(location["lat"], location["lon"], place["lat"], place["lon"])
        score += 0.02 * max(0.0, 1.0 - km / 120.0)
        if score > best_score:
            best_score = score
            best = place
    return best


def _rank(transcript, vocab, location, memory_place):
    heard_key = strip_carrier(transcript)
    heard_phones = word_phones(heard_key)
    ranked = []
    for place in vocab["places"]:
        spelling = similarity(heard_phones, place["spelling_phones"], vocab["swaps"])
        local = similarity(heard_phones, place["local_phones"], vocab["swaps"])
        if local >= spelling:
            saying, score = "local", local
        else:
            saying, score = "spelling", spelling
        raw_score = score
        mishear = heard_key in place["mishears"]
        if mishear:
            score = max(score, MISHEAR_FLOOR)
        km = haversine_km(location["lat"], location["lon"], place["lat"], place["lon"])
        boosted = bool(memory_place and memory_place == place["id"])
        rank_score = score + (MEMORY_BONUS if boosted else 0.0)
        ranked.append(
            {
                "place": place,
                "saying": saying,
                "score": round(score, 4),
                "raw_score": raw_score,
                "mishear": mishear,
                "rank_score": rank_score,
                "boosted": boosted,
                "km": round(km, 1),
            }
        )
    ranked.sort(key=lambda row: (row["rank_score"], -row["km"]), reverse=True)
    return heard_phones, ranked


def _place_card(place, score=0):
    return {
        "id": place["id"],
        "name": place["name"],
        "lat": place["lat"],
        "lon": place["lon"],
        "score": score,
    }


def _mishear_asks(row):
    """A listed collapse asks. A local pronunciation that already scores clearly does not."""
    return bool(row["mishear"] and row["raw_score"] < AUTO_SCORE)


def _select_cards(ranked, winner):
    score = winner["score"]
    if score < WEAK_FLOOR:
        return []
    if score < SHOW_FLOOR:
        return list(ranked[:3])
    chosen = []
    for row in ranked:
        if row["score"] >= SHOW_FLOOR or winner["score"] - row["score"] <= SHOW_BAND:
            chosen.append(row)
        if len(chosen) >= MAX_CARDS:
            break
    return chosen


def _insert_letter(cards, letter_place):
    if not letter_place:
        return cards
    if any(row["place"]["id"] == letter_place["id"] for row in cards):
        return cards
    row = {
        "place": letter_place,
        "saying": "spelling",
        "score": 0,
        "raw_score": 0,
        "mishear": False,
        "rank_score": 0,
        "boosted": False,
        "km": 0,
    }
    if not cards:
        return [row]
    return (cards[:1] + [row] + cards[1:])[:MAX_CARDS]


def _merge_later(cards, later_ranked, chain):
    """Add a later guess's own near misses. Do not pull in the rest of that list."""
    seen = {row["place"]["id"] for row in cards}
    for ranked in later_ranked:
        if not ranked:
            continue
        top_score = ranked[0]["score"]
        for row in ranked:
            if row["score"] < SHOW_FLOOR or top_score - row["score"] > SHOW_BAND:
                break
            if row["place"]["id"] in seen:
                continue
            if chain and row["place"].get("chain") != chain:
                continue
            cards.append(row)
            seen.add(row["place"]["id"])
            if len(cards) >= MAX_CARDS:
                return cards
    return cards


def match(transcripts, vocab, location, memory_place=None, memory_kind=None):
    """Score the first guess, then keep its near misses. Later guesses can add a place."""
    texts = [row["text"] if isinstance(row, dict) else row for row in transcripts]
    texts = [text for text in texts if text and text.strip()]
    if not texts:
        return _empty("")
    top = texts[0]
    bonus_place = None if memory_kind == "correction" else memory_place
    best_phones, ranked = _rank(top, vocab, location, bonus_place)
    if not ranked:
        return _empty(top)
    later = []
    for text in texts[1:]:
        _, other = _rank(text, vocab, location, bonus_place)
        if other:
            later.append(other)
    winner = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    margin = winner["rank_score"] - (runner["rank_score"] if runner else 0.0)
    same_chain = bool(
        runner
        and winner["place"].get("chain")
        and winner["place"]["chain"] == runner["place"].get("chain")
    )
    from_memory = bool(winner["boosted"] and margin >= AUTO_MARGIN and winner["score"] >= SHOW_FLOOR)
    forced_ask = _mishear_asks(winner)
    clear = winner["score"] >= AUTO_SCORE and margin >= AUTO_MARGIN
    prefix_chain = chain_prefix(top, vocab, location)

    def card(row):
        return _place_card(row["place"], row["score"])

    place_payload = None
    chosen = []
    if prefix_chain and not from_memory:
        outcome = "ask"
        chosen = prefix_chain
    elif winner["score"] < WEAK_FLOOR and not from_memory:
        outcome = "none"
    elif from_memory:
        outcome = "place"
        place_payload = card(winner)
    elif same_chain or forced_ask or not clear:
        outcome = "ask"
        if same_chain and not forced_ask:
            chain = winner["place"]["chain"]
            chosen = [row for row in ranked if row["place"].get("chain") == chain][:MAX_CARDS]
            chosen = _merge_later(chosen, later, chain)
        else:
            chosen = _select_cards(ranked, winner)
            literal = spoken_prefix_place(top, vocab, location) or plain_lookup(top, vocab, location)
            chosen = _insert_letter(chosen, literal)
            chosen = _merge_later(chosen, later, None)
        if not chosen:
            outcome = "none"
    else:
        outcome = "place"
        place_payload = card(winner)

    cards = [card(row) for row in chosen]
    label_place = winner["place"]
    if memory_kind == "correction" and memory_place:
        remembered = next((place for place in vocab["places"] if place["id"] == memory_place), None)
        shown = []
        if outcome == "place" and place_payload:
            shown = [place_payload["id"]]
        elif outcome == "ask":
            shown = [item["id"] for item in cards]
        if remembered and remembered["id"] not in shown:
            extra = _place_card(remembered)
            if outcome == "none" or not cards and outcome != "place":
                outcome = "place"
                place_payload = extra
                label_place = remembered
            elif outcome == "place":
                outcome = "ask"
                cards = [place_payload, extra]
                place_payload = None
            else:
                cards = cards + [extra]
    saying = winner["saying"]
    label_key = "local_label" if saying == "local" else "spelling_label"
    return {
        "top_transcript": top,
        "heard": normalize_heard(top) or top,
        "alternatives": [normalize_heard(text) or text for text in texts[1:]],
        "matched_saying": saying if outcome != "none" else None,
        "matched_pronunciation": label_place.get(label_key) if outcome != "none" else None,
        "spelling_label": label_place.get("spelling_label"),
        "local_label": label_place.get("local_label"),
        "score": winner["score"],
        "from_memory": from_memory,
        "outcome": outcome,
        "place": place_payload,
        "cards": cards,
        "phones": best_phones,
    }


def _empty(top):
    return {
        "top_transcript": top,
        "heard": normalize_heard(top) if top else "",
        "alternatives": [],
        "matched_saying": None,
        "matched_pronunciation": None,
        "spelling_label": None,
        "local_label": None,
        "score": 0,
        "from_memory": False,
        "outcome": "none",
        "place": None,
        "cards": [],
        "phones": [],
    }


def public_result(result):
    """Drop phones from the API payload. The UI does not show them."""
    view = dict(result)
    view.pop("phones", None)
    return view


def _names(result):
    if result["place"]:
        return [result["place"]["name"]]
    return [card["name"] for card in result["cards"]]


def _ids(result):
    if result["place"]:
        return [result["place"]["id"]]
    return [card["id"] for card in result["cards"]]


def _expect(failures, result, label, outcome, first_id=None, includes=(), exact_ids=None):
    ids = _ids(result)
    problems = []
    if result["outcome"] != outcome:
        problems.append("outcome %s" % result["outcome"])
    if first_id and (not ids or ids[0] != first_id):
        problems.append("first %s" % (ids[:1] or ["none"]))
    for place_id in includes:
        if place_id not in ids:
            problems.append("missing %s" % place_id)
    if exact_ids is not None and ids != exact_ids:
        problems.append("ids %s" % ids)
    where = _names(result)
    print(f"{label!r:28} {result['outcome']:6} {where} {result['score']}")
    if problems:
        failures.append("%s: %s" % (label, ", ".join(problems)))


if __name__ == "__main__":
    vocab = load_vocab()
    origin = vocab["origin"]
    failures = []
    samples = [
        ("wooster", "place", "worcester-ma"),
        ("worcester", "place", "worcester-ma"),
        ("take me to worcester", "place", "worcester-ma"),
        ("glawster", "place", "gloucester-ma"),
        ("gloucester", "place", "gloucester-ma"),
        ("sous church", "place", "south-church-andover"),
        ("south church", "place", "south-church-andover"),
        ("watertown", "place", "watertown-ma"),
        ("take me to boston", "place", "boston-ma"),
        ("boston", "place", "boston-ma"),
        ("lemonster", "place", "leominster-ma"),
        ("lynn", "place", "lynn-ma"),
    ]
    for text, outcome, place_id in samples:
        result = match([{"text": text}], vocab, origin)
        _expect(failures, result, text, outcome, place_id)
    for text in ("water", "the water"):
        result = match([{"text": text}], vocab, origin)
        _expect(
            failures,
            result,
            text,
            "ask",
            "worcester-ma",
            ("worcester-ma", "watertown-ma"),
        )
        if len(result["cards"]) < 2:
            failures.append("%s: expected at least two cards" % text)
    whats = match([{"text": "what's the"}], vocab, origin)
    _expect(failures, whats, "what's the", "ask", "worcester-ma", ("worcester-ma",))
    if len(whats["cards"]) < 2:
        failures.append("what's the: expected at least two cards")
    both = match([{"text": "water"}, {"text": "Watertown"}], vocab, origin)
    _expect(failures, both, "water + Watertown", "ask", "worcester-ma", ("worcester-ma",))
    foods = match([{"text": "whole poods market"}], vocab, origin)
    _expect(
        failures,
        foods,
        "whole poods market",
        "ask",
        exact_ids=["whole-foods-andover", "whole-foods-lynnfield"],
    )
    foods_clear = match([{"text": "whole foods market"}], vocab, origin)
    _expect(
        failures,
        foods_clear,
        "whole foods market",
        "ask",
        exact_ids=["whole-foods-andover", "whole-foods-lynnfield"],
    )
    foods_short = match([{"text": "whole foods"}], vocab, origin)
    _expect(
        failures,
        foods_short,
        "whole foods",
        "ask",
        exact_ids=["whole-foods-andover", "whole-foods-lynnfield"],
    )
    foods_take = match([{"text": "take me to whole foods"}], vocab, origin)
    _expect(
        failures,
        foods_take,
        "take me to whole foods",
        "ask",
        exact_ids=["whole-foods-andover", "whole-foods-lynnfield"],
    )
    remembered = match([{"text": "whole poods market"}], vocab, origin, "whole-foods-andover")
    print("remembered", remembered["outcome"], remembered["from_memory"], _names(remembered))
    if remembered["outcome"] != "place" or not remembered["from_memory"]:
        failures.append("remembered whole foods did not open Andover")
    elif remembered["place"]["id"] != "whole-foods-andover":
        failures.append("remembered place %s" % remembered["place"]["id"])
    if failures:
        print("FAILED")
        for item in failures:
            print(" -", item)
        raise SystemExit(1)
    print("ok")
