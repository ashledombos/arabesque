# Session-or — Jalon 2 : chiffrage de l'implémentation time-exit

Date : 2026-07-10 · Livrable de spec/estimation — **zéro code écrit**.
Suite du jalon 1 PASS (WF formel, dérogations WR + DD actées, sizing gravé
0,20-0,30 %/session).

## Ce que le moteur doit savoir faire (et ne sait pas faire aujourd'hui)

Le design validé : LONG à la réouverture (1re barre ≥ 18:00 America/New_York),
**sortie à heure de mur** (1re barre ≥ 08:00 Europe/London), SL -1R, et
**AUCUN overlay BE/trailing/ROI** (ils détruisent l'edge, dossier 07-04 §4).

État des lieux (vérifié fichier par fichier) :
- `PositionManager._check_time_stop` existe mais compte des **barres**
  (`time_stop_bars=336`) — inadapté : la durée 18hNY→8hLondres varie avec les
  DST des deux fuseaux, et un trou de feed fausse `bars_open`.
- `update_position(pos, high, low, close, indicators)` **ne reçoit pas le
  timestamp** de la barre — 3 call sites (backtest.py:209/513,
  orchestrator.py:212).
- **Pas de flag `be_enabled`** : le BE est inconditionnel dans le manager.
  Trailing/ROI/giveback/deadfish/time-stop : désactivables par config
  (précédent `trailing_tiers=[]` dans ablation.py).
- **Le live n'utilise PAS PositionManager** : `position_monitor.py` (1 622 l)
  est un *miroir* de sa logique opérant sur le broker réel → toute nouvelle
  sortie doit être écrite DEUX fois et maintenue synchrone (la contrainte
  connue du système).

## Architecture retenue pour le chiffrage

1. **`PositionManager`** (zone noyau) — chemin backtest/dry-run :
   - nouveau flag `be_enabled: bool = True` (défaut inchangé → zéro impact) ;
   - nouveau champ `session_exit: str | None = None` (ex. `"08:00@Europe/London"`) ;
   - `update_position(..., bar_ts=None)` — paramètre optionnel, comportement
     strictement inchangé si None ; check EXIT_SESSION prioritaire (avant BE) ;
   - nouveau `DecisionType.EXIT_SESSION`.
2. **`position_monitor`** (miroir live) :
   - les positions de la stratégie session-or sont exclues du BE-polling
     (par label de stratégie, déjà porté par les ordres) ;
   - tâche de fermeture à heure de mur : au passage de 08:00 Londres, close
     market via broker, `exit_reason="session_exit"` ; broker injoignable →
     **retry + alerte URGENT, jamais d'exit inventé** (règle reconcile).
3. **Nouvelle stratégie `arabesque/strategies/<nom>/signal.py`** (création,
   pas une modif de stratégie validée) : signal LONG XAUUSD sur la 1re barre
   min1 ≥ 18:00 NY (aggregator min1 — précédent Fouetté), σ(20 sessions)
   causal pour le SL, refus vendredi (marché fermé 17h NY) — les sauts
   weekend du design sont dans le générateur, pas dans le moteur.
4. **Config** : entrée `universes`/settings + ManagerConfig dédié
   (BE off, trailing [], ROI off, giveback/deadfish/time-stop off) ; câblage
   du profil par stratégie côté monitor à préciser au dev (le monitor n'a pas
   de ManagerConfig — vérifier sa source de paramètres par stratégie).

## Estimation

| Lot | Contenu | Taille |
|---|---|---|
| 1 | Manager : flag BE + session_exit + bar_ts + DecisionType + ~6 tests | 1 session |
| 2 | Monitor live : skip BE + fermeture horaire + erreurs broker + ~6 tests | 1 session |
| 3 | Stratégie signal.py + config + validation croisée backtest moteur vs étude (+0,070R ± tol.) | 1 session |

**Total : ~3 sessions de dev**, puis jalon 3 (dry-run parquet 3 mois) sans code.
Suite des 434 tests verte exigée à chaque lot ; zone noyau touchée par le
lot 1 uniquement (param optionnel + flags à défaut inchangé = risque de
régression minimal, couvert par la suite).

## Risques nommés

1. **Divergence miroir** manager/monitor — la plaie historique du système ;
   mitigée par tests jumeaux (même scénario joué des deux côtés).
2. **Broker injoignable à 08:00 Londres** → la position déborde de l'heure ;
   politique : retry + alerte, l'edge se dégrade marginalement (accepté).
3. **DST double fuseau** (l'écart NY/Londres passe de 5h à 4h ~2 sem/an) —
   géré par zoneinfo, tests dédiés sur les dates de bascule 2024-2026.
4. **Fenêtre morte** : rien n'est écrit pour XAG (kill) ni pour d'autres
   heures — le mécanisme est générique (`session_exit` paramétrable) mais
   ce dossier ne valide QUE 18hNY→8hLondres sur XAUUSD.

## Décision demandée (jalon 2 → 2bis)

GO implémentation (3 lots ci-dessus, retour à chaque lot) / STOP ici
(le dossier reste prêt, réactivable sans re-étude).
