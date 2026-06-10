# Arbio Outreach Tracker

Dashboard für Instagram & LinkedIn Outreach — wer hat geantwortet, abgelehnt, geghostet.

**Live:** öffne `index.html` direkt im Browser oder via GitHub Pages.

## Struktur

```
data/
  contacts.json   — unified contact data (auto-updated daily)
  contacts.csv    — CSV export
scripts/
  build_data.py   — data builder (run locally or via GitHub Actions)
index.html        — dashboard
```

## Status-Kategorien

| Status | Bedeutung |
|--------|-----------|
| Geantwortet | Positiv geantwortet, Gespräch läuft |
| Verschoben | Hat Interesse, aber aktuell nicht passend |
| Abgelehnt | Explizit abgelehnt |
| Kein Reply | Angeschrieben, keine Antwort |

## Update manuell starten

```bash
cd scripts
python3 build_data.py          # nur Daten zusammenführen
python3 build_data.py --scrape # + fehlende Websites scrapen (dauert ~5 min)
```

## Auto-Update

GitHub Actions läuft täglich um 07:00 UTC und aktualisiert `data/contacts.json` + `data/contacts.csv`.
