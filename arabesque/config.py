"""
Arabesque — Configuration.

Deux modes :
  1. load_config() — charge ArabesqueConfig depuis un YAML (usage legacy/runner)
  2. load_full_config() — charge settings + secrets + instruments (nouveau pipeline)
     Retourne (settings: dict, secrets: dict, instruments: dict)

Fonctions utilitaires :
  - update_broker_tokens() : met à jour les tokens dans secrets.yaml après un refresh
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("arabesque.config")


@dataclass
class ArabesqueConfig:
    """Configuration globale Arabesque (usage legacy runner)."""
    # ── Brokers ──
    brokers: list[dict] = field(default_factory=list)

    # ── Webhook ──
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 5000
    webhook_secret: str = ""

    # ── Prop firm ──
    start_balance: float = 100_000.0
    risk_per_trade_pct: float = 0.5
    max_daily_dd_pct: float = 3.0
    max_total_dd_pct: float = 8.0
    max_positions: int = 10
    max_open_risk_pct: float = 2.0
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
    mode: str = "dry_run"
    instruments: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str) -> "ArabesqueConfig":
        """Charge depuis un fichier YAML."""
        return load_config(path)


# =============================================================================
# load_config — usage legacy (runner.py)
# =============================================================================

def load_config(path: str | Path = "config/settings.yaml") -> ArabesqueConfig:
    """Charge la configuration depuis un fichier YAML."""
    env_path = os.environ.get("ARABESQUE_CONFIG_PATH")
    if env_path:
        path = env_path
    path = Path(path)
    data: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}

    config = ArabesqueConfig()
    for key, value in data.items():
        if hasattr(config, key):
            setattr(config, key, value)

    if os.environ.get("ARABESQUE_MODE"):
        config.mode = os.environ["ARABESQUE_MODE"]
    if os.environ.get("ARABESQUE_SECRET"):
        config.webhook_secret = os.environ["ARABESQUE_SECRET"]
    if os.environ.get("ARABESQUE_TELEGRAM_TOKEN"):
        config.telegram_token = os.environ["ARABESQUE_TELEGRAM_TOKEN"]
    if os.environ.get("ARABESQUE_TELEGRAM_CHAT_ID"):
        config.telegram_chat_id = os.environ["ARABESQUE_TELEGRAM_CHAT_ID"]

    if not config.brokers:
        config.brokers = [{"type": "dry_run", "name": "dry_run"}]

    return config


# =============================================================================
# load_full_config — nouveau pipeline multi-brokers
# =============================================================================

def load_full_config(
    settings_path: str | Path = "config/settings.yaml",
    secrets_path: str | Path = "config/secrets.yaml",
    instruments_path: str | Path = "config/instruments.yaml",
) -> tuple[dict, dict, dict]:
    """
    Charge et retourne (settings, secrets, instruments) comme dicts bruts.

    - settings    : config/settings.yaml
    - secrets     : config/secrets.yaml (credentials, absent = {})
    - instruments : config/instruments.yaml fusionné avec settings[instruments]
                    (instruments.yaml a priorité si les deux existent)

    Fusion notifications :
      settings.notifications.channels a priorité.
      Si vide/absent, utilise secrets.notifications.channels.
      Le résultat est stocké dans settings.notifications.channels
      pour que l'engine n'ait qu'une source à lire.

    Si secrets.yaml n'existe pas, un avertissement est émis mais pas d'erreur.
    """
    settings_path = Path(settings_path)
    secrets_path = Path(secrets_path)
    instruments_path = Path(instruments_path)

    # --- Settings ---
    if not settings_path.exists():
        logger.warning(
            f"settings.yaml introuvable : {settings_path} — "
            f"utilisez config/settings.example.yaml comme base"
        )
        settings: dict = {}
    else:
        with open(settings_path) as f:
            settings = yaml.safe_load(f) or {}

    # --- Secrets ---
    if not secrets_path.exists():
        logger.warning(
            f"secrets.yaml introuvable : {secrets_path} — "
            f"aucun credential chargé (mode dry_run ou variables d'env ?)"
        )
        secrets: dict = {}
    else:
        with open(secrets_path) as f:
            secrets = yaml.safe_load(f) or {}

    # --- Fusion notifications.channels ---
    # settings.notifications.channels a la priorité.
    # Fallback : secrets.notifications.channels (contient les URLs avec tokens).
    notif_settings = settings.setdefault("notifications", {})
    if not notif_settings.get("channels"):
        notif_secrets = secrets.get("notifications", {})
        secret_channels = notif_secrets.get("channels", [])
        if secret_channels:
            notif_settings["channels"] = secret_channels
            logger.debug(
                "[config] notifications.channels chargés depuis secrets.yaml"
            )

    # --- Instruments ---
    # Base : instruments dans settings["instruments"] (peut être {})
    instruments_from_settings = settings.get("instruments", {})
    instruments: dict = {}

    if instruments_path.exists():
        with open(instruments_path) as f:
            instruments_from_file = yaml.safe_load(f) or {}
        # Fusion : instruments.yaml a priorité sur settings[instruments]
        instruments = {**instruments_from_settings, **instruments_from_file}
    else:
        instruments = instruments_from_settings

    return settings, secrets, instruments


# =============================================================================
# update_broker_tokens — persistance des tokens rafraîchis
# =============================================================================

def update_broker_tokens(
    broker_id: str,
    access_token: str,
    refresh_token: str,
    secrets_path: str | Path = "config/secrets.yaml",
) -> bool:
    """
    Met à jour les tokens cTrader dans secrets.yaml après un refresh OAuth2.

    Appelé par CTraderBroker._save_tokens_to_config() à chaque refresh.
    Retourne True si la sauvegarde a réussi, False sinon.

    Le fichier est lu, modifié en mémoire, puis réécrit atomiquement.
    Les autres credentials (email, passwords) ne sont pas touchés.
    """
    secrets_path = Path(secrets_path)

    if not secrets_path.exists():
        logger.warning(
            f"[update_broker_tokens] secrets.yaml introuvable : {secrets_path}. "
            f"Tokens non sauvegardés (ils seront perdus au prochain redémarrage)."
        )
        return False

    try:
        with open(secrets_path) as f:
            data = yaml.safe_load(f) or {}

        if broker_id not in data:
            data[broker_id] = {}

        data[broker_id]["access_token"] = access_token
        data[broker_id]["refresh_token"] = refresh_token

        # Écriture atomique via fichier temporaire
        tmp_path = secrets_path.with_suffix(".yaml.tmp")
        with open(tmp_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        tmp_path.replace(secrets_path)

        logger.info(
            f"[update_broker_tokens] ✅ Tokens sauvegardés pour {broker_id} "
            f"dans {secrets_path}"
        )
        return True

    except Exception as e:
        logger.error(f"[update_broker_tokens] ❌ Erreur: {e}")
        return False


# =============================================================================
# generate_default_config
# =============================================================================

def generate_default_config(path: str = "config/settings.yaml"):
    """Génère un fichier de configuration par défaut."""
    default = """# ═══════════════════════════════════════════════════════
# Arabesque — Configuration principale
# Voir config/settings.example.yaml pour tous les paramètres
# ═══════════════════════════════════════════════════════

mode: dry_run

brokers:
  ftmo_ctrader:
    enabled: true
    type: ctrader
    name: "FTMO"
    is_demo: true
    auto_refresh_token: true
    account_id: null

price_feed:
  source_broker: ftmo_ctrader
  symbols: []  # Vide = tous les instruments avec follow: true dans instruments.yaml

filters:
  min_margin_percent: 30
  max_daily_drawdown_percent: 4.0
  max_total_drawdown_percent: 8.0
  max_open_positions: 5
  max_pending_orders: 10
  prevent_duplicate_orders: true

instruments: {}

notifications:
  enabled: false
  channels: []  # Mettre dans secrets.yaml : notifications.channels
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(default)
    return path
