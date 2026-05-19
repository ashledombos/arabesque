# `docs/RESTORE.md` — Procédure de restauration Arabesque sur une nouvelle machine

> **Cible** : remettre Arabesque en état de marche sur une machine neuve (perte de disque, migration, deuxième machine de redondance).
> **Pré-requis** : avoir une copie des backups hors disque (cf. `docs/BACKUP.md`).
> **Statut** : procédure validée méthodologiquement ; les destinations cloud / clés GPG sont à arbitrer (placeholders `<...>` ci-dessous).

---

## 0. Pré-requis machine cible

| Composant | Version recommandée | Pourquoi |
|---|---|---|
| OS | Fedora 44+ ou Linux équivalent avec `systemd --user` activé | Les 11 unités systemd sont en mode user, pas system |
| Python | 3.10+ (idéalement 3.14 pour matcher la machine actuelle) | `pyproject.toml` exige `>=3.10` |
| Git | toute version récente | clone du repo |
| GPG | 2.x | déchiffrement des backups |
| rclone | toute version récente | sync depuis le cloud chiffré |
| zstd | toute version | décompression des archives |
| Espace disque | ~5 GB (repo + .venv + parquets) | `barres_au_sol/` pèse 2,2 GB |

Vérifier `loginctl enable-linger $USER` pour que les units `--user` tournent même sans session SSH active.

---

## 1. Clone du repo

```bash
mkdir -p ~/dev && cd ~/dev
git clone git@github.com:<owner>/arabesque.git
cd arabesque
git checkout main
```

Si on veut figer sur un commit précis (par exemple le dernier connu sain) :

```bash
git checkout <commit_sha>
```

---

## 2. Restauration des fichiers non versionnés (depuis le backup chiffré)

### Pré-requis : avoir importé la clé GPG de déchiffrement

```bash
gpg --import <path_vers_cle_privee>
```

### Récupération du backup le plus récent

```bash
# Placeholder destination (à substituer une fois arbitrée)
RCLONE_REMOTE="<remote_name>:arabesque"

rclone copy "$RCLONE_REMOTE/state-latest.tar.zst.gpg" /tmp/
gpg --decrypt /tmp/state-latest.tar.zst.gpg | tar -I zstd -xf - -C ~/dev/arabesque
```

Le tar restaure :

- `config/secrets.yaml`
- `logs/` (tout sauf `barres_au_sol/`)

Vérifier le contenu :

```bash
ls -la config/secrets.yaml logs/trade_journal.jsonl logs/journal/
```

### Restauration des parquets (optionnelle au boot)

Option A — depuis le backup hebdo sur disque externe / NAS :

```bash
rsync -av --progress <source>/barres_au_sol/ ~/dev/arabesque/barres_au_sol/
```

Option B — régénérer via `arabesque.data.fetch` (long, mais possible) :

```bash
# Après l'étape 3 (venv installé)
python -m arabesque.data.fetch --start 2024-01-01 --end <today> --derive 1h 5m
```

---

## 3. Installation du `.venv`

```bash
./install.sh
```

Ce script gère le conflit `ctrader-open-api` / `tradelocker` sur `requests` (cf. commentaire en tête du script). Il installe :

- dépendances principales (`pandas`, `numpy`, `pyarrow`, `pyyaml`, `twisted`, `aiohttp`, `requests`, `python-telegram-bot`, `ctrader-open-api`).
- `tradelocker` avec `--no-deps` puis ses deps manuellement.
- `service-identity` pour TLS hostname verification.
- `ccxt`, `yfinance`, `apprise` (via `[all]`).

### Vérification des dépendances

Le script termine par un import test ; s'il échoue, le venv est cassé. Manuel :

```bash
./.venv/bin/python -c "import arabesque, ctrader_open_api, tradelocker, ccxt, yfinance, apprise; print('OK')"
```

---

## 4. Installation des unités systemd user

### 4.a Copier les unités 1:1 (depuis le snapshot `installed/`)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/installed/*.service ~/.config/systemd/user/
cp deploy/systemd/installed/*.timer ~/.config/systemd/user/
cp deploy/systemd/*.timer ~/.config/systemd/user/  # timers versionnés directement
```

### 4.b Instancier les templates `.service.template`

Les 5 templates contiennent des chemins absolus `/var/home/machine/dev/arabesque` et l'username `machine`. Si la machine cible reproduit ces chemins, copier tel quel ; sinon, substituer :

```bash
USER_HOME="$HOME"
USER_NAME="$USER"
REPO_PATH="$USER_HOME/dev/arabesque"

for tpl in deploy/systemd/*.service.template; do
    target="$HOME/.config/systemd/user/$(basename "$tpl" .template)"
    sed -e "s|/var/home/machine/dev/arabesque|$REPO_PATH|g" \
        -e "s|User=machine|User=$USER_NAME|g" \
        "$tpl" > "$target"
done
```

(Vérifier que les `.service.template` ne contiennent pas d'autres placeholders ; à la date de cet audit, seuls le path et l'utilisateur sont à substituer.)

### 4.c Reload + activation

```bash
systemctl --user daemon-reload

# Services au démarrage automatique
systemctl --user enable --now arabesque-live.service
systemctl --user enable --now arabesque-telegram-bot.service

# Timers
systemctl --user enable --now arabesque-feed-watchdog.timer
systemctl --user enable --now arabesque-fetch.timer
systemctl --user enable --now arabesque-report-daily.timer
systemctl --user enable --now arabesque-report-weekly.timer
systemctl --user enable --now arabesque-suivi-reminder.timer
```

---

## 5. Vérification post-restore

### 5.a Live engine actif

```bash
systemctl --user is-active arabesque-live.service  # → active
systemctl --user is-active arabesque-telegram-bot.service  # → active
```

### 5.b Feed 31/31 abonné + BarAggregators résumés récents

```bash
journalctl --user -u arabesque-live.service -n 100 | grep -E "BarAggregator|Subscribed"
```

Attendu : un résumé `Résumé subscriptions: 31/31 OK` ou équivalent dans les 5 premières minutes de boot.

### 5.c Watchdog OK

```bash
systemctl --user list-timers arabesque-feed-watchdog.timer
cat logs/feed_watchdog_state.json  # doit se mettre à jour toutes les 5 min
```

### 5.d Trade journal écrit correctement

```bash
tail -5 logs/trade_journal.jsonl  # nouvelles lignes au prochain tick
```

### 5.e Notifications Telegram + ntfy fonctionnelles

Test ponctuel :

```bash
./.venv/bin/python -c "
import asyncio, yaml, apprise
from pathlib import Path
secrets = yaml.safe_load(Path('config/secrets.yaml').read_text())
channels = secrets.get('notifications', {}).get('channels', [])
ap = apprise.Apprise()
for ch in channels:
    if isinstance(ch, str): ap.add(ch)
asyncio.run(ap.async_notify(body='Restore test OK', title='Arabesque RESTORE'))
"
```

Attendu : message reçu sur Telegram + ntfy.

### 5.f Health check global

```bash
./.venv/bin/python scripts/health_check.py --warn-only
echo "Exit code: $?"  # 0 = OK
```

### 5.g Tests unitaires

```bash
./.venv/bin/python -m pytest tests/ -q
```

Attendu : 85+ tests verts (mai 2026, peut évoluer).

---

## 6. Sanity check trading

Avant de laisser le live tourner, vérifier :

- `python -m arabesque positions --account ftmo_challenge` → liste cohérente avec le broker
- `python -m arabesque positions --account gft_compte1` → idem
- Aucune position orpheline (sans entry correspondant dans `logs/trade_journal.jsonl`)
- Protection level NORMAL pour les 2 brokers
- DD courant cohérent avec la balance broker

Si discordance, **arrêter le live** (`systemctl --user stop arabesque-live.service`) et investiguer avant de reprendre.

---

## 7. Erreurs courantes au restore

| Symptôme | Cause probable | Fix |
|---|---|---|
| `loginctl enable-linger` absent | services s'arrêtent à la déconnexion SSH | `sudo loginctl enable-linger $USER` |
| ImportError sur `arabesque` | `pip install -e .` raté dans install.sh | relancer `./install.sh` avec `set -x` |
| `ALREADY_LOGGED_IN` au boot cTrader | session précédente toujours active | attendre ~14 min (TTL serveur), patch A+B du 2026-05-18 gère ça automatiquement |
| Feed BarAggregator < 31/31 | DNS au boot, watchdog ne re-subscribe pas tout | restart manuel : `systemctl --user restart arabesque-live.service` |
| `config/secrets.yaml` introuvable | backup pas restauré | re-télécharger depuis le cloud chiffré |
| Telegram notif silencieuse | URL apprise mal échappée (HTML par défaut) | cf `feedback_telegram_html_pitfall.md` : forcer `body_format=TEXT` |

---

## 8. Cas dégradé : restore sans backup `logs/`

Si la perte est totale et qu'il n'y a aucun backup des `logs/`, l'historique trade est perdu. Le système peut néanmoins repartir vide :

- `config/secrets.yaml` doit être **recréé manuellement** : nouveau client OpenAPI cTrader, ré-enregistrement bot Telegram, nouveau topic ntfy.
- `logs/` se remplira au fur et à mesure des nouveaux trades.
- L'audit edge perd sa profondeur historique — il faudra attendre ~30 jours pour reconstituer une fenêtre rolling utile.
- Les bilans `logs/journal/*.md` sont perdus.

**Conclusion** : la priorité absolue du backup est `config/secrets.yaml` + `logs/trade_journal.jsonl` + `logs/journal/`. Le reste est récupérable ou regénérable.
