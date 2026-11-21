# leonix-data

Dati didattici per **Leonix – Macro Intelligence**, un laboratorio educativo sui regimi macro (nessuna consulenza, nessuna profilazione).

Questo repository contiene lo snapshot giornaliero (`data/`) letto dall'app, aggiornato automaticamente da GitHub Actions.

> **Disclaimer:** i prezzi provengono da Yahoo Finance e sono usati **solo per il prototipo**. Non sono dati con licenza per uso commerciale e non costituiscono consulenza finanziaria. I dati macro provengono da FRED (Federal Reserve Bank of St. Louis).

---

## Pipeline giornaliera Leonix

Aggiorna lo snapshot dopo la chiusura USA. Nessuna chiave sta in questo archivio: `FRED_API_KEY` si legge solo dall'ambiente. Senza quella variabile le serie FRED restano mancanti e non vengono inventate.

## Comando

Dalla radice di questo archivio:

```bash
python -m pip install -r requirements.txt
python mobile/scripts/update_daily.py
```

## Output

Il file pubblicato è `data/snapshot.json`.

Per un altro percorso, relativo alla radice del repository:

```bash
LEONIX_SNAPSHOT_OUT=data/snapshot.json python mobile/scripts/update_daily.py
```

Lo stesso giro scrive anche `mobile/src/data/snapshot.json`, `mobile/public/snapshot.json` e `mobile/src/data/overlay.json`. L'app legge l'indirizzo pubblico di `data/snapshot.json`.

Il workflow in `.github/workflows/daily-snapshot.yml` esegue lo stesso comando dal lunedì al venerdì alle 22:30 UTC e pubblica `data/snapshot.json`.
