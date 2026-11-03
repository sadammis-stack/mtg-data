#!/usr/bin/env python3
"""
build.py — henter Scryfalls bulk-data og bygger kompakte filer.

Køres dagligt af GitHub Actions (.github/workflows/scryfall.yml).
Kun Pythons standardbibliotek — ingen pip-installationer.

  python3 build.py OUTDIR                 # henter fra Scryfall
  python3 build.py OUTDIR --source DIR    # bruger lokale bulk-filer (test)

Output i OUTDIR:
  oracle.jsonl.gz   ét kort pr. linje (oracle-tekst, legalitet, game changer, prisoversigt)
  prints.csv.gz     ét papirprint pr. række (set, nummer, Cardmarket-id, EUR trend)
  rulings.jsonl.gz  officielle rulings grupperet pr. kort
  meta.json         tidsstempler og antal
  README.md         beskrivelse af filerne
"""
import argparse
import csv
import gzip
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

API = "https://api.scryfall.com/bulk-data"
UA = "mtg-data-mirror/1.0 (personal GitHub Actions mirror of Scryfall bulk data)"
TYPES = ("oracle_cards", "default_cards", "rulings")

# Prints der ikke tæller med i "billigste print" (guldkant, art cards, oversized m.m.)
EXCLUDE_FROM_MIN = {"memorabilia", "minigame"}
# Layouts der ikke er spillekort
NON_GAME_LAYOUTS = {"art_series"}

PRINT_COLS = [
    "name", "set", "set_name", "collector_number", "lang", "rarity", "released_at",
    "finishes", "eur", "eur_foil", "eur_etched", "usd", "usd_foil", "cardmarket_id",
    "scryfall_id", "oracle_id", "set_type", "promo", "promo_types", "frame_effects",
    "border_color", "full_art", "oversized",
]
WUBRG = "WUBRG"
CHUNK = 1 << 22  # tegn pr. læsning i iter_records


# ---------------------------------------------------------------- netværk

def http_open(url, accept="application/json", tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            return urllib.request.urlopen(req, timeout=180)
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            wait = 5 * 2 ** i
            print(f"  fejl ved {url}: {e} — prøver igen om {wait}s", file=sys.stderr)
            time.sleep(wait)


def download(url, dest, tries=3):
    for i in range(tries):
        try:
            with http_open(url, accept="*/*") as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            return
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            print(f"  download afbrudt ({e}) — prøver igen", file=sys.stderr)
            time.sleep(10)


def fetch_bulk(workdir):
    with http_open(API) as r:
        index = json.load(r)
    paths, updated = {}, {}
    for item in index.get("data", []):
        t = item.get("type")
        if t not in TYPES:
            continue
        # Nyere format: gzip-JSONL i jsonl_download_uri; ældre: JSON-array i download_uri
        url = item.get("jsonl_download_uri") or item.get("download_uri")
        dest = os.path.join(workdir, t)
        print(f"Henter {t} ({item.get('updated_at')}): {url}")
        download(url, dest)
        print(f"  {os.path.getsize(dest) / 1e6:.1f} MB")
        paths[t], updated[t] = dest, item.get("updated_at")
    missing = set(TYPES) - set(paths)
    if missing:
        sys.exit(f"Scryfall bulk-data mangler typer: {sorted(missing)}")
    return paths, updated


def local_bulk(source):
    paths, updated = {}, {}
    for t in TYPES:
        hits = sorted(f for f in os.listdir(source) if f.startswith(t))
        if not hits:
            sys.exit(f"Ingen fil for {t} i {source}")
        paths[t] = os.path.join(source, hits[0])
        updated[t] = "local"
    return paths, updated


# ---------------------------------------------------------------- parsing

def iter_records(path):
    """Streamer objekter fra JSON-array ELLER JSONL, gzip eller ej, uden at læse alt i RAM."""
    with open(path, "rb") as fh:
        gz = fh.read(2) == b"\x1f\x8b"
    opener = gzip.open if gz else open
    dec = json.JSONDecoder()
    skip = " \t\r\n,﻿"
    with opener(path, "rt", encoding="utf-8") as f:
        buf, pos, eof = "", 0, False
        while True:
            n = len(buf)
            while pos < n and buf[pos] in skip:
                pos += 1
            if pos < n and buf[pos] == "[":      # start på array
                pos += 1
                continue
            if pos < n and buf[pos] == "]":      # slut på array
                return
            if pos < n:
                try:
                    obj, end = dec.raw_decode(buf, pos)
                except json.JSONDecodeError:
                    if eof:
                        raise
                else:
                    pos = end
                    yield obj
                    continue
            elif eof:
                return
            chunk = f.read(CHUNK)
            eof = not chunk
            buf = buf[pos:] + chunk
            pos = 0


# ---------------------------------------------------------------- hjælpere

def oracle_id(c):
    if c.get("oracle_id"):
        return c["oracle_id"]
    for face in c.get("card_faces") or []:
        if face.get("oracle_id"):
            return face["oracle_id"]
    return None


def num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def b(v):
    return "1" if v else ""


def ordered_colors(cols):
    return [c for c in WUBRG if c in set(cols)]


def drop_empty(d):
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


# ---------------------------------------------------------------- byg

def build_prints(path, out):
    best = {}
    rows = 0
    with gzip.open(os.path.join(out, "prints.csv.gz"), "wt", newline="", encoding="utf-8") as g:
        w = csv.writer(g)
        w.writerow(PRINT_COLS)
        for c in iter_records(path):
            if c.get("object") != "card" or "paper" not in (c.get("games") or []):
                continue
            p = c.get("prices") or {}
            oid = oracle_id(c)
            w.writerow([
                c.get("name"), c.get("set"), c.get("set_name"), c.get("collector_number"),
                c.get("lang"), c.get("rarity"), c.get("released_at"),
                "|".join(c.get("finishes") or []),
                p.get("eur") or "", p.get("eur_foil") or "", p.get("eur_etched") or "",
                p.get("usd") or "", p.get("usd_foil") or "",
                c.get("cardmarket_id") or "", c.get("id"), oid or "",
                c.get("set_type"), b(c.get("promo")), "|".join(c.get("promo_types") or []),
                "|".join(c.get("frame_effects") or []), c.get("border_color"),
                b(c.get("full_art")), b(c.get("oversized")),
            ])
            rows += 1
            if not oid or c.get("oversized") or c.get("set_type") in EXCLUDE_FROM_MIN:
                continue
            s = best.setdefault(oid, {"prints": 0})
            s["prints"] += 1
            where = f"{c.get('set', '').upper()} {c.get('collector_number')}"
            for key, label in (("eur", "eur_min"), ("eur_foil", "eur_foil_min")):
                v = num(p.get(key))
                if v is not None and (s.get(label) is None or v < s[label]):
                    s[label], s[label + "_print"] = v, where
    return rows, best


def build_oracle(path, out, best):
    names = {}
    rows = 0
    with gzip.open(os.path.join(out, "oracle.jsonl.gz"), "wt", encoding="utf-8") as g:
        for c in iter_records(path):
            if c.get("object") != "card" or c.get("layout") in NON_GAME_LAYOUTS:
                continue
            oid = oracle_id(c)
            stats = best.get(oid, {})
            if "paper" not in (c.get("games") or []) and not stats:
                continue  # rene digitale kort (Alchemy m.m.)
            faces = c.get("card_faces") or []
            colors = c.get("colors")
            if colors is None and faces:
                colors = ordered_colors({x for f in faces for x in f.get("colors") or []})
            leg = c.get("legalities") or {}
            p = c.get("prices") or {}
            rec = {
                "name": c.get("name"),
                "oracle_id": oid,
                "layout": c.get("layout"),
                "mana_cost": c["mana_cost"] if "mana_cost" in c
                else " // ".join(f["mana_cost"] for f in faces if f.get("mana_cost")),
                "cmc": c.get("cmc"),
                "type_line": c.get("type_line")
                or " // ".join(f.get("type_line", "") for f in faces),
                "oracle_text": c["oracle_text"] if "oracle_text" in c
                else "\n//\n".join(f.get("oracle_text", "") for f in faces),
                "power": c.get("power"),
                "toughness": c.get("toughness"),
                "loyalty": c.get("loyalty"),
                "defense": c.get("defense"),
                "colors": colors,
                "color_identity": c.get("color_identity"),
                "keywords": c.get("keywords"),
                "produced_mana": c.get("produced_mana"),
                "commander": leg.get("commander"),
                "legalities": {k: v for k, v in leg.items() if v != "not_legal"},
                "game_changer": True if c.get("game_changer") else None,
                "reserved": True if c.get("reserved") else None,
                "edhrec_rank": c.get("edhrec_rank"),
                "eur_default": num(p.get("eur")),
                "eur_foil_default": num(p.get("eur_foil")),
                "eur_min": stats.get("eur_min"),
                "eur_min_print": stats.get("eur_min_print"),
                "eur_foil_min": stats.get("eur_foil_min"),
                "eur_foil_min_print": stats.get("eur_foil_min_print"),
                "prints": stats.get("prints"),
                "uri": f"https://scryfall.com/card/{c.get('set')}/{c.get('collector_number')}",
            }
            if faces:
                rec["faces"] = [drop_empty({
                    "name": f.get("name"), "mana_cost": f.get("mana_cost"),
                    "type_line": f.get("type_line"), "oracle_text": f.get("oracle_text"),
                    "power": f.get("power"), "toughness": f.get("toughness"),
                    "loyalty": f.get("loyalty"), "defense": f.get("defense"),
                    "colors": f.get("colors"),
                }) for f in faces]
            rec = {k: v for k, v in rec.items() if v not in (None, "", [], {})
                   or k in ("mana_cost", "colors", "color_identity")}
            g.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            names[oid] = c.get("name")
            rows += 1
    return rows, names


def build_rulings(path, out, names):
    grouped = {}
    for r in iter_records(path):
        oid = r.get("oracle_id")
        if not oid:
            continue
        grouped.setdefault(oid, []).append({
            "date": r.get("published_at"), "source": r.get("source"), "text": r.get("comment"),
        })
    with gzip.open(os.path.join(out, "rulings.jsonl.gz"), "wt", encoding="utf-8") as g:
        for oid, rs in grouped.items():
            if oid not in names:
                continue
            rs.sort(key=lambda x: x["date"] or "")
            g.write(json.dumps({"name": names[oid], "oracle_id": oid, "rulings": rs},
                               ensure_ascii=False, separators=(",", ":")) + "\n")
    return sum(1 for oid in grouped if oid in names)


DATA_README = """# Scryfall-data (kompakt spejl)

Bygget automatisk af GitHub Actions fra Scryfalls bulk-data — https://scryfall.com/docs/api/bulk-data
Kortdata og priser tilhører deres respektive ejere; data leveres af Scryfall.

| Fil | Indhold |
|---|---|
| `oracle.jsonl.gz` | Ét JSON-objekt pr. linje pr. kort (oracle_id): navn, mana, type, oracle-tekst, faces, farver, color identity, Commander-legalitet, game_changer, edhrec_rank, `eur_default`, `eur_min` + `eur_min_print` (billigste papirprint, Cardmarket trend), foil-tilsvarende, antal prints |
| `prints.csv.gz` | Ét papirprint pr. række: set, collector_number, rarity, finishes, `eur`/`eur_foil` (Cardmarket trend), `cardmarket_id` (= Cardmarket idProduct), scryfall_id, oracle_id m.m. |
| `rulings.jsonl.gz` | Officielle rulings pr. kort |
| `meta.json` | Byggetidspunkt, Scryfalls `updated_at` og antal |

Priser er Scryfalls daglige snapshot af Cardmarket trend (EUR) — ikke live.
Billed-URL for et print: `https://api.scryfall.com/cards/<scryfall_id>?format=image`
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--source", help="mappe med lokale bulk-filer (oracle_cards*, default_cards*, rulings*)")
    ap.add_argument("--no-sanity", action="store_true", help="spring minimumstjek over (til test)")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="scryfall-")
    try:
        paths, updated = local_bulk(a.source) if a.source else fetch_bulk(work)

        t0 = time.time()
        n_prints, best = build_prints(paths["default_cards"], a.out)
        print(f"prints:  {n_prints:>7} rækker  ({time.time() - t0:.0f}s)")
        n_oracle, names = build_oracle(paths["oracle_cards"], a.out, best)
        print(f"oracle:  {n_oracle:>7} kort")
        n_rulings = build_rulings(paths["rulings"], a.out, names)
        print(f"rulings: {n_rulings:>7} kort med rulings")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if not a.no_sanity and (n_oracle < 25000 or n_prints < 60000):
        sys.exit(f"Sanity-tjek fejlede (oracle={n_oracle}, prints={n_prints}) — publicerer ikke.")

    files = {f: os.path.getsize(os.path.join(a.out, f))
             for f in ("oracle.jsonl.gz", "prints.csv.gz", "rulings.jsonl.gz")}
    meta = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scryfall_updated_at": updated,
        "counts": {"oracle": n_oracle, "prints": n_prints, "rulings": n_rulings},
        "files": files,
        "source": "Scryfall bulk data — https://scryfall.com/docs/api/bulk-data",
    }
    with open(os.path.join(a.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(a.out, "README.md"), "w", encoding="utf-8") as f:
        f.write(DATA_README)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
