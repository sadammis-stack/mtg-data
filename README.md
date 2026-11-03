# mtg-data — Scryfall-spejl

Henter Scryfalls bulk-data én gang i døgnet med GitHub Actions og lægger kompakte filer
på branchen `data`. Formålet: kortdata og Cardmarket-trendpriser kan hentes fra
`raw.githubusercontent.com` i miljøer, hvor Scryfall selv er blokeret (fx Claudes sandkasse).

Data leveres af [Scryfall](https://scryfall.com/docs/api/bulk-data). Priser er Scryfalls
daglige snapshot af Cardmarket trend (EUR) — ikke live.

## Filer på `data`-branchen

| Fil | Indhold |
|---|---|
| `oracle.jsonl.gz` | Ét kort pr. linje: oracle-tekst, faces, color identity, Commander-legalitet, game_changer, billigste print (EUR) |
| `prints.csv.gz` | Ét papirprint pr. række: set, nummer, finishes, `eur`/`eur_foil`, `cardmarket_id` (= idProduct) |
| `rulings.jsonl.gz` | Officielle rulings pr. kort |
| `meta.json` | Byggetidspunkt og antal |

URL-mønster: `https://raw.githubusercontent.com/<ejer>/mtg-data/data/<fil>`

## Opslag med mtgdata.py

```
python3 mtgdata.py card "Sol Ring" "Mana Crypt"
python3 mtgdata.py prints "Urza's Saga"
python3 mtgdata.py set SLD 1512
python3 mtgdata.py cm 560000
python3 mtgdata.py rulings "Thassa's Oracle"
python3 mtgdata.py search "draw a card" --ci UR --type instant --max-cmc 2 --commander
python3 mtgdata.py deck vivi.txt -o vivi_priser.csv --skip-basics
```

Sæt `REPO` øverst i `mtgdata.py` (eller miljøvariablen `MTGDATA_REPO`) til `<ejer>/mtg-data`.

## Drift

- Kører dagligt kl. ~05:37 UTC; kan startes manuelt under **Actions → Scryfall mirror → Run workflow**.
- Fejler bygget (fx Scryfall nede), bliver gårsdagens data liggende — der publiceres kun, hvis sanity-tjekket består.
- `data`-branchen er altid én commit, så repoet vokser ikke.
