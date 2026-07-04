# Pas de Deux — Étude compliance (2026-07-04) : VERDICT KILL (contexte prop firm)

**Étape (a) du pipeline coût/compliance-d'abord** (plan portefeuille, tâche #7).
L'étude s'arrête à l'étape compliance — coût de l'instruction : ~30 minutes, zéro code.

## Textes officiels

**FTMO** (`ftmo.com/en/forbidden-trading-practices/`) — pratiques interdites :
> "use trading strategies that artificially distribute profit across multiple days
> without proportionally distributing market risk, such as **hedging or holding
> opposing positions on the same or highly correlated instruments**, or partially
> closing and managing the same trade idea across multiple trading days, in order
> to circumvent the Best Day Rule"

Le pattern Pas de Deux (long XAUUSD / short XAGUSD, long AUDUSD / short NZDUSD…)
correspond **littéralement** à « opposing positions on highly correlated
instruments ». La clause d'intention (« in order to circumvent the Best Day Rule »)
pourrait en théorie exonérer un stat-arb sincère, mais au moment d'un review de
payout, c'est le pattern observé qui parle — risque de refus de paiement réel.
Lecture pessimiste obligatoire (boussole projet).

**GFT / Goat Funded Trader** (`help.goatfundedtrader.com` — Prohibited Trading
Practices) — encore plus explicite :
- hedging interdit **dans le même compte**, entre comptes, et avec des firmes externes ;
- interdits nommés : « **hedging across correlated instruments** to mask directional
  exposure », grid, martingale, HFT/latency arbitrage, reverse hedging.

## Verdict

**KILL absolu dans le contexte prop firm** — les deux venues du projet interdisent le
cœur même de la stratégie (positions opposées sur instruments corrélés). Aucun
contournement propre : choisir des instruments *liés* est la définition du pairs
trading. Pas d'infra multi-jambes à construire, pas de backtest à lancer.

## Notes pour le futur

- Le verdict est **contextuel, pas statistique** : Pas de Deux reste une famille
  valide sur un compte personnel (hors prop firm) ou si une future venue l'autorise
  explicitement par écrit. Réhabilitation = changement de contexte, pas nouveau WF.
- Le swap GFT (trou de données) n'est plus bloquant pour rien d'actif — reste utile
  pour le rapport P1 général.
- Conséquence portefeuille : le diversifiant market-neutral tombe. Le déficit de
  2-4 edges décorrélés se comble via les familles suivantes de la Phase C :
  effets de session sur métaux, breakout-échec (fade) forex, carry/swap positif.
