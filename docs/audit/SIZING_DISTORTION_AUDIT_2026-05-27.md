# Audit sizing Phase 4 bis - 2026-05-27

## Objet

Verifier si le niveau de protection reduit (`CAUTION`/`DANGER`) et le rodage
produisent des volumes si faibles que le minimum broker transforme le risque
reel ou fausse la lecture en `R`.

Scope : entries `extension` + `glissade` depuis
`2026-05-16T08:44:00+00:00`, par broker. Les specs volume/pip ont ete lues
en connexion broker non destructive pendant que `arabesque-live.service`
restait `inactive` et `disabled`.

## Methode

Script versionne : `scripts/audit_sizing_distortion.py`.

- `target` = `risk_cash` apres DD, timeframe, rodage, correlation et
  protection, tel que journalise a l'entry.
- `actual` = risque impose par le volume execute, calcule avec la meme
  convention pip value que `OrderDispatcher` (YAML calibre et pip size
  broker). Le `contractSize` TradeLocker n'est pas utilise comme valeur
  economique pour crypto/metaux.
- Flag materiel : `actual / target > 1.25`, volume au minimum broker, ou
  spread d'entree `> 0.10R`.
- Un cash faible sans distorsion n'est pas, seul, une invalidation de l'edge
  en `R`; il ralentit surtout la recovery/payout.

## Resultats observes

| Date | Broker | Strategie | Instrument | Target | Actual | Ratio | Volume | Verdict |
|---|---|---|---|---:|---:|---:|---:|---|
| 2026-05-18 | FTMO | extension | CHFJPY | 19.23$ | 18.78$ | 0.98x | 0.09 | OK |
| 2026-05-18 | GFT | extension | CHFJPY | 19.23$ | 19.69$ | 1.02x | 0.09 | OK |
| 2026-05-19 | GFT | glissade | XAUUSD | 4.82$ | 46.01$ | **9.54x** | 0.01 min | **SUR-RISQUE** |
| 2026-05-20 | FTMO | extension | DASHUSD | 23.52$ | 24.27$ | 1.03x | 0.09 | OK |
| 2026-05-23 | GFT | extension | ETHUSD | 18.98$ | 21.51$ | 1.13x | 0.05 | OK |
| 2026-05-26 | GFT | extension | AUDJPY | 15.56$ | 15.54$ | 1.00x | 0.13 | OK |
| 2026-05-26 | GFT | extension | XAUUSD | 15.56$ | 17.97$ | 1.16x | 0.01 min | OK sous seuil |

`n=7`, `1/7` distorsion materielle. Aucun spread d'entree ne depasse
`0.10R` dans cet echantillon (maximum observe `4.84%R`).

## Decision

Le probleme n'est pas le niveau `CAUTION x0.50` en tant que tel. Le risque
concret est le minimum de lot sur certains couples strategie/instrument/
broker apres reduction du budget, ici Glissade XAUUSD sur GFT.

Correction retenue :

- ajouter `execution.max_executed_risk_ratio: 1.25`;
- dans `OrderDispatcher`, recalculer le risque du volume arrondi/minimum avant
  envoi ;
- rejeter fail-closed tout ordre dont le risque executable excede `125%` du
  budget autorise, avec warning explicite.

Cette regle prefere sauter un trade non representatif a relever le risque du
compte. Elle ne change pas le signal ni la logique d'edge ; elle empeche un
sur-risque cause par la granularite broker.

## Reprise

Apres push et validation de la suite de tests, la reprise peut etre envisagee
en `CAUTION x0.50` calcule : GFT reste legitiment en `CAUTION` via
`glissade streak=5`, donc le pire niveau broker protege aussi FTMO. Conditions
operateur : comptes plats, service chargeant les commits correctifs, moteur
pret et feed souscrit, watchdog sain, puis surveillance des rejets
`risk overshoot` pendant la reprise Phase 4 bis.
