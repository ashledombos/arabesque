"""
Arabesque v2 — Webhook Server.

Reçoit les alertes TradingView et les passe à l'orchestrateur.
Référence : envolees-auto/webhook/server.py

Endpoints :
    POST /webhook          — Signal TradingView
    POST /update           — Mise à jour positions (bougie 1H)
    GET  /status           — État du système
    GET  /health           — Healthcheck

Usage :
    python -m arabesque.webhook.server
    # ou
    from arabesque.webhook.server import create_app
    app = create_app()
    app.run()
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, request, jsonify

from arabesque.config import load_config, ArabesqueConfig
from arabesque.broker.factory import create_all_brokers
from arabesque.webhook.orchestrator import Orchestrator

logger = logging.getLogger("arabesque.webhook")


def create_app(config: ArabesqueConfig | None = None) -> Flask:
    """Crée l'application Flask.

    Args:
        config: Configuration Arabesque (charge depuis YAML si None)
    """
    if config is None:
        config = load_config()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                Path(config.log_dir) / "webhook.log",
                mode="a",
            ),
        ],
    )
    Path(config.log_dir).mkdir(parents=True, exist_ok=True)

    # Créer et connecter les brokers
    logger.info(f"Mode: {config.mode}")
    logger.info(f"Creating brokers...")
    brokers = create_all_brokers(config.brokers)
    logger.info(f"Brokers connected: {list(brokers.keys())}")

    # Créer l'orchestrateur
    orchestrator = Orchestrator(config, brokers)

    # Flask app
    app = Flask(__name__)
    app.config["orchestrator"] = orchestrator
    app.config["arabesque_config"] = config

    # ── Routes ────────────────────────────────────────────────────

    @app.route("/webhook", methods=["POST"])
    def webhook():
        """Reçoit un signal TradingView.

        Le JSON est envoyé directement par l'alerte TradingView.
        Format attendu : voir pine/arabesque_signal.pine
        """
        # Auth (shared secret)
        if config.webhook_secret:
            auth = request.headers.get("X-Webhook-Secret", "")
            if auth != config.webhook_secret:
                # Aussi essayer dans le body
                data = _parse_body()
                if data.get("secret") != config.webhook_secret:
                    logger.warning(f"Unauthorized webhook request from {request.remote_addr}")
                    return jsonify({"error": "unauthorized"}), 401
            else:
                data = _parse_body()
        else:
            data = _parse_body()

        if not data:
            return jsonify({"error": "invalid JSON"}), 400

        logger.info(f"Webhook received: {data.get('symbol', '?')} "
                     f"{data.get('side', '?')}")

        # Passer à l'orchestrateur
        result = orchestrator.handle_signal(data)

        status_code = 200 if result.get("status") != "error" else 500
        return jsonify(result), status_code

    @app.route("/update", methods=["POST"])
    def update():
        """Met à jour les positions avec de nouvelles données OHLC.

        Body attendu :
        {
            "instrument": "EURUSD",
            "high": 1.0780,
            "low": 1.0750,
            "close": 1.0765,
            "indicators": {"rsi": 45, "cmf": 0.1, "bb_width": 0.004}
        }

        Peut aussi recevoir un batch :
        {"updates": [{"instrument": ..., "high": ..., ...}, ...]}
        """
        data = _parse_body()
        if not data:
            return jsonify({"error": "invalid JSON"}), 400

        all_actions = []

        # Batch ou single
        updates = data.get("updates", [data])

        for upd in updates:
            instrument = upd.get("instrument", "")
            high = float(upd.get("high", 0))
            low = float(upd.get("low", 0))
            close = float(upd.get("close", 0))
            indicators = upd.get("indicators")

            if not instrument or close <= 0:
                continue

            actions = orchestrator.update_positions(
                instrument, high, low, close, indicators
            )
            all_actions.extend(actions)

        return jsonify({"actions": all_actions}), 200

    @app.route("/status", methods=["GET"])
    def status():
        """Retourne l'état du système."""
        return jsonify(orchestrator.get_status()), 200

    @app.route("/health", methods=["GET"])
    def health():
        """Healthcheck."""
        return jsonify({
            "status": "ok",
            "mode": config.mode,
            "uptime": "running",
            "ts": datetime.now(timezone.utc).isoformat(),
        }), 200

    @app.route("/positions", methods=["GET"])
    def positions():
        """Liste les positions ouvertes et fermées."""
        open_pos = orchestrator.manager.open_positions
        closed_pos = orchestrator.manager.closed_positions

        return jsonify({
            "open": [
                {
                    "id": p.position_id,
                    "instrument": p.instrument,
                    "side": p.side.value,
                    "entry": p.entry,
                    "sl": p.sl,
                    "current_r": round(p.current_r, 2),
                    "mfe_r": round(p.mfe_r, 2),
                    "bars": p.bars_open,
                    "trailing_tier": p.trailing_tier,
                }
                for p in open_pos
            ],
            "closed_recent": [
                {
                    "id": p.position_id,
                    "instrument": p.instrument,
                    "side": p.side.value,
                    "result_r": round(p.result_r or 0, 2),
                    "exit_reason": p.exit_reason,
                    "bars": p.bars_open,
                }
                for p in closed_pos[-20:]  # Last 20
            ],
        }), 200

    return app


def _parse_body() -> dict:
    """Parse le body de la requête en JSON.

    TradingView peut envoyer du JSON pur ou du text.
    """
    try:
        if request.is_json:
            return request.get_json(silent=True) or {}
        # Essayer de parser le text comme JSON
        text = request.get_data(as_text=True)
        if text:
            return json.loads(text)
    except (json.JSONDecodeError, Exception):
        pass
    return {}


# ── Main ─────────────────────────────────────────────────────────────

def main():
    """Point d'entrée pour lancer le webhook."""
    config = load_config()
    app = create_app(config)

    logger.info(f"Starting Arabesque webhook server on "
                f"{config.webhook_host}:{config.webhook_port}")
    logger.info(f"Mode: {config.mode}")

    app.run(
        host=config.webhook_host,
        port=config.webhook_port,
        debug=False,
    )


if __name__ == "__main__":
    main()
