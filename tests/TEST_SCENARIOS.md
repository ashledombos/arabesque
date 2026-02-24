# Arabesque — Scénarios de test "tordus" pour validation live

> Créé 2026-02-24. Chaque scénario DOIT passer avant de mettre 1€ en live.
> Convention: [P] = passe / [F] = fail attendu / [R] = recovery attendue

---

## 1. Reset journalier + DST

### 1.1 Reset FTMO hiver (CET = UTC+1)
- Reset à minuit Prague = 23:00 UTC
- Trade à 22:59 UTC → comptabilisé dans la journée en cours
- Trade à 23:01 UTC → nouvelle journée, compteurs remis à zéro
- **Vérifier** : daily_pnl, daily_trades, daily_start_balance tous reset [P]

### 1.2 Reset FTMO été (CEST = UTC+2)
- Reset à minuit Prague = 22:00 UTC
- Même test, l'heure UTC change mais le comportement est identique [P]

### 1.3 Transition DST (dernier dimanche de mars / octobre)
- Position ouverte la veille du changement d'heure
- Reset se produit 1h plus tôt/tard en UTC
- **Vérifier** : pas de double-reset, pas de reset manqué [P]

### 1.4 Reset Topstep (17h CT = 23:00 UTC hiver, 22:00 UTC été)
- Même principe, heure différente [P]

---

## 2. Drawdown et worst-case

### 2.1 Daily DD atteint avec floating P&L
- Balance 100k, 3 positions ouvertes, floating P&L total = -4800$
- Daily DD limit = 5% = 5000$
- Nouveau signal arrive → **REJECT** (worst-case dépasse) [F]

### 2.2 Daily DD trailing intraday (FTMO)
- FTMO calcule le daily DD sur "equity start of day" vs "lowest equity point"
- Balance monte de 100k → 102k (trades gagnants)
- Puis redescend à 97k (trades perdants)
- Daily DD = (100k - 97k) / 100k = 3%, PAS (102k - 97k) / 100k = 5%
- **Vérifier** : daily_start_balance = 100k (le start, pas le high) [P]

### 2.3 Total DD trailing (Apex / certains futures)
- Certaines firms ont un trailing max DD basé sur le high watermark
- Ex: balance monte à 110k, trailing DD 6% → seuil = 110k * 0.94 = 103.4k
- **Note** : pas implémenté actuellement, à ajouter si firm le requiert

### 2.4 Worst-case budget avec 5 positions ouvertes
- 5 positions × 400$ risk = 2000$ open risk
- Daily remaining = 2500$
- Nouveau trade 400$ → total worst case = 2400$ < 2500$ → **ACCEPT** [P]
- Nouveau trade 600$ → total worst case = 2600$ > 2500$ → **REJECT** [F]

### 2.5 DD safety margin
- Max total DD = 8%, safety margin = 1%
- Account à -6.9% → **ACCEPT** (sous le seuil -7%) [P]
- Account à -7.1% → **REJECT** (au-delà de pause) [F]

---

## 3. Sizing et arrondis

### 3.1 Lot step arrondi
- Risk cash = 400$, risk distance = 50 pips, lot step = 0.01
- Volume calculé = 0.0857 lots → arrondi à 0.08 (floor, pas round)
- Risque réel = 0.08 × 50 pips × 10$ = 400$ → ok
- **Vérifier** : ne JAMAIS arrondir au-dessus (over-risk) [P]

### 3.2 Volume minimum
- Risk cash réduit (DD avancé) → 50$, risk distance = 100 pips
- Volume = 0.005 lots, mais min_lot = 0.01
- → **REJECT** (impossible de respecter le risque avec min_lot) [F]
- Alternative : accepter à min_lot si le risque reste < 2× nominal

### 3.3 Contract size différent par broker
- EURUSD: contract_size = 100,000 chez cTrader, 100,000 chez TradeLocker
- XAUUSD: contract_size = 100 chez cTrader (oz), 1 chez TradeLocker (oz)?
- **Vérifier** : InstrumentSpec chargé depuis le broker, pas hardcodé [P]

### 3.4 Risque réel ≠ risque calculé (écart d'arrondi)
- Si |risque_réel - risque_calculé| > 10% → circuit breaker warning
- Si > 50% → circuit breaker critical [R]

---

## 4. SL/TP et ordres

### 4.1 SL absent après fill
- Ordre placé avec SL → broker confirme fill → relecture position → SL absent
- **Action** : circuit breaker CRITICAL, tenter de replacer SL, si échec → fermer [R]

### 4.2 SL du mauvais côté
- LONG entry 1.0800, SL posé à 1.0900 (au-dessus = du mauvais côté)
- **Vérifier** : invariant vérifié post-fill [F → CRITICAL]

### 4.3 SL/TP modifié par le broker (arrondi stop level)
- Entry 1.08000, SL calculé 1.07500, mais broker arrondit à 1.07490
- Écart = 1 pip → **WARNING** (< seuil), logguer [P avec warning]
- Écart = 10 pips → **CRITICAL** [R]

### 4.4 SL/TP supprimé pendant la vie de la position
- Boucle health check détecte position sans SL
- → circuit breaker CRITICAL, replacer SL, si échec → fermer [R]

### 4.5 Partial fill
- Demandé 0.10 lots, rempli 0.06 lots
- SL/TP doivent couvrir le volume réel, pas le demandé
- **Vérifier** : relecture post-fill ajuste le tracking [P avec warning]

---

## 5. Exécution et prix

### 5.1 Slippage excessif
- Signal close = 1.0800, broker ask = 1.0815 (15 pips de slip)
- Si > max_slippage_atr → **REJECT** [F]

### 5.2 Spread blow-up
- Spread normal = 1 pip, soudain = 15 pips (annonce macro)
- Guard spread: spread / ATR > max_ratio → **REJECT** [F]

### 5.3 Prix stale (broker ne répond plus)
- Dernier tick > 60 secondes → pas de fill estimé fiable
- → **REJECT** (ou circuit breaker si plusieurs instruments affectés) [F]

### 5.4 Double envoi (idempotence)
- Le même signal_id arrive 2 fois (bug réseau, retry)
- La 2e fois → **REJECT** (duplicate_instrument si position déjà ouverte,
  ou dedup par signal_id) [F]

---

## 6. Mapping instruments

### 6.1 Symbole inconnu
- Signal pour "AUDNZD" mais le broker a "AUD/NZD" ou "AUDNZD.e"
- **Vérifier** : mapping symbole dans la config, erreur claire si absent [F]

### 6.2 Instrument non autorisé dans le profil
- Signal pour BTCUSD sur un compte FTMO Standard qui n'autorise que le forex
- **Vérifier** : filtré par la liste instruments du profil [F]

### 6.3 Instrument hors session
- Signal pour EURUSD le samedi (marché fermé)
- **Vérifier** : broker rejette ou guard horaire filtre [F]

---

## 7. Cas limites temporels

### 7.1 Close weekend obligatoire
- Profil avec close_weekend = true
- Vendredi 21:55 UTC → fermer toutes les positions avant 22:00 [R]

### 7.2 News buffer
- Config news_buffer_min = 2
- Signal 90 secondes avant NFP → **REJECT** [F]
- Signal 3 minutes avant NFP → **ACCEPT** [P]

### 7.3 Swap overnight (frais)
- Position ouverte à 21:00 UTC, swap appliqué à 22:00 UTC
- Le swap change l'equity → recalculer DD avec le swap inclus
- **Note** : les swaps sont petits mais s'accumulent si many positions [P]

---

## 8. Circuit breaker

### 8.1 Freeze sur SL absent → récupération
- SL absent détecté → FROZEN
- SL replacé manuellement → manual_reset("SL repositionné") → RUNNING [R]

### 8.2 Freeze sur 3 warnings → pas de auto-reset
- 3 partial fills → FROZEN
- Le système NE DOIT PAS se déverrouiller seul → attendre manual_reset [F auto-reset]

### 8.3 Incident pendant un trade en cours
- Trade en cours de remplissage, circuit breaker se déclenche
- **Vérifier** : le trade en cours est géré (SL posé), seuls les NOUVEAUX sont bloqués [P]

---

## 9. Multi-compte

### 9.1 Même instrument sur 2 comptes de la même firm
- Signal EURUSD → dispatché au compte FTMO A et FTMO B
- → **REJECT** sur B si même firm (anti-duplication) [F]

### 9.2 Instrument assigné au mauvais compte
- BTCUSD assigné au profil crypto, pas au profil forex
- Signal BTCUSD vérifié contre la liste instruments du profil [F sur forex]

### 9.3 Un compte FROZEN, les autres continuent
- Compte A frozen, compte B running
- Nouveaux signaux → exécutés UNIQUEMENT sur B [P sur B, F sur A]

---

## Comment exécuter ces tests

Phase 1 (avant live) — tests unitaires :
  Chaque scénario ci-dessus → fonction test Python dans tests/test_guards_live.py.
  Pas besoin de broker réel, mock des réponses.

Phase 2 (avant live) — test d'intégration :
  Lancer le moteur en mode dry-run contre un broker démo (cTrader démo).
  Vérifier les logs pour chaque catégorie ci-dessus.
  Le shadow ledger permet de mesurer les rejets.

Phase 3 (première semaine live) — monitoring :
  Chaque incident réel est comparé à la catégorie correspondante ci-dessus.
  Si un scénario non couvert se produit → ajouter ici + corriger.
