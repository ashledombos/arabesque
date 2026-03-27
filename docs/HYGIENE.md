# Arabesque — Règles d'Hygiène du Code

> Lire avant de coder. Respecter systématiquement.
> Ces règles évitent que le projet devienne ingérable entre les sessions.

---

## 1. Principe fondamental

**Ce qui est construit ne doit pas être déconstruit sans raison valide.**

Avant toute modification structurelle : poser la question *"Est-ce que je
résous un vrai problème ou est-ce que je réorganise par goût personnel ?"*
Si la réponse n'est pas clairement un vrai problème, ne pas toucher.

---

## 2. Structure des stratégies

Une stratégie = **un dossier** dans `arabesque/strategies/` avec :

```
arabesque/strategies/nom_danse/
├── __init__.py        # Exports + metadata
├── signal.py          # LE générateur de signal — unique pour backtest ET live
├── params.yaml        # Jeux de paramètres nommés (presets)
└── STRATEGY.md        # Fiche : description, résultats, limites, décisions
```

**Règle d'or** : `signal.py` est le fichier unique utilisé par le backtest,
le dry-run, et le live. Une seule implémentation, zéro divergence possible.

**Seul Claude Opus 4.6** peut modifier `signal.py` d'une stratégie validée.

---

## 3. Nommage des stratégies

Les stratégies portent des noms tirés des **disciplines artistiques de la
souplesse et de l'acrobatie gracieuse** — famille large incluant :
danse classique, barre au sol, Gymnastique Rythmique (GR),
Gymnastique Artistique Féminine (GAF), Gymnastique Acrobatique,
danse aérienne, Body Ballet / Pilates Danse, contorsionnisme artistique,
natation artistique, et disciplines proches.

Ces disciplines partagent : vocabulaire français, emphase sur la posture,
coordination et expression corporelle, musique classique ou néo-classique.
Elles sont souvent féminines et olympiques ou proches.

Le nom doit être :
- **en français**
- **suffisamment évocateur** pour qu'une relation imagée existe avec la logique
  de la stratégie (mouvement de prix ↔ mouvement du corps)
- expliqué dans la fiche `STRATEGY.md` (étymologie + relation avec la stratégie)

Exemples de correspondances :
- *Extension* → trend-following : allongement du membre dans la continuité du corps
  = prix qui s'étend hors des bandes après compression
- *Arabesque* → le projet lui-même : équilibre sur une jambe, pose distinctive
- *Développé* → potentielle stratégie à déploiement progressif (mean-reversion ?)
- *Ruban* (GR) → stratégie fluide et continue ?
- *Spirale* (natation artistique) → rotation de régime ?

**Noms disponibles (à attribuer selon logique — non exhaustif) :**

| Discipline | Noms |
|---|---|
| Danse classique | Développé, Tombé, Fouetté, Grand Jeté, Battement, Pivot, Enroulé, Plié |
| GR | Ruban, Cerceau, Ballon, Rotation, Massue, Corde |
| GAF | Vrille, Salto, Rondade, Appui, Renversement |
| Acrobatique | Portée, Voltigeur, Pyramide, Catapulte |
| Danse aérienne | Ailé, Envol, Suspension, Vrille aérienne |
| Natation artistique | Spirale, Barracuda, Flamant, Cosaque |
| Contorsionnisme | Contraction, Cambrure, Pont, Torsion |
| Body Ballet | Arabesque au sol, Relevé, Penché |

---

## 4. Scripts : règle du tiroir propre

`scripts/` contient **uniquement** des outils CLI permanents, réutilisés
régulièrement. Maximum ~6 fichiers.

Scripts permanents autorisés :
- `backtest.py` — backtest IS+OOS
- `analyze.py` — analyse logs live/dry-run
- `run_pipeline.py` — screening multi-instruments
- `run_stats.py` — statistiques approfondies
- `fetch_data.py` — téléchargement données
- `check_broker.py` — test connectivité

**Tout le reste va dans `tmp/`** (gitignored). Exemples de code temporaire :
diagnostics one-off, explorations, scripts de migration, tests ad-hoc.

```bash
# Créer un script temporaire
vim tmp/diagnose_btcusd_spike.py
# → gitignored, visible localement, pas dans le repo
```

Si un script `tmp/` devient utile régulièrement → le migrer dans `scripts/`
avec une PR explicite.

---

## 5. Modules réutilisables

Code réutilisable entre stratégies → `arabesque/modules/`.
Code spécifique à une stratégie → dans son dossier `arabesque/strategies/*/`.

```python
# ✅ Bon : indicateur dans modules/
from arabesque.modules.indicators import compute_rsi

# ✅ Bon : signal spécifique dans la stratégie
from arabesque.strategies.extension.signal import ExtensionSignalGenerator

# ❌ Mauvais : logique d'indicateur dans le signal generator
```

---

## 6. Documentation

Documents principaux :

| Document | Rôle | Mise à jour |
|---|---|---|
| `CLAUDE.md` | Instructions pour Claude Code | Quand les règles changent |
| `HANDOFF.md` | Reprise de session + état courant | **Chaque fin de session** |
| `docs/STATUS.md` | Snapshot opérationnel live | **Chaque changement de config/compte** |
| `docs/DECISIONS.md` | Décisions techniques + historique | À chaque décision importante |
| `docs/EXPERIMENT_LOG.md` | Paramètres testés + résultats | **Chaque expérimentation** |
| `docs/HYGIENE.md` | Ce document | Quand les règles changent |

Chaque stratégie a aussi son `STRATEGY.md` dans `arabesque/strategies/*/`.

**Fin de session** — obligatoire :
1. `HANDOFF.md` : état actuel, bugs ouverts, prochaines étapes
2. `docs/DECISIONS.md` : toute nouvelle décision technique
3. `docs/STATUS.md` : si la config live, les balances ou les notifications ont changé
4. `docs/EXPERIMENT_LOG.md` : si des paramètres ou stratégies ont été testés

---

## 7. Stable vs fragile

### Immuable (ne pas toucher sans rejeu complet)
- `arabesque/core/models.py` — types de données
- `arabesque/core/guards.py` — règles prop firm
- `arabesque/strategies/extension/signal.py` — logique de signal validée
- `arabesque/modules/position_manager.py` — TSL/BE (edge principal)
- `config/prop_firms.yaml` — mapping instruments

### Stable (modifier avec précaution, tests obligatoires)
- `arabesque/execution/live.py` — moteur live en production
- `arabesque/execution/dryrun.py` — replay parquet
- `arabesque/broker/*.py` — connecteurs broker

### Évolutif (peut changer librement)
- `arabesque/analysis/*.py` — métriques et rapports
- `arabesque/data/*.py` — fetch et store
- `scripts/*.py` — outils CLI
- `docs/` — documentation
- `config/settings.yaml` — paramètres runtime

---

## 8. Imports : règle des nouveaux chemins

Depuis la restructuration (mars 2026), les chemins canoniques sont :

```python
# ✅ Nouveaux chemins (à utiliser dans tout nouveau code)
from arabesque.core.models import Signal, Position
from arabesque.core.guards import Guards, PropConfig
from arabesque.core.audit import AuditLogger
from arabesque.modules.indicators import compute_rsi
from arabesque.modules.position_manager import PositionManager
from arabesque.strategies.extension.signal import ExtensionSignalGenerator
from arabesque.data.store import load_ohlc
from arabesque.analysis.metrics import compute_metrics
from arabesque.execution.live import LiveEngine

# ⚠️ Anciens chemins (toujours fonctionnels via shims, mais dépréciés)
# from arabesque.models import Signal          # → arabesque.core.models
# from arabesque.guards import Guards          # → arabesque.core.guards
# from arabesque.live.engine import LiveEngine # → arabesque.execution.live
```

---

## 9. Tests

Les tests automatisés sont un placeholder pour l'instant.
`tests/TEST_SCENARIOS.md` documente les 35 scénarios de validation manuelle.

Avant de déployer un changement de stratégie ou de moteur :
→ Rejeu `python -m arabesque run --strategy extension --mode dryrun` sur 3 mois
→ Comparer les métriques avec la baseline documentée dans `HANDOFF.md`

---

## 10. Sécurité comptes

Les comptes avec des fonds réels **doivent** être marqués `protected: true`
dans `config/accounts.yaml`. Le moteur live vérifie ce flag au démarrage et
refuse sans `--force-live` explicite.

Ne jamais hardcoder de credentials dans le code. Tout dans `config/secrets.yaml`.
