"""
Arabesque v2 — Configuration.

Charge les settings depuis un fichier YAML.
Référence : envolees-auto/config/settings.yaml
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ArabesqueConfig:
    """Configuration globale Arabesque."""
    # ── Brokers ──
    brokers: list[dict] = field(default_factory=list)

    # ── Webhook ──
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 5000
    webhook_secret: str = ""           # Shared secret pour valider les requêtes

    # ── Prop firm ──
    start_balance: float = 100_000.0
    risk_per_trade_pct: float = 0.5
    max_daily_dd_pct: float = 3.0
    max_total_dd_pct: float = 8.0
    max_positions: int = 3
    max_daily_trades: int = 10

    # ── Execution ──
    max_spread_atr: float = 0.15
    max_slippage_atr: float = 0.10
    signal_expiry_sec: int = 300
    min_rr: float = 0.5

    # ── Logging ──
    log_dir: str = "logs"
    audit_dir: str = "logs/audit"
    log_level: str = "INFO"

    # ── Notifications ──
    telegram_token: str = ""
    telegram_chat_id: str = ""
    ntfy_topic: str = ""
    ntfy_url: str = "https://ntfy.sh"

    # ── Mode ──
    mode: str = "dry_run"             # "dry_run", "paper", "live"
    instruments: list[str] = field(default_factory=list)


def load_config(path: str | Path = "config/settings.yaml") -> ArabesqueConfig:
    """Charge la configuration depuis un fichier YAML.

    Aussi supporte les variables d'environnement :
        ARABESQUE_CONFIG_PATH : chemin vers le fichier
        ARABESQUE_MODE : override du mode
        ARABESQUE_SECRET : webhook secret

    Args:
        path: Chemin vers le fichier YAML

    Returns:
        ArabesqueConfig
    """
    # Override path via env
    env_path = os.environ.get("ARABESQUE_CONFIG_PATH")
    if env_path:
        path = env_path

    path = Path(path)
    data: dict[str, Any] = {}

    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    else:
        # Pas de fichier → config par défaut (dry_run)
        pass

    # Construire le config
    config = ArabesqueConfig()

    # Mapper les clés YAML vers les champs
    for key, value in data.items():
        if hasattr(config, key):
            setattr(config, key, value)

    # Env overrides
    if os.environ.get("ARABESQUE_MODE"):
        config.mode = os.environ["ARABESQUE_MODE"]
    if os.environ.get("ARABESQUE_SECRET"):
        config.webhook_secret = os.environ["ARABESQUE_SECRET"]
    if os.environ.get("ARABESQUE_TELEGRAM_TOKEN"):
        config.telegram_token = os.environ["ARABESQUE_TELEGRAM_TOKEN"]
    if os.environ.get("ARABESQUE_TELEGRAM_CHAT_ID"):
        config.telegram_chat_id = os.environ["ARABESQUE_TELEGRAM_CHAT_ID"]

    # S'assurer qu'il y a au moins un broker
    if not config.brokers:
        config.brokers = [{"type": "dry_run", "name": "dry_run"}]

    return config


def generate_default_config(path: str = "config/settings.yaml"):
    """Génère un fichier de configuration par défaut."""
    default = """# ═══════════════════════════════════════════════════════════
# Arabesque v2 — Configuration
# ═══════════════════════════════════════════════════════════

# Mode : dry_run | paper | live
mode: dry_run

# ── Brokers ──────────────────────────────────────────────
brokers:
  # cTrader (FTMO)
  - type: ctrader
    name: ctrader_ftmo
    host: demo.ctraderapi.com
    port: 5035
    client_id: ""       # cTrader Open API client ID
    client_secret: ""   # cTrader Open API client secret
    access_token: ""    # OAuth2 access token
    account_id: 0       # ctidTraderAccountId

  # TradeLocker (GFT)
  - type: tradelocker
    name: tradelocker_gft
    email: ""
    password: ""
    server: live
    base_url: https://bsb.tradelocker.com
    account_id: 0

  # Dry-run (toujours actif pour paper trading)
  - type: dry_run
    name: dry_run

# ── Webhook ──────────────────────────────────────────────
webhook_host: "0.0.0.0"
webhook_port: 5000
webhook_secret: ""    # Set via ARABESQUE_SECRET env var

# ── Prop firm constraints ────────────────────────────────
start_balance: 100000
risk_per_trade_pct: 0.5
max_daily_dd_pct: 3.0
max_total_dd_pct: 8.0
max_positions: 3
max_daily_trades: 10

# ── Execution guards ────────────────────────────────────
max_spread_atr: 0.15
max_slippage_atr: 0.10
signal_expiry_sec: 300
min_rr: 0.5

# ── Instruments autorisés ────────────────────────────────
instruments:
  - EURUSD
  - GBPUSD
  - USDJPY
  - AUDUSD
  - XAUUSD

# ── Logging ──────────────────────────────────────────────
log_dir: logs
audit_dir: logs/audit
log_level: INFO

# ── Notifications ────────────────────────────────────────
telegram_token: ""     # Set via ARABESQUE_TELEGRAM_TOKEN env var
telegram_chat_id: ""   # Set via ARABESQUE_TELEGRAM_CHAT_ID env var
ntfy_topic: arabesque
ntfy_url: https://ntfy.sh
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(default)
    return path
