# Arabesque — Instructions dépôt

> À lire **avant toute action** dans ce repo, quel que soit le mode (question, code, refactor).

---

## 1. Contexte

Système de trading quantitatif Python pour prop firms (FTMO / Goat Funded Trader).  
Edge : **mean-reversion BB H1** + module trend, sur crypto alt-coins et métaux précieux.  
Contraintes strictes : drawdown journalier 3%, drawdown total 10% (FTMO).

## 2. Lire en premier (ordre impératif)

1. **`HANDOFF.md`** — état opérationnel actuel + prochaines étapes P0-P8
2. **`docs/decisions_log.md`** — pourquoi chaque décision, bugs connus, ce qui a été abandonné
3. **`docs/instrument_selection_philosophy.md`** — logique de sélection par catégorie (anti-overfitting)
4. **`docs/TECH_DEBT.md`** — dette technique en cours + plan de résolution

Ne pas redécouvrir ce qui est déjà documenté.

---

## 3. Règles anti-biais (inviolables)

- **Anti-lookahead strict** : signal généré sur bougie `i`, exécuté au open de `i+1` via `_pending_signals`.
- **Code identique** backtest / replay parquet / live cTrader : un seul `CombinedSignalGenerator`, zéro divergence.
- **Tout en R/ATR** (invariant d'instrument) : sizing, paliers de trailing, métriques de performance.
- **Guards toujours actifs**, dry-run et replay inclus. Les désactiver pour "aller plus vite" invalide les résultats.
- **Un seul trade simultané par instrument** (`duplicate_instrument`). Ne pas revenir sur cette décision.
- **Règle pire-cas intrabar** : si SL et TP sont touchés sur la même bougie, c'est le SL qui gagne.

---

## 4. Workflow Git

- Push direct sur `main` autorisé (pas de PR).
- **Jamais `git push --force` sur `main`** (a déjà écrasé un commit).
- Commits atomiques et descriptifs : `fix:`, `feat:`, `docs:`, `refactor:`, `chore:`.
- Format : `type(scope): description courte` — ex : `fix(guards): daily_dd_pct divisé par daily_start_balance`.

---

## 5. Discipline "mémoire du projet"

Après **chaque session** :
- Mettre à jour `HANDOFF.md` : date, bugs corrigés, résultats obtenus, prochaines étapes.
- Mettre à jour `docs/decisions_log.md` : toute décision prise, bug identifié, hypothèse testée + résultat chiffré.
- Mettre à jour `docs/journal.md` : entrée datée avec commits liés.
- Mettre à jour `docs/TECH_DEBT.md` : ajouter les nouveaux items, marquer les résolus.

Règle : **ne pas dupliquer**. Si l'info est dans `decisions_log.md`, on référence, on ne recopie pas.

---

## 6. Qualité et dette technique

- **Supprimer le code mort** plutôt que de commenter `# deprecated`.
- **Créer un script CLI dédié** si une commande revient souvent (boucle sur instruments, run complet…).
- **Ne jamais laisser des calculs dupliqués** (ex : ADX calculé dans `signal_gen.py` ET `signal_gen_trend.py`).
- **Toute nouvelle dette** détectée → ajouter dans `docs/TECH_DEBT.md` (symptom, impact, fichiers concernés, priorité).
- **Rien ne passe de `research/` à `stable/`** sans pipeline IS/OOS + Monte Carlo complet.

---

## 7. Alertes (être proactif, ne pas attendre qu'on demande)

- Toute **divergence live vs backtest** → signaler immédiatement.
- Tout **biais potentiel** (lookahead, sélection, paramètre optimisé sur OOS) → signaler + proposer validation.
- Toute **zone dangereuse prop-firm** (guards DD, slippage, spread) → proposer dry-run ou paper avant live.
- Toute **hypothèse non validée** présentée comme certaine → signaler.
- Si une demande implique un **risque sur le compte challenge** (~5% DD restant) → refuser et proposer le compte test.

---

## 8. Comptes — rappel critique

| Compte | Montant | Type cTrader | Règle |
|---|---|---|---|
| Live test 15j (account_id 17057523) | 100 000 USD | « Live » | Sans risque réel — utiliser pour tests ordres |
| Challenge 100k | ~94 989 USD | « Demo » | Argent réel payé — **NE PAS connecter le bot avant validation guards DD** |

⚠️ Bug actif BLOQUANT : `daily_dd_pct` divisé par `start_balance` au lieu de `daily_start_balance` → guards DD ne se déclenchent jamais.

---

## 9. Prompt de reprise (copier-coller en début de nouvelle session)

```
Lis HANDOFF.md et docs/decisions_log.md dans le repo GitHub ashledombos/arabesque
(branche main) avant de répondre. Contexte : trading algo prop firms FTMO, Python.
Bug critique non corrigé : daily_dd_pct divisé par start_balance
(doit être daily_start_balance) — guards DD ne se déclenchent jamais.
Workflow : push direct main, doc à jour après chaque session, supprimer code mort.
Si tu proposes une modification de code : indique impact, risques, comment valider,
met à jour HANDOFF.md + decisions_log.md + TECH_DEBT.md si nécessaire.
```
