# Adage — Session-hold nocturne or (« session-or »)

> **Nom de code** : Adage
> **Famille** : Danse classique — *adage*
> **Mouvement** : L'adage est la section lente d'un ballet : une position
> tenue en équilibre, longtemps, sans mouvement parasite, puis relâchée avec
> contrôle. Adage tient un LONG XAUUSD toute la nuit et le relâche au mur du
> matin — la sortie est l'heure, pas le prix.

---

## Description

**Adage** exploite le rendement de détention nocturne de l'or (biais
overnight documenté : la performance de l'or se concentre hors des heures
US). Une seule décision par jour, zéro paramètre libre :

1. **Entrée** : LONG à l'open de la 1re barre min1 ≥ 18:00 America/New_York
   (réouverture Globex après la fermeture 17:00-18:00 NY).
2. **Sortie** : mur horaire — 1re barre min1 ≥ 08:00 Europe/London.
   Appliquée par le **moteur** (`ManagerConfig.session_exit` en
   backtest/dry-run, `live.session_exit_by_strategy` côté monitor live),
   PAS par le générateur.
3. **SL -1R** : R = 1,0 × σ des 20 derniers rendements de session (causal,
   shift 1). Pas de TP.
4. **AUCUN overlay** : BE/trailing/ROI/giveback/deadfish/time-stop
   désactivés (`adage_manager_config()`) — le dossier 07-04 §4 montre que
   le BE maison détruit l'edge (l'or respire sous le trigger la nuit).

Garde-fous de construction de session (identiques à l'étude) : exit
apparié à J+1..J+3 (saut weekend), session > 20h exclue (férié/trou de
feed), ≥ 60 barres min1 par session.

## Paramètres clés

| Paramètre | Valeur | Description |
|---|---|---|
| `entry_time` | `18:00@America/New_York` | Réouverture Globex |
| `exit_time` | `08:00@Europe/London` | Mur (DST double fuseau via zoneinfo) |
| `sigma_window` | 20 | Sessions pour le σ causal |
| `sl_sigma_mult` | 1.0 | SL = -1σ = -1R |
| `max_spread_atr` | 0.10 | Guard : spread ≤ 0,10 × R (signal.atr = distance R) |

**Design FIGÉ** (protocole `docs/audit/session_or_wf_protocole_2026-07-10.md`,
zéro paramètre ajustable). Toute variante = nouveau protocole pré-enregistré,
un tir.

## Résultats validés

**WF formel jalon 1 : PASS par dérogation DD opérateur** (2026-07-10,
coût primaire 2,4 bps/session = 1,0 spread + 1,4 swap, pessimiste) :

| Métrique | Valeur |
|---|---|
| Sessions (2024-01 → 2026-07) | 634 |
| Exp nette | **+0,070R** |
| Rythme récent 18 mois | +1,51R/mois |
| Fenêtres semestrielles récentes | 3/3 positives (+0,098 / +0,112 / +0,059) |
| WR(> -0,25R) | 58,6 % (dérogation WR actée) |
| maxDD série sessions | -16,2R (> seuil 15R → dérogation DD actée) |

**Sizing gravé : 0,20-0,30 %/session max** (maxDD -16,2R ≈ -3,2 à -4,9 %
d'equity). ⚠️ Le pire creux (54 j) date de mai-juin 2026, tout frais.

**Validation croisée moteur vs étude (lot 3, 2026-07-10) : PASS** —
634/634 sessions appariées, |Δr| = 0,0000R aux conventions moteur (sortie
au close de la barre du mur, R linéaire), Exp nette moteur +0,068R vs
étude +0,070R. Backtest CLI aux coûts moteur par défaut : +0,060R
(slippage 0,03R > 0,5 bps mesuré = côté pessimiste).
Scripts : `tmp/wf_session_or.py`, `tmp/validation_croisee_adage.py`.

## Instruments

**XAUUSD UNIQUEMENT.** XAG = kill (spread nuit 10-11 bps re-confirmé par le
sondeur 7 nuits). Le mécanisme (`session_exit` paramétrable) est générique
mais ce dossier ne valide QUE 18hNY→8hLondres sur l'or.

## Gestion des positions

Profil dédié `adage_manager_config()` — la sortie au mur est le SEUL
mécanisme actif avec le SL. En live, le monitor exclut ces positions de
tout overlay BE/trailing et les ferme market au mur (retry + alerte URGENT
si broker injoignable, jamais d'exit inventé — lot 2).

## Statut

- 2026-07-04 : dossier session-métaux (étude initiale, XAU retenu, XAG kill).
- 2026-07-10 : GO opérateur (dérogation WR) + WF formel jalon 1 PASS
  (dérogation DD) + sizing gravé.
- 2026-07-10 : jalon 2bis — implémentation moteur (lot 1 manager, lot 2
  monitor live, lot 3 stratégie + validation croisée PASS).
- **Prochaine étape : jalon 3 = dry-run parquet 3 mois, puis jalon 4 =
  ombre.** Pas de live avant.
