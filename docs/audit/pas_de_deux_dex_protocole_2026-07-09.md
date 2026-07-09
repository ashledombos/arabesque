# Pas de Deux — DEX (Hyperliquid) : protocole d'étude pré-enregistré

**Date de gel : 2026-07-09, AVANT tout calcul de résultat.**
Étape (b) du pipeline — le kill 07-04 était compliance prop-firm only
(« réutilisable hors prop firm », cf. `pas_de_deux_compliance_2026-07-04.md`) ;
sur DEX en capital propre, l'interdiction hedging n'existe pas.

## Contexte de coûts (mesuré, étapes 1-2 Hyperliquid)

- Fees taker 4,5 bps/côté → **18 bps de notionnel brut par aller-retour de paire** (2 jambes).
- Funding réel horaire disponible 2024-10→2026-07 (14 instruments) ; côté short
  reçoit en moyenne +9,3 %/an → carry de paire ≈ neutre.

## Protocole (paramètres FIGÉS — aucun ne sera ajusté après lecture des résultats)

- **Données** : closes 4h Binance (cache `barres_au_sol`), 14 instruments
  liquides ∩ listings HL, log-prix. Fenêtre totale = tout le cache.
- **Walk-forward roulant** (aucune paire n'est sélectionnée sur les données
  qu'elle trade) : sélection sur 180 j (1080 barres 4h) → trading sur les
  90 j suivants (540 barres) → pas de 90 j.
- **Sélection par fenêtre** (parmi les 91 paires) :
  - OLS log(A) = α + β·log(B) sur la fenêtre de sélection ;
  - Engle-Granger : ADF(résidus, k=1, avec constante) t-stat < **-3.34**
    (MacKinnon 5 %, 2 variables) ;
  - half-life du spread (AR(1)) ∈ **[12 h, 30 j]** ;
  - β ∈ [0.2, 5] ;
  - **max 5 paires** par fenêtre (t-stat les plus négatifs) — cap concentration.
- **Règle de trading** (standard littérature, zéro paramètre optimisé) :
  - z-score du spread avec α, β, μ, σ **figés de la fenêtre de sélection** ;
  - entrée |z| ≥ 2 (z>0 : short A / long B pondéré β ; z<0 : inverse) ;
  - sortie : z croise 0 ; stop : |z| ≥ 3.5 ; time-stop : 3× half-life ;
  - une position par paire à la fois.
- **Coûts par trade** : 18 bps notionnel (fees) + funding réel horaire signé
  par jambe (long paie, short reçoit) ; heures non couvertes → fallback
  pessimiste (long paie la moyenne, short reçoit 0).
- **Comptabilité** : P&L en fraction du notionnel d'une jambe ;
  **R = distance entrée→stop = 1,5 σ_spread** → net_R = P&L net / (1,5 σ).

## Critères de verdict (écrits avant les résultats)

PASS (→ étape suivante : backtest approfondi + dossier complet) si TOUTES :
1. Exp **nette** > 0 sur l'ensemble des fenêtres OOS roulantes ;
2. Exp nette > 0 aussi sur la moitié récente (entrées ≥ 2025-07-01) ;
3. brut ≥ **3× coût** (filtre dur standard) ;
4. débit agrégé ≥ **2 trades/mois** ;
5. aucune paire seule ne porte > 50 % du P&L total (anti-cherry-pick).

Sinon KILL, consigné dans EXPERIMENT_LOG comme les autres candidats.
