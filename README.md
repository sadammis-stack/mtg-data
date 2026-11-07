# Scryfall-data (kompakt spejl)

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
