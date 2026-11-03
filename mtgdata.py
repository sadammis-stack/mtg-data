#!/usr/bin/env python3
"""
mtgdata.py — Scryfall-kortdata via GitHub-spejlet (ingen direkte Scryfall-kald).

  python3 mtgdata.py info                         dato for data + antal
  python3 mtgdata.py card NAVN [NAVN ...] [--json] oracle-tekst, legalitet, priser
  python3 mtgdata.py prints NAVN                  alle papirprints med Cardmarket trend
  python3 mtgdata.py set SET NR                   ét print ud fra set-kode + collector number
  python3 mtgdata.py cm ID [ID ...]               print ud fra Cardmarket idProduct
  python3 mtgdata.py rulings NAVN                 officielle rulings
  python3 mtgdata.py search REGEX [--ci UR] [--type Instant] [--max-cmc 2] [--commander]
  python3 mtgdata.py deck FIL [-o ud.csv] [--skip-basics]
                                                  dekliste → oracle + pris pr. kort + total

Kan også importeres:  from mtgdata import find, prints_for, by_set, by_cardmarket, search

Miljøvariabler:
  MTGDATA_REPO   ejer/repo med data-branchen (standard: REPO herunder)
  MTGDATA_URL    fuld base-URL eller lokal mappe (overstyrer REPO)
  MTGDATA_CACHE  cache-mappe (standard ~/.cache/mtgdata)
"""
import argparse
import csv
import difflib
import gzip
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

REPO = os.environ.get("MTGDATA_REPO", "CHANGE-ME/mtg-data")
BASE = os.environ.get("MTGDATA_URL") or f"https://raw.githubusercontent.com/{REPO}/data"
CACHE = Path(os.environ.get("MTGDATA_CACHE", Path.home() / ".cache" / "mtgdata"))
RECHECK_SECONDS = 6 * 3600
NON_GAME = {"token", "double_faced_token", "emblem", "art_series", "planar", "scheme", "vanguard"}
BASICS = {"plains", "island", "swamp", "mountain", "forest", "wastes",
          "snowcovered plains", "snowcovered island", "snowcovered swamp",
          "snowcovered mountain", "snowcovered forest"}


# ---------------------------------------------------------------- hentning + cache

def _is_local():
    return not re.match(r"https?://", BASE)


def _fetch(name):
    if _is_local():
        return Path(BASE, name).read_bytes()
    req = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": "mtgdata/1.0"})
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"Kunne ikke hente {BASE}/{name}: {last}")


def _write_atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


_checked = False


def _sync():
    """Henter meta.json (højst hver 6. time); nye data → gamle cachefiler slettes."""
    global _checked
    if _checked:
        return
    _checked = True
    CACHE.mkdir(parents=True, exist_ok=True)
    meta_p, stamp = CACHE / "meta.json", CACHE / ".checked"
    fresh = stamp.exists() and time.time() - stamp.stat().st_mtime < RECHECK_SECONDS
    if fresh and meta_p.exists() and not _is_local():
        return
    try:
        new = _fetch("meta.json")
    except Exception as e:  # noqa: BLE001
        if meta_p.exists():
            print(f"[mtgdata] advarsel: bruger cache, kunne ikke tjekke for nye data ({e})",
                  file=sys.stderr)
            return
        raise
    old = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    if json.loads(new).get("built_at") != old.get("built_at"):
        for f in ("oracle.jsonl.gz", "prints.csv.gz", "rulings.jsonl.gz"):
            (CACHE / f).unlink(missing_ok=True)
        _write_atomic(meta_p, new)
    stamp.touch()


def ensure(name):
    _sync()
    path = CACHE / name
    if not path.exists():
        _write_atomic(path, _fetch(name))
    return path


def meta():
    _sync()
    return json.loads((CACHE / "meta.json").read_text())


# ---------------------------------------------------------------- navne

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("’", "'").replace("‘", "'").lower()
    s = s.replace("æ", "ae")
    s = re.split(r"\s*//\s*", s)[0]
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- oracle

_oracle = None


def oracle():
    """(liste af kort, indeks normaliseret navn → kort)."""
    global _oracle
    if _oracle is None:
        with gzip.open(ensure("oracle.jsonl.gz"), "rt", encoding="utf-8") as f:
            cards = [json.loads(line) for line in f]
        idx = {}
        for c in cards:
            keys = {norm(c["name"])} | {norm(x.get("name", "")) for x in c.get("faces", [])}
            for k in keys:
                cur = idx.get(k)
                # spillekort går forud for tokens/emblems med samme navn
                if cur is None or (cur.get("layout") in NON_GAME and c.get("layout") not in NON_GAME):
                    idx[k] = c
        _oracle = (cards, idx)
    return _oracle


def find(name):
    """Returnerer (kort, 'exact'|'fuzzy') eller (None, None)."""
    _, idx = oracle()
    k = norm(name)
    if k in idx:
        return idx[k], "exact"
    if len(k) >= 4:  # entydigt præfiks, fx "delver" → Delver of Secrets
        hits = {id(c): c for key, c in idx.items() if key.startswith(k) and c.get("layout") not in NON_GAME}
        if len(hits) == 1:
            return next(iter(hits.values())), "fuzzy"
    m = difflib.get_close_matches(k, idx.keys(), n=1, cutoff=0.82)
    return (idx[m[0]], "fuzzy") if m else (None, None)


def search(pattern, ci=None, type_=None, max_cmc=None, commander=False, include_tokens=False):
    cards, _ = oracle()
    rx = re.compile(pattern, re.I)
    allowed = set(ci.upper()) if ci else None
    trx = re.compile(type_, re.I) if type_ else None
    for c in cards:
        if not include_tokens and c.get("layout") in NON_GAME:
            continue
        if allowed is not None and not set(c.get("color_identity") or []) <= allowed:
            continue
        if trx and not trx.search(c.get("type_line", "")):
            continue
        if max_cmc is not None and (c.get("cmc") or 0) > max_cmc:
            continue
        if commander and c.get("commander") != "legal":
            continue
        hay = "\n".join([c["name"], c.get("type_line", ""), c.get("oracle_text", "")])
        if rx.search(hay):
            yield c


# ---------------------------------------------------------------- prints

_prints = None


def _load_prints():
    global _prints
    if _prints is None:
        with gzip.open(ensure("prints.csv.gz"), "rt", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        by_oid, by_set, by_cm = {}, {}, {}
        for r in rows:
            by_oid.setdefault(r["oracle_id"], []).append(r)
            by_set[(r["set"].lower(), r["collector_number"].lower())] = r
            if r["cardmarket_id"]:
                by_cm.setdefault(r["cardmarket_id"], r)
        _prints = (by_oid, by_set, by_cm)
    return _prints


def _f(v):
    return float(v) if v not in (None, "") else None


def prints_for(name_or_card):
    card = name_or_card if isinstance(name_or_card, dict) else find(name_or_card)[0]
    if not card:
        return []
    rows = _load_prints()[0].get(card.get("oracle_id"), [])
    return sorted(rows, key=lambda r: (_f(r["eur"]) is None, _f(r["eur"]) or 0))


def by_set(set_code, number):
    return _load_prints()[1].get((set_code.lower(), str(number).lower()))


def by_cardmarket(cm_id):
    return _load_prints()[2].get(str(cm_id).strip())


def rulings(name):
    card, _ = find(name)
    if not card:
        return None, []
    with gzip.open(ensure("rulings.jsonl.gz"), "rt", encoding="utf-8") as f:
        for line in f:
            if card["oracle_id"] in line:
                r = json.loads(line)
                if r["oracle_id"] == card["oracle_id"]:
                    return card, r["rulings"]
    return card, []


# ---------------------------------------------------------------- dekliste

LINE_RX = re.compile(
    r"^\s*(?:(?P<qty>\d+)\s*[xX]?\s+)?(?P<name>.+?)"
    r"(?:\s+[\(\[](?P<set>[A-Za-z0-9]{2,6})[\)\]](?:\s+(?P<cn>[^\s*]+))?)?"
    r"(?:\s+\*(?P<flag>[A-Za-z]+)\*)*\s*$")
SKIP_SECTIONS = ("maybe", "sideboard", "considering", "tokens")
MAIN_SECTIONS = ("main", "commander", "deck", "companion")


def parse_deck(text):
    """Returnerer liste af dicts: qty, name, set, cn, foil. Ignorerer maybe-/sideboard."""
    out, active = [], True
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower().lstrip("/#").strip().rstrip(":").strip()
        is_header = line.startswith(("//", "#")) or (
            not re.match(r"^\d", line) and low.split(" ")[0] in SKIP_SECTIONS + MAIN_SECTIONS
            and len(low.split()) <= 2)
        if is_header:
            if any(s in low for s in SKIP_SECTIONS):
                active = False
            elif any(s in low for s in MAIN_SECTIONS):
                active = True
            continue
        if not active:
            continue
        m = LINE_RX.match(line)
        if not m:
            continue
        out.append({
            "qty": int(m["qty"] or 1), "name": m["name"].strip(),
            "set": m["set"], "cn": m["cn"], "foil": bool(m["flag"] and m["flag"].upper() in ("F", "E")),
        })
    return out


def price_deck(entries, skip_basics=False):
    rows = []
    for e in entries:
        card, how = find(e["name"])
        row = {"qty": e["qty"], "input": e["name"], "match": how or "IKKE FUNDET"}
        if card:
            row.update({
                "name": card["name"], "mana_cost": card.get("mana_cost", ""), "cmc": card.get("cmc"),
                "type_line": card.get("type_line", ""),
                "color_identity": "".join(card.get("color_identity") or []),
                "commander": card.get("commander", ""),
                "game_changer": "ja" if card.get("game_changer") else "",
                "eur_min": card.get("eur_min"), "eur_min_print": card.get("eur_min_print", ""),
                "eur_default": card.get("eur_default"),
            })
            unit = card.get("eur_min")
            if e["set"]:
                p = by_set(e["set"], e["cn"]) if e["cn"] else None
                if p:
                    v = _f(p["eur_foil"] if e["foil"] else p["eur"])
                    row["your_print"] = f"{p['set'].upper()} {p['collector_number']}{' foil' if e['foil'] else ''}"
                    row["your_print_eur"] = v
            if skip_basics and norm(card["name"]) in BASICS:
                unit = 0.0
            row["line_eur_min"] = round(unit * e["qty"], 2) if unit is not None else None
        rows.append(row)
    return rows


# ---------------------------------------------------------------- udskrift

def _eur(v):
    return f"€{v:,.2f}" if isinstance(v, (int, float)) else "–"


def show_card(c, how=None):
    pt = f"  {c['power']}/{c['toughness']}" if "power" in c else ""
    loy = f"  Loyalty {c['loyalty']}" if "loyalty" in c else ""
    flag = "  (fuzzy match)" if how == "fuzzy" else ""
    print(f"{c['name']}  {c.get('mana_cost', '')}  (MV {c.get('cmc', 0):g}){flag}")
    print(f"{c.get('type_line', '')}{pt}{loy}")
    if c.get("faces"):
        for fc in c["faces"]:
            fpt = f"  {fc['power']}/{fc['toughness']}" if "power" in fc else ""
            print(f"  ▸ {fc.get('name')}  {fc.get('mana_cost', '')}  — {fc.get('type_line', '')}{fpt}")
            for ln in (fc.get("oracle_text") or "").splitlines():
                print(f"    {ln}")
    else:
        print(c.get("oracle_text", ""))
    ci = "".join(c.get("color_identity") or []) or "farveløs"
    print(f"CI: {ci} · Commander: {c.get('commander', '?')} · "
          f"Game Changer: {'ja' if c.get('game_changer') else 'nej'}"
          f"{' · Reserved List' if c.get('reserved') else ''}"
          f"{' · EDHREC #' + str(c['edhrec_rank']) if c.get('edhrec_rank') else ''}")
    print(f"Cardmarket trend: billigst {_eur(c.get('eur_min'))} ({c.get('eur_min_print', '–')}) · "
          f"foil {_eur(c.get('eur_foil_min'))} ({c.get('eur_foil_min_print', '–')}) · "
          f"standardprint {_eur(c.get('eur_default'))} · {c.get('prints', 0)} prints")
    print(c.get("uri", ""))


def show_print(p):
    print(f"{p['name']} — {p['set'].upper()} {p['collector_number']} ({p['set_name']}, {p['rarity']}, "
          f"{p['released_at']})  finishes: {p['finishes']}")
    print(f"  Cardmarket trend: {_eur(_f(p['eur']))} · foil {_eur(_f(p['eur_foil']))}"
          f"{' · etched ' + _eur(_f(p['eur_etched'])) if p['eur_etched'] else ''}"
          f"  · idProduct {p['cardmarket_id'] or '–'} · scryfall {p['scryfall_id']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Scryfall-kortdata via GitHub-spejl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    s = sub.add_parser("card"); s.add_argument("names", nargs="+"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("prints"); s.add_argument("name")
    s = sub.add_parser("set"); s.add_argument("set"); s.add_argument("number")
    s = sub.add_parser("cm"); s.add_argument("ids", nargs="+")
    s = sub.add_parser("rulings"); s.add_argument("name")
    s = sub.add_parser("search"); s.add_argument("pattern")
    s.add_argument("--ci"); s.add_argument("--type"); s.add_argument("--max-cmc", type=float)
    s.add_argument("--commander", action="store_true"); s.add_argument("--limit", type=int, default=100)
    s = sub.add_parser("deck"); s.add_argument("file"); s.add_argument("-o", "--out")
    s.add_argument("--skip-basics", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "info":
        m = meta()
        print(json.dumps(m, indent=2))
    elif a.cmd == "card":
        found = []
        for i, n in enumerate(a.names):
            c, how = find(n)
            if not c:
                print(f"IKKE FUNDET: {n}", file=sys.stderr)
                continue
            found.append(c)
            if not a.json:
                if i:
                    print("-" * 60)
                show_card(c, how)
        if a.json:
            print(json.dumps(found, ensure_ascii=False, indent=1))
    elif a.cmd == "prints":
        c, _ = find(a.name)
        if not c:
            sys.exit(f"IKKE FUNDET: {a.name}")
        print(f"{c['name']} — {c.get('prints', 0)} papirprints (sorteret efter EUR trend)")
        for p in prints_for(c):
            print(f"  {p['set'].upper():>6} {p['collector_number']:<8} {p['rarity'][:1].upper()} "
                  f"{_eur(_f(p['eur'])):>9}  foil {_eur(_f(p['eur_foil'])):>9}  "
                  f"idProduct {p['cardmarket_id'] or '–':<8} {p['set_name']}")
    elif a.cmd == "set":
        p = by_set(a.set, a.number)
        show_print(p) if p else sys.exit(f"IKKE FUNDET: {a.set} {a.number}")
    elif a.cmd == "cm":
        for i in a.ids:
            p = by_cardmarket(i)
            show_print(p) if p else print(f"IKKE FUNDET: idProduct {i}", file=sys.stderr)
    elif a.cmd == "rulings":
        c, rs = rulings(a.name)
        if not c:
            sys.exit(f"IKKE FUNDET: {a.name}")
        print(f"{c['name']} — {len(rs)} rulings")
        for r in rs:
            print(f"  [{r['date']}] {r['text']}")
    elif a.cmd == "search":
        n = 0
        for c in search(a.pattern, a.ci, a.type, a.max_cmc, a.commander):
            n += 1
            if n <= a.limit:
                print(f"{c['name']:<40} {c.get('mana_cost', ''):<14} {c.get('type_line', '')}  "
                      f"{_eur(c.get('eur_min'))}{'  [GC]' if c.get('game_changer') else ''}")
        print(f"— {n} kort" + (f" (viser {a.limit})" if n > a.limit else ""))
    elif a.cmd == "deck":
        entries = parse_deck(Path(a.file).read_text(encoding="utf-8-sig"))
        rows = price_deck(entries, a.skip_basics)
        total = sum(r.get("line_eur_min") or 0 for r in rows)
        cards = sum(r["qty"] for r in rows)
        missing = [r["input"] for r in rows if r["match"] == "IKKE FUNDET"]
        fuzzy = [f"{r['input']} → {r['name']}" for r in rows if r["match"] == "fuzzy"]
        gcs = sorted({r["name"] for r in rows if r.get("game_changer")})
        for r in sorted(rows, key=lambda r: -(r.get("line_eur_min") or 0)):
            print(f"{r['qty']:>3} {r.get('name', r['input']):<40} {_eur(r.get('eur_min')):>9}  "
                  f"{r.get('eur_min_print', '')}")
        print(f"\n{cards} kort · billigste-print-total {_eur(total)}"
              f"{' (basics ikke medregnet)' if a.skip_basics else ''}")
        print(f"Game Changers ({len(gcs)}): {', '.join(gcs) or '–'}")
        if fuzzy:
            print("Fuzzy matches: " + "; ".join(fuzzy))
        if missing:
            print("IKKE FUNDET: " + "; ".join(missing))
        if a.out:
            cols = ["qty", "input", "match", "name", "mana_cost", "cmc", "type_line", "color_identity",
                    "commander", "game_changer", "eur_min", "eur_min_print", "eur_default",
                    "your_print", "your_print_eur", "line_eur_min"]
            with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            print(f"CSV: {a.out}")


if __name__ == "__main__":
    main()
