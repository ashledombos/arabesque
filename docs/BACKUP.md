# `docs/BACKUP.md` — Stratégie de sauvegarde Arabesque

> **Statut au 2026-05-19** : doctrine **arbitrée** (cf. §0), **automatisation non activée**.
> Pré-requis techniques restants : configuration `rclone` (remote chiffré dédié) + génération/import des clés GPG (principale + secours).
> En attendant l'activation, faire au minimum **un backup manuel ponctuel** des fichiers critiques (§3) sur clé USB chiffrée.

---

## 0. Doctrine arbitrée (2026-05-19)

Quatre points arbitrés. Au moment d'activer le script + le timer systemd, ces choix sont figés ; ne plus revenir dessus sans entrée `docs/DECISIONS.md` qui acte la révision.

| # | Décision | Choix retenu | Justification |
|---|---|---|---|
| 1 | **Destination cloud chiffré** | Remote **rclone chiffré dédié** (`rclone crypt`) — pas un dossier cloud en clair | Le contenu (`secrets.yaml`, journal trades) ne doit jamais transiter en clair via l'API d'un provider. Le chiffrement applicatif (GPG) est doublé par le chiffrement de transport rclone côté remote. |
| 2 | **Clés GPG** | **Clé principale** (chiffrement quotidien) + **clé de secours** (stockée séparément, support physique cold) | Une seule clé = un seul point de défaillance. La clé de secours permet de récupérer si la principale est perdue/compromise. Les deux sont déclarées en `--recipient` du `gpg --encrypt`. |
| 3 | **Rétention (GFS)** | **14 quotidiens + 8 hebdos + 6 mensuels** | Grandfather-Father-Son : couvre 2 semaines de granularité fine, 2 mois de granularité hebdo, 6 mois de granularité mensuelle. Évite l'explosion de stockage tout en gardant un horizon de 6 mois pour les incidents tardifs (corruption silencieuse découverte plus tard). |
| 4 | **Inclusion `barres_au_sol/`** | **Hors backup quotidien.** Backup hebdo séparé sur **disque externe / NAS local** | Volume 2,2 GB régénérable via `arabesque.data.fetch`. L'inclure dans le quotidien chiffré ferait exploser le coût du cloud et la durée du backup. Tier 4 dédié, support local non-cloud. |

Placeholders restant à remplacer **au moment de l'activation** (pas tant que rclone+GPG ne sont pas configurés) :

- `<REMOTE_NAME>` — nom du remote rclone chiffré dédié (à choisir : ex `arabesque-crypt`).
- `<GPG_KEY_FINGERPRINT>` — empreinte de la clé principale.
- `<GPG_BACKUP_FINGERPRINT>` — empreinte de la clé de secours.

---

## 1. Périmètre du backup

### 1.a Tier 1 — CRITIQUE (perte = système non restaurable sans re-création des credentials)

| Quoi | Pourquoi | Volume |
|---|---|---|
| `config/secrets.yaml` | tokens OAuth + URLs Telegram/ntfy | <1 KB |
| `logs/trade_journal.jsonl` (+ `.bak.*`) | historique trades fondateur | ~250 KB |
| `logs/journal/*.md` | bilans manuels (rédigés à la main, perte = perte de connaissance) | ~50 KB |

### 1.b Tier 2 — IMPORTANT (perte = perte d'historique d'observabilité)

| Quoi | Pourquoi | Volume |
|---|---|---|
| `logs/edge_audit.jsonl` + `edge_audit_latest.md` | audit edge persistant | ~25 KB |
| `logs/equity_snapshots.jsonl` | courbe d'équité (DD reconstruit) | ~350 KB |
| `logs/maintenance_state.jsonl` | mémoire `/suivi` | ~20 KB |
| `logs/replay_live_vs_theory*.jsonl` | replay détail par trade | ~200 KB |
| `logs/edge_decomposition.jsonl` | décomposition catégorielle ΔExp | ~2 KB |
| `logs/rolling_baseline_distribution.jsonl` | baseline rolling 20 mois | ~2 KB |
| `logs/audit/decisions_2026-03-16.jsonl` | décisions formalisées | ~80 KB |

### 1.c Tier 3 — SECONDAIRE (utile mais régénérable ou non bloquant)

| Quoi | Pourquoi | Volume |
|---|---|---|
| `logs/multi_broker_snapshots.jsonl` | snapshots simultanés FTMO/GFT | ~7 MB |
| `logs/backtest_runs.jsonl` | historique runs backtest | ~2,5 MB |
| `logs/shadow_filters.jsonl`, `weekend_crypto_guard.jsonl` | logs filtres | ~50 KB |
| `logs/feed_watchdog_state.json`, `pending_fills.json` | état runtime (recréé en quelques minutes) | <1 KB |

### 1.d Tier 4 — VOLUMINEUX RÉGÉNÉRABLE

| Quoi | Pourquoi | Volume |
|---|---|---|
| `barres_au_sol/dukascopy/` + `ccxt/` | données OHLC parquet | 2,2 GB |

Régénérable via `python -m arabesque.data.fetch`, mais ~plusieurs heures de download. **Recommandation : backup hebdo séparé sur disque externe ou NAS local**, pas dans le quotidien cloud chiffré.

---

## 2. Architecture proposée

```
┌─────────────────────────────────┐
│ Local                           │
│  config/secrets.yaml            │
│  logs/*                         │
│  barres_au_sol/*                │
└──────────────┬──────────────────┘
               │
               │ tar + zstd + gpg
               │ (quotidien, Tier 1+2+3)
               ▼
┌─────────────────────────────────┐
│ /tmp/arabesque-state-DATE.tgz.gpg│
└──────────────┬──────────────────┘
               │
               │ rclone copy
               ▼
┌─────────────────────────────────────────────┐
│ <REMOTE_NAME>:arabesque/state/              │  ← remote rclone chiffré dédié
│  daily/state-2026-05-19.tar.zst.gpg         │  (14 quotidiens GFS)
│  daily/state-2026-05-18.tar.zst.gpg         │
│  weekly/state-2026-W20.tar.zst.gpg          │  (8 hebdos)
│  monthly/state-2026-05.tar.zst.gpg          │  (6 mensuels)
│  state-latest.tar.zst.gpg                   │  ← alias dernier daily
└─────────────────────────────────────────────┘

┌─────────────────────────────────┐
│ Local                           │
│  barres_au_sol/*                │
└──────────────┬──────────────────┘
               │ rsync incrémental
               │ (hebdo, Tier 4)
               ▼
┌─────────────────────────────────┐
│ <DISQUE_EXTERNE>/barres_au_sol/ │
└─────────────────────────────────┘
```

---

## 3. Backup manuel ponctuel (en attendant l'arbitrage)

À faire **dès aujourd'hui** sur clé USB chiffrée :

```bash
# Adapter <USB_MOUNT> à la clé USB chiffrée
USB="<USB_MOUNT>/arabesque-backup"
mkdir -p "$USB"

# Tier 1 + 2 + 3 (logs + secrets)
tar -I 'zstd -19' --exclude='logs/multi_broker_snapshots.jsonl' \
    --exclude='logs/backtest_runs.jsonl' \
    -cf "$USB/state-$(date -u +%Y%m%d).tar.zst" \
    config/secrets.yaml logs/

# Tier 4 (parquets)
rsync -av barres_au_sol/ "$USB/barres_au_sol/"
```

Vérifier la lisibilité :

```bash
tar -I zstd -tf "$USB/state-$(date -u +%Y%m%d).tar.zst" | head -20
```

**Sécurité** : la clé USB doit être chiffrée au niveau filesystem (LUKS, VeraCrypt, ou équivalent macOS/Windows). Ne pas brancher cette clé sur une machine non-confiance.

---

## 4. Backup automatique chiffré (template, à instancier après configuration rclone + GPG)

### 4.a Script `scripts/backup_state.sh` (à créer une fois rclone+GPG configurés)

Stratégie : un seul script lancé quotidiennement, qui produit un snapshot daily.
Une copie est promue en `weekly/` chaque dimanche (UTC) et en `monthly/` le 1er du mois.
Le ménage GFS (14 daily + 8 weekly + 6 monthly) est fait en fin de run.

```bash
#!/usr/bin/env bash
# scripts/backup_state.sh — backup quotidien chiffré de l'état Arabesque
# Pré-requis : GPG keys importées (principale + secours), rclone configuré, zstd installé.
# Doctrine : cf. docs/BACKUP.md §0.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATE=$(date -u +%Y-%m-%d)
DOW=$(date -u +%u)        # 1=lundi … 7=dimanche
DOM=$(date -u +%d)        # 01..31
ISOWEEK=$(date -u +%G-W%V)
MONTH=$(date -u +%Y-%m)

TMP="/tmp/arabesque-state-$DATE.tar.zst"
GPG_OUT="$TMP.gpg"

# Placeholders à remplir au moment de l'activation (cf. docs/BACKUP.md §0)
RECIPIENT_PRIMARY="<GPG_KEY_FINGERPRINT>"
RECIPIENT_BACKUP="<GPG_BACKUP_FINGERPRINT>"
REMOTE="<REMOTE_NAME>:arabesque/state"

# Rétention GFS (doctrine §0 — ne pas changer sans entrée docs/DECISIONS.md)
KEEP_DAILY=14
KEEP_WEEKLY=8
KEEP_MONTHLY=6

cd "$REPO"

# 1. Archive (Tier 1 + 2 + Tier 3 sans les gros JSONL secondaires)
tar -I 'zstd -19' --exclude='logs/multi_broker_snapshots.jsonl' \
    --exclude='logs/backtest_runs.jsonl' \
    --exclude='.venv' \
    -cf "$TMP" \
    config/secrets.yaml logs/

# 2. Chiffrement GPG asymétrique vers 2 destinataires (principal + secours)
gpg --batch --yes \
    --recipient "$RECIPIENT_PRIMARY" \
    --recipient "$RECIPIENT_BACKUP" \
    --encrypt --output "$GPG_OUT" "$TMP"
rm "$TMP"

# 3. Upload daily
rclone copyto "$GPG_OUT" "$REMOTE/daily/state-$DATE.tar.zst.gpg"

# 4. Alias latest (rclone n'a pas de symlink, on duplique)
rclone copyto "$GPG_OUT" "$REMOTE/state-latest.tar.zst.gpg"

# 5. Promotion hebdo (dimanche UTC)
if [ "$DOW" = "7" ]; then
    rclone copyto "$GPG_OUT" "$REMOTE/weekly/state-$ISOWEEK.tar.zst.gpg"
fi

# 6. Promotion mensuelle (1er du mois UTC)
if [ "$DOM" = "01" ]; then
    rclone copyto "$GPG_OUT" "$REMOTE/monthly/state-$MONTH.tar.zst.gpg"
fi

# 7. Ménage GFS — purge des snapshots au-delà de la fenêtre de rétention
#    --min-age = supprime ce qui est PLUS VIEUX que N jours (approximation lisible).
rclone delete "$REMOTE/daily/"   --min-age "${KEEP_DAILY}d"
rclone delete "$REMOTE/weekly/"  --min-age "$((KEEP_WEEKLY * 7))d"
rclone delete "$REMOTE/monthly/" --min-age "$((KEEP_MONTHLY * 31))d"

# 8. Nettoyage local
rm "$GPG_OUT"

echo "[backup] OK $DATE → $REMOTE (daily$([ "$DOW" = "7" ] && echo "+weekly")$([ "$DOM" = "01" ] && echo "+monthly"))"
```

### 4.b Unité systemd dédiée (à créer une fois le script en place)

`deploy/systemd/arabesque-backup-state.service.template` :

```ini
[Unit]
Description=Arabesque — backup quotidien état chiffré
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/var/home/machine/dev/arabesque
ExecStart=/var/home/machine/dev/arabesque/scripts/backup_state.sh
Nice=15
StandardOutput=journal
StandardError=journal
```

`deploy/systemd/arabesque-backup-state.timer` :

```ini
[Unit]
Description=Backup Arabesque toutes les 24h

[Timer]
OnCalendar=*-*-* 03:30:00 UTC
Persistent=true
RandomizedDelaySec=10min
Unit=arabesque-backup-state.service

[Install]
WantedBy=timers.target
```

**Pas créé tant que `rclone` et les clés GPG ne sont pas configurés** (doctrine §0 figée, infra à monter).

### 4.c Backup hebdo des parquets (sur disque externe local)

À automatiser via un second timer ou un cron manuel quand le disque externe est branché :

```bash
rsync -av --delete-excluded barres_au_sol/ <DISQUE_EXTERNE>/barres_au_sol/
```

---

## 5. Procédure de test du backup

Une fois activé, **tester mensuellement** la restauration sur un répertoire de test :

```bash
mkdir -p /tmp/arabesque-restore-test
cd /tmp/arabesque-restore-test
rclone copy "<REMOTE_NAME>:arabesque/state/state-latest.tar.zst.gpg" .
gpg --decrypt state-latest.tar.zst.gpg | tar -I zstd -xf -
ls -la config/secrets.yaml logs/trade_journal.jsonl
diff <(head -100 logs/trade_journal.jsonl) <(head -100 ~/dev/arabesque/logs/trade_journal.jsonl)
# Attendu : pas de différence (ou seulement des nouvelles lignes côté prod)
```

Tester périodiquement la **clé de secours** : refaire le décrypt avec uniquement la clé de secours montée (la principale retirée du keyring de test), pour vérifier que les deux destinataires ont bien été inclus dans le chiffrement et que la clé de secours est lisible.

Si le test échoue, la procédure backup est cassée — **investiguer avant la prochaine perte réelle**.

---

## 6. Ce que NE PAS sauvegarder

- `.venv/` (recréable via `install.sh` en ~3 min)
- `arabesque.egg-info/` (régénéré par `pip install -e .`)
- `__pycache__/`
- `.pytest_cache/`
- `logs/multi_broker_snapshots.jsonl` (volumineux, secondaire, peut être exclu du quotidien)
- `logs/backtest_runs.jsonl` (volumineux, secondaire)

Adapter les `--exclude` du `tar` en conséquence.

---

## 7. Sécurité opérationnelle

- **Ne jamais committer `config/secrets.yaml`** : déjà dans `.gitignore`, vérifier avant chaque `git add -A` (préférer `git add <fichier>` explicite).
- **Ne jamais committer les snapshots chiffrés** : `.tar.zst.gpg` → ajouter à `.gitignore` si jamais ils sont produits dans le repo par erreur.
- **Clé GPG privée** : à protéger comme un mot de passe maître. Une perte = perte définitive de l'accès aux backups. Stocker un backup de la clé privée sur un support physique séparé (papier dans coffre, ou clé USB cold).
- **rclone config** : contient les tokens du cloud chiffré → également sensible, à backup séparément (souvent dans `~/.config/rclone/rclone.conf`).

---

## 8. Statut d'activation

| Composant | Statut |
|---|---|
| Documentation procédure | ✅ ce fichier |
| `docs/RESTORE.md` | ✅ |
| Snapshot systemd dans `deploy/systemd/installed/` | ✅ |
| Doctrine arbitrée (§0 : remote rclone chiffré dédié / clé GPG principale + secours / GFS 14d+8w+6m / parquets hors quotidien) | ✅ 2026-05-19 |
| Backup manuel ponctuel sur clé USB | ⏳ **à faire dès aujourd'hui** |
| Configuration `rclone` (remote chiffré dédié) | ⏳ user |
| Génération/import clé GPG principale + clé de secours | ⏳ user |
| Script `scripts/backup_state.sh` (template §4.a prêt) | ⏳ après rclone+GPG |
| Timer systemd `arabesque-backup-state.timer` (template §4.b prêt) | ⏳ après script |
| Test restore mensuel (incluant la clé de secours) | ⏳ après activation |
