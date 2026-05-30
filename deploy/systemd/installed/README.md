# `deploy/systemd/installed/` — snapshot 1:1 des unités systemd user installées

Ces fichiers reproduisent à l'identique le contenu de `~/.config/systemd/user/arabesque-*` au moment du snapshot.

## Différence avec `deploy/systemd/*.template`

Les `*.service.template` du dossier parent sont des modèles paramétrables (path et user à substituer). Les fichiers de `installed/` sont des copies exactes utilisables tel quel **si la machine de restore reproduit l'environnement de l'utilisateur original** (username, chemins absolus).

## Contenu actuel

| Fichier | Pourquoi ici (et pas en `.template` parent) |
|---|---|
| `arabesque-feed-watchdog.service` | Aucune version template versionnée — créée le 2026-05-14 sans passer par le pipeline templates. |
| `arabesque-feed-watchdog.timer` | Idem. |
| `arabesque-telegram-bot.service` | Idem — créée le 2026-05-03 lors du déploiement du bot phase 1. |
| `arabesque-execution-integrity.service` | Snapshot exact de l'audit quotidien d'intégrité d'exécution installé le 2026-05-30. |
| `arabesque-execution-integrity.timer` | Snapshot exact du timer quotidien associé. |

## Vérification no-secret au moment du snapshot (2026-05-19)

Les fichiers contiennent uniquement :
- `WorkingDirectory=/var/home/machine/dev/arabesque`
- `ExecStart=/var/home/machine/dev/arabesque/.venv/bin/python ...`
- Métadonnées systemd standard (`Restart`, `Type`, `Description`, `After`).

**Aucun token, mot de passe ou variable d'environnement contenant un secret.** Les credentials sont chargés depuis `config/secrets.yaml` à l'exécution.

## Restauration

```bash
# Sur la machine cible, après clone du repo dans le même chemin :
mkdir -p ~/.config/systemd/user
cp deploy/systemd/installed/*.service ~/.config/systemd/user/
cp deploy/systemd/installed/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now arabesque-feed-watchdog.timer
systemctl --user enable --now arabesque-telegram-bot.service
systemctl --user enable --now arabesque-execution-integrity.timer
```

Si la machine cible a un chemin différent, éditer les `WorkingDirectory=` et `ExecStart=` avant `daemon-reload`. Une version `.template` futur peut être ajoutée pour automatiser la substitution (non urgent tant que la machine de prod reste unique).

## Maintenance

Ce dossier n'est **pas** auto-synchronisé. Quand une unité change ou qu'on en ajoute une nouvelle :

```bash
cp ~/.config/systemd/user/arabesque-<nom>.service deploy/systemd/installed/
git add deploy/systemd/installed/ && git commit -m "deploy(systemd): snapshot <nom>"
```

Vérifier l'absence de secrets avant chaque commit.
