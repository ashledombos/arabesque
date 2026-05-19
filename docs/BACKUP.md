# `docs/BACKUP.md` — Stratégie de sauvegarde Arabesque

> **Statut au 2026-05-19** : procédure documentée, **non encore activée**.
> Bloquée sur **un choix externe** : où chiffrer et stocker (cf. §0).
> Pendant qu'on attend l'arbitrage, faire au minimum **un backup manuel ponctuel** des fichiers critiques (§3) sur clé USB chiffrée.

---

## 0. Décisions à prendre avant activation

Quatre points à arbitrer avant d'écrire le script + le timer systemd :

| # | Décision | Options envisagées | Choix actuel |
|---|---|---|---|
| 1 | **Destination cloud chiffré** | Proton Drive (rclone) / iCloud Drive (manuel) / Google Drive perso (rclone) / NAS local / autre | `<À ARBITRER>` |
| 2 | **Clé GPG** | Nouvelle paire dédiée Arabesque / Réutiliser clé personnelle existante | `<À ARBITRER>` |
| 3 | **Rétention** | 7 / 14 / 30 jours / illimité | `<À ARBITRER>` |
| 4 | **Inclusion `barres_au_sol/`** | Dans le quotidien (lourd) / Hebdo séparé sur disque externe / Pas du tout (régénérable) | **Recommandation : hebdo séparé** |

Une fois ces 4 points arbitrés, les placeholders `<REMOTE>`, `<RECIPIENT_KEY>`, `<RETENTION_DAYS>` de ce document peuvent être remplacés et le script `scripts/backup_state.sh` + l'unité systemd dédiée peuvent être ajoutés.

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
┌─────────────────────────────────┐
│ <REMOTE>:arabesque/state/       │  ← cloud chiffré (Proton/iCloud/NAS)
│  state-2026-05-19.tar.zst.gpg   │
│  state-2026-05-18.tar.zst.gpg   │
│  state-latest.tar.zst.gpg ──────┼──→ symlink ou alias
│  ...                            │
└─────────────────────────────────┘

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

## 4. Backup automatique chiffré (template, après arbitrage §0)

### 4.a Script `scripts/backup_state.sh` (à créer après arbitrage)

```bash
#!/usr/bin/env bash
# scripts/backup_state.sh — backup quotidien chiffré de l'état Arabesque
# Pré-requis : GPG key importée, rclone configuré, zstd installé.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATE=$(date -u +%Y%m%d)
TMP="/tmp/arabesque-state-$DATE.tar.zst"
GPG_OUT="$TMP.gpg"

# Placeholders à remplir après arbitrage
RECIPIENT="<RECIPIENT_KEY>"
REMOTE="<REMOTE>:arabesque/state"
RETENTION_DAYS="<RETENTION_DAYS>"

cd "$REPO"

# 1. Archive (Tier 1 + 2 + Tier 3 sans les gros JSONL secondaires)
tar -I 'zstd -19' --exclude='logs/multi_broker_snapshots.jsonl' \
    --exclude='logs/backtest_runs.jsonl' \
    --exclude='.venv' \
    -cf "$TMP" \
    config/secrets.yaml logs/

# 2. Chiffrement GPG asymétrique
gpg --batch --yes --recipient "$RECIPIENT" --encrypt --output "$GPG_OUT" "$TMP"
rm "$TMP"

# 3. Upload
rclone copy "$GPG_OUT" "$REMOTE/"
rclone copy "$GPG_OUT" "$REMOTE/" --include "$(basename "$GPG_OUT")"

# 4. Mise à jour du pointeur latest (rclone n'a pas de symlink, on duplique)
rclone copyto "$GPG_OUT" "$REMOTE/state-latest.tar.zst.gpg"

# 5. Rétention : suppression des snapshots > N jours sur le remote
rclone delete "$REMOTE/" --min-age "${RETENTION_DAYS}d" --include "state-2*"

# 6. Nettoyage local
rm "$GPG_OUT"

echo "[backup] OK $DATE → $REMOTE"
```

### 4.b Unité systemd dédiée (à créer après arbitrage)

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

**Pas créé tant que §0 n'est pas arbitré.**

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
rclone copy "<REMOTE>:arabesque/state/state-latest.tar.zst.gpg" .
gpg --decrypt state-latest.tar.zst.gpg | tar -I zstd -xf -
ls -la config/secrets.yaml logs/trade_journal.jsonl
diff <(head -100 logs/trade_journal.jsonl) <(head -100 ~/dev/arabesque/logs/trade_journal.jsonl)
# Attendu : pas de différence (ou seulement des nouvelles lignes côté prod)
```

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
| Backup manuel ponctuel sur clé USB | ⏳ **à faire dès aujourd'hui** |
| Choix destination cloud chiffré | ⏳ user |
| Choix clé GPG | ⏳ user |
| Choix rétention | ⏳ user |
| Script `scripts/backup_state.sh` | ⏳ après arbitrage |
| Timer systemd `arabesque-backup-state.timer` | ⏳ après arbitrage |
| Test restore mensuel | ⏳ après activation |
