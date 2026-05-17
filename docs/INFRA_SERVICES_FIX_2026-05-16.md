# Infra services — corrections 2026-05-16

Contexte: après reprise live, deux services systemd utilisateur apparaissaient en `failed`:

- `arabesque-report-daily.service`
- `arabesque-suivi-reminder.service`

Ces corrections ne touchent pas au moteur live, aux stratégies, au broker layer, au dispatcher, ni à la logique de sizing.

## 1. `arabesque-report-daily.service`

Cause observée:

- Le service lance `scripts/health_check.py --notify --warn-only` en `ExecStartPost`.
- `health_check.py` remontait correctement des alertes, mais sortait avec code `2` en présence de CRIT.
- Résultat: le rapport quotidien était marqué `failed` même quand le comportement attendu était seulement d'envoyer/afficher une alerte.

Correction:

- `scripts/health_check.py`
- `--warn-only` devient explicitement un mode timer/reporting:
  - notification seulement si WARN/CRIT;
  - exit code `0` même si des alertes sont présentes.

Validation:

- `.venv/bin/python -m py_compile scripts/health_check.py`
- `.venv/bin/python scripts/health_check.py --warn-only`
- Le script affiche toujours les alertes, mais sort en code `0`.

Note:

- Le mode sans `--warn-only` conserve les exit codes stricts:
  - `2` si CRIT;
  - `1` si WARN;
  - `0` si clean.

## 2. `arabesque-suivi-reminder.service`

Cause observée:

- Le service installé lance `/usr/bin/env python3 scripts/suivi_reminder.py`.
- Le Python système ne voit pas `apprise`.
- Le template dans `deploy/systemd/arabesque-suivi-reminder.service.template` utilise déjà `.venv/bin/python`, mais le service utilisateur installé localement était encore sur `/usr/bin/env python3`.

Correction:

- `scripts/suivi_reminder.py`
- L'import `apprise` est chargé via une fonction `_load_apprise()`.
- Si `apprise` n'est pas disponible dans l'interpréteur courant, le script ajoute les `site-packages` de `.venv` au `sys.path`, puis réessaie.

Validation:

- `.venv/bin/python -m py_compile scripts/suivi_reminder.py`
- `/usr/bin/env python3 -m py_compile scripts/suivi_reminder.py`
- `/usr/bin/env python3 -c "import scripts.suivi_reminder as s; print(s._load_apprise().__version__)"`
- `systemctl --user start arabesque-suivi-reminder.service`
- Résultat systemd confirmé: `Result=success`, `ExecMainStatus=0`.

## État systemd après correction

Après `systemctl --user reset-failed arabesque-report-daily.service arabesque-suivi-reminder.service`:

- `arabesque-report-daily.service`: `inactive`, `Result=success`, `ExecMainStatus=0`
- `arabesque-suivi-reminder.service`: `inactive`, `Result=success`, `ExecMainStatus=0`

Timers observés actifs:

- `arabesque-feed-watchdog.timer`
- `arabesque-suivi-reminder.timer`
- `arabesque-fetch.timer`
- `arabesque-report-daily.timer`
- `arabesque-report-weekly.timer`

## Points à transmettre

- Ces changements sont de l'outillage opérationnel, pas de la logique de trading.
- Le service installé `arabesque-suivi-reminder.service` devrait idéalement être réaligné avec le template et utiliser `.venv/bin/python`.
- La correction `_load_apprise()` est un filet de compatibilité pour éviter un retour en failed si l'ancien unit reste installé.
- `health_check --warn-only` est maintenant le mode adapté aux timers de reporting; utiliser le mode strict sans `--warn-only` pour CI ou diagnostic manuel.
