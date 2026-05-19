# Audit réplicabilité / disaster recovery — 2026-05-19

> **Scope** : diagnostic only. Aucune modification du live, aucun secret committé, aucun commit sans validation user.
> **Auteur** : Claude (Opus 4.7) à la demande de Raphael.
> **Statut** : audit produit en réponse à la question "si le disque meurt maintenant, qu'est-ce qu'on perd ?".

---

## Verdict global

**Réplicable partiellement.**

Le code source, les configs non-sensibles et la documentation décisionnelle sont 100 % versionnés sur `origin/main` (alignement vérifié 0/0 à 22:50 UTC le 2026-05-19, juste avant cet audit). En revanche :

- **3 unités systemd actives n'existaient nulle part dans le repo** (ajoutées par le commit du jour suivant cet audit — cf. `deploy/systemd/installed/`).
- Aucun backup automatique des logs/state/journal/secrets n'est en place.
- 2,2 GB de parquets non sauvegardés (régénérables via `python -m arabesque.data.fetch` mais coût ~plusieurs heures et possible perte de granularité Dukascopy sur fenêtres anciennes).

Sans intervention humaine, une perte de disque entraîne aujourd'hui :

1. Reset complet OAuth FTMO + ré-enregistrement bot Telegram + nouveau topic ntfy (`config/secrets.yaml` non sauvegardé hors disque).
2. Reconstruction à la main des 3 unités systemd manquantes (corrigé par ce commit).
3. **Perte définitive de tout l'historique trade** (`logs/trade_journal.jsonl`) — base de toute mesure Phase 4 et de toute analyse rétroactive.
4. Perte des bilans manuels (`logs/journal/2026-04.md`, `2026-05.md`).
5. Perte de l'historique audit edge persistant (`logs/edge_audit.jsonl`).

---

## 1. État Git / code (snapshot 2026-05-19 22:50 UTC, avant patch DR)

- **Branche** `main` alignée avec `origin/main` (0 ahead / 0 behind). Dernier commit pushé : `33a94ae fix(feed): force broker reconnect after stale feed`.
- **Stash** : vide.
- **Non commités à ce moment** (perte immédiate si crash) :
  - Patch observabilité du jour : `scripts/replay_live_vs_theory.py`, `arabesque/execution/live.py`, `scripts/check_execution_invariants.py`, `tests/test_invariants_broker_filter.py`, 3 nouveaux fichiers de test, `HANDOFF.md`, `docs/DECISIONS.md`.
  - **Statut post-audit** : commité dans `c9c763b fix(observability): dedup multi-broker + loop resilience + legacy BE fallback`, pushé.
- **Suite pytest** : 85/85 verte avant et après commit.

---

## 2. Fichiers critiques non versionnés

### 2.a Secrets (sensibles — JAMAIS à committer)

| Chemin | Taille | Contenu (sections seulement, pas le contenu) |
|---|---|---|
| `config/secrets.yaml` | 871 o | `ctrader_oauth`, `ctrader_oauth_old`, `ftmo_challenge`, `tradelocker_gft`, `notifications` |

**Aucune fuite détectée dans `logs/`** (un seul faux positif sur `logs/maintenance_state.jsonl` correspondant au mot "secret" dans un nom de trigger watchlist, vérifié manuellement).

### 2.b État runtime + historique fondateur

| Chemin | Taille | Criticité | Type |
|---|---|---|---|
| `logs/trade_journal.jsonl` | 232 K | **CRITIQUE** | historique trades Phase 4 (source de mesure) |
| `logs/trade_journal.jsonl.bak.20260514` | 210 K | important | backup pré-fix be_source |
| `logs/edge_audit.jsonl` + `edge_audit_latest.md` | 23+2 K | important | audit edge persistant |
| `logs/equity_snapshots.jsonl` | 335 K | important | courbe d'équité (DD reconstruit) |
| `logs/multi_broker_snapshots.jsonl` | 6,8 M | secondaire | snapshots simultanés FTMO/GFT |
| `logs/maintenance_state.jsonl` | 17 K | important | mémoire `/suivi` cross-session |
| `logs/journal/2026-04.md`, `2026-05.md` | 29+15 K | **CRITIQUE** | bilans manuels |
| `logs/feed_watchdog_state.json` | 198 o | runtime | état watchdog |
| `logs/pending_fills.json` | 2 o | runtime | fills en attente |
| `logs/replay_live_vs_theory*.jsonl` | 203 K | important | replay détail par trade |
| `logs/shadow_filters.jsonl`, `weekend_crypto_guard.jsonl`, `edge_decomposition.jsonl`, `rolling_baseline_distribution.jsonl`, `backtest_runs.jsonl` (2,4 M) | — | secondaire | |
| `logs/audit/decisions_2026-03-16.jsonl` | 78 K | important | décisions formalisées |

### 2.c Systemd user units

11 unités installées dans `~/.config/systemd/user/arabesque-*`. Avant le commit DR :

- **3 entièrement absentes du repo** : `arabesque-feed-watchdog.service`, `arabesque-feed-watchdog.timer`, `arabesque-telegram-bot.service`.
  → **Corrigé** : copie 1:1 ajoutée dans `deploy/systemd/installed/` (vérifié no-secret).
- **5 services en template uniquement** : `arabesque-{live,fetch,report-daily,report-weekly,suivi-reminder}.service.template` dans `deploy/systemd/`. Procédure de substitution path/user **non documentée** → traitée dans `docs/RESTORE.md`.
- **4 timers versionnés 1:1** : `fetch.timer`, `report-daily.timer`, `report-weekly.timer`, `suivi-reminder.timer`.

### 2.d Données parquet

- `barres_au_sol/` : 2,2 GB (dukascopy 1,3 G + ccxt 896 M).
- **Régénérable** via `python -m arabesque.data.fetch` mais coût ~heures et certaines fenêtres anciennes Dukascopy peuvent ne plus être disponibles à l'identique.
- `barres_au_sol/README.md` versionné.

---

## 3. Backlog / connaissance ignorée

| Élément | Taille | Classement |
|---|---|---|
| `resources/gestion_trading_prop_firms.txt` | 30 K | note opérationnelle externe — candidat à migration vers `docs/research/` si jamais utile en référence persistante |
| `resources/indicateurs.txt` | 25 K | idem |
| `tmp/compare_live_vs_backtest.py` | 12 K | **À investiguer** : référencé dans skill `/bilan` §2.a — orphelin ou doublon ? (vérification reportée) |
| `tmp/missing_trades_chiffrage.py` | 8 K | one-off 13/05 |
| `tmp/*.py` autres (test_*, *_ablation.py) | ~85 K | one-offs de validation |
| `tmp/cointegration/`, `tmp/donchian/` | — | recherches externes (Pas de Deux backlog) — archive externe |
| `arabesque.egg-info/` | — | régénéré par `pip install -e .` |

**Aucun document stratégique majeur n'est piégé dans `tmp/` ou `resources/`.** Les vrais backlogs (`HANDOFF.md`, `docs/DECISIONS.md`, `docs/EXPERIMENT_LOG.md`, `STRATEGY.md`) sont déjà versionnés.

---

## 4. Stratégie de backup proposée

Détail dans `docs/BACKUP.md` (commandes + placeholders pour destination chiffrée).

Découpage par fréquence :

| Cible | Quoi | Fréquence | Format | Destination |
|---|---|---|---|---|
| **Secrets** | `config/secrets.yaml` | sur changement | GPG asym | cloud chiffré + copie cold sur clé USB |
| **State live** | `logs/` + `config/secrets.yaml` | quotidien | tar.zst chiffré GPG | rclone vers cloud chiffré |
| **Systemd units** | `~/.config/systemd/user/arabesque-*` | sur changement | versionner directement dans `deploy/systemd/installed/` | git (depuis ce commit) |
| **Parquets** | `barres_au_sol/dukascopy/`, `ccxt/` | hebdo | rsync incrémental | disque externe local OU NAS |

**Le timer systemd de backup n'est pas activé tant que la destination chiffrée n'est pas arbitrée.** Choix à faire entre Proton Drive (rclone backend) / iCloud Drive (via interface manuelle) / Drive perso / NAS local.

---

## 5. Procédure RESTORE.md

Fichier dédié : `docs/RESTORE.md`. Squelette validé dans cet audit, à compléter quand la destination de backup sera arbitrée.

---

## 6. Checklist perte disque (résumé)

**Si le disque mourait maintenant (après commit DR), on perdrait définitivement** :
- `config/secrets.yaml` (tokens OAuth cTrader + TradeLocker + Telegram + ntfy URLs).
- Historique trade `logs/trade_journal.jsonl` + bilans `logs/journal/*.md` + audit edge `logs/edge_audit.jsonl`.
- Tout autre fichier de `logs/` non publié.
- Données parquet `barres_au_sol/` (régénérables avec effort).

**Déjà protégé par git/origin/main** :
- Code source complet `arabesque/`, `scripts/`, `tests/`.
- Configs non-sensibles `config/{accounts,instruments,prop_firms,settings,signal_filters,universes,prop_firm_profiles}.yaml` + templates `*.example.yaml`.
- Documentation `docs/` (DECISIONS, STATUS, HANDOFF, et désormais DR/RESTORE/BACKUP).
- `STRATEGY.md` par stratégie.
- `pyproject.toml`, `install.sh`, `requirements.txt`.
- Systemd : 5 templates `.service.template` + 4 timers + 1 service webhook + **les 3 unités précédemment manquantes** (depuis commit DR).

**À sauvegarder hors git, action ouverte** :
- `config/secrets.yaml` → procédure dans `docs/BACKUP.md`.
- `logs/` complet (chiffré) → procédure dans `docs/BACKUP.md`.
- `barres_au_sol/` → procédure dans `docs/BACKUP.md`.

---

## 7. Sécurité

- **Aucun token affiché** dans cet audit (uniquement noms de sections : `ctrader_oauth`, `notifications`, etc.).
- `config/secrets.yaml` resté gitignored : aucun ajout au tracking.
- `git diff` non lancé sur secrets.
- Unités systemd vérifiées no-secret avant copie dans `deploy/systemd/installed/`.
- Aucune modification du live pendant l'audit.

---

## Blockers (ordre de priorité) — statut post-commit DR

| # | Blocker | Statut |
|---|---|---|
| 1 | Patch observabilité du jour non commité | ✅ Résolu (commit `c9c763b`, pushé) |
| 2 | 3 unités systemd jamais versionnées | ✅ Résolu (copie dans `deploy/systemd/installed/`) |
| 3 | Aucune sauvegarde de `config/secrets.yaml` hors disque | ⏳ Procédure documentée (`docs/BACKUP.md`), exécution en attente du choix de destination |
| 4 | Aucune sauvegarde de `logs/` | ⏳ Idem |
| 5 | `tmp/compare_live_vs_backtest.py` statut orphelin | ⏳ Vérification reportée |

---

## Actions minimales — état après commit DR

| # | Action | Statut |
|---|---|---|
| 1 | Commit + push patch observabilité du jour | ✅ |
| 2 | Snapshot des 3 unités systemd absentes | ✅ |
| 3 | Script `scripts/backup_state.sh` (tar+gpg+rclone) | ⏳ Bloquée tant que destination chiffrée non choisie |
| 4 | `docs/RESTORE.md` + `docs/BACKUP.md` rédigés | ✅ |
| 5 | Premier backup manuel + restore test | ⏳ Bloquée par #3 |
| 6 | Migrer `resources/*.txt` vers `docs/research/` si pertinent | ⏳ optionnel |

---

## Choix externe restant — décision user

Avant d'activer le backup automatique, arbitrage à faire :

1. **Où chiffrer / stocker** : Proton Drive (rclone-friendly) / iCloud Drive (manuel) / Google Drive personnel (rclone) / NAS local / autre ?
2. **Clé GPG** : générer une paire dédiée Arabesque ou réutiliser la clé personnelle existante ?
3. **Rétention** : combien de snapshots quotidiens conserver (7 jours ? 30 jours ?) ?
4. **Inclure ou non `barres_au_sol/`** dans le backup quotidien : très volumineux mais régénérable → recommandation : non, hebdo séparé sur disque externe.

Une fois ces 4 points arbitrés, le script `scripts/backup_state.sh` + le timer systemd dédié pourront être ajoutés en un commit dédié (probablement 30-45 min de travail).
