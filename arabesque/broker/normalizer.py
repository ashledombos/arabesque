"""
Arabesque — Broker Normalizer.

Gère la validation et la normalisation des ordres pour chaque type de broker.
Évite les rejets TRADING_BAD_VOLUME / TRADING_BAD_STOPS en validant AVANT envoi.

Usage dans le dispatcher:
    from arabesque.broker.normalizer import validate_order
    result = validate_order(broker, symbol, volume_lots, sl, tp, entry)
    if not result.valid:
        logger.warning(f"Ordre rejeté en pré-vol: {result.reason}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("arabesque.broker.normalizer")


@dataclass
class ValidationResult:
    """Résultat de la validation pré-envoi."""
    valid: bool
    volume_lots: float = 0.0     # Volume ajusté (lots)
    stop_loss: float = 0.0       # SL arrondi
    take_profit: float = 0.0     # TP arrondi
    entry_price: float = 0.0     # Entry arrondi
    reason: str = ""             # Raison du rejet si invalid


def validate_order(
    broker,
    symbol: str,
    volume_lots: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    entry_price: Optional[float] = None,
    side: str = "BUY",
) -> ValidationResult:
    """Valide et normalise un ordre avant envoi au broker.

    Vérifie:
    - Volume min/max/step du symbole sur ce broker
    - Arrondi du SL/TP aux digits autorisés
    - Cohérence SL/TP vs entry vs side

    Args:
        broker: Instance BaseBroker (CTraderBroker ou TradeLockerBroker)
        symbol: Nom unifié (EURUSD, BTCUSD)
        volume_lots: Volume calculé en lots
        stop_loss: Prix du stop loss
        take_profit: Prix du take profit
        entry_price: Prix d'entrée
        side: "BUY" ou "SELL"

    Returns:
        ValidationResult avec volume/prix ajustés ou raison du rejet
    """
    # Chercher le SymbolInfo du broker
    sym_info = None
    if hasattr(broker, '_symbols'):
        for sinfo in broker._symbols.values():
            if sinfo.symbol == symbol:
                sym_info = sinfo
                break

    if sym_info is None:
        # Pas d'info symbole → validation basique
        return ValidationResult(
            valid=True,
            volume_lots=volume_lots,
            stop_loss=stop_loss or 0,
            take_profit=take_profit or 0,
            entry_price=entry_price or 0,
        )

    # --- Volume validation ---
    min_vol = sym_info.min_volume
    max_vol = sym_info.max_volume
    step = sym_info.volume_step

    if volume_lots < min_vol:
        return ValidationResult(
            valid=False,
            reason=f"Volume {volume_lots:.4f}L < minimum {min_vol:.4f}L "
                   f"pour {symbol}. Augmenter risk_percent ou réduire instruments."
        )

    if volume_lots > max_vol:
        volume_lots = max_vol
        logger.warning(
            f"[Normalizer] Volume capé: {symbol} {volume_lots:.3f}L → "
            f"max {max_vol:.3f}L"
        )

    # Arrondir au step
    if step > 0:
        volume_lots = max(min_vol, round(round(volume_lots / step) * step, 8))

    # --- Price rounding ---
    digits = sym_info.digits
    rounded_sl = round(stop_loss, digits) if stop_loss else 0
    rounded_tp = round(take_profit, digits) if take_profit else 0
    rounded_entry = round(entry_price, digits) if entry_price else 0

    # --- SL/TP cohérence ---
    if rounded_entry > 0 and rounded_sl > 0:
        if side == "BUY" and rounded_sl >= rounded_entry:
            return ValidationResult(
                valid=False,
                reason=f"SL {rounded_sl} >= entry {rounded_entry} pour BUY {symbol}"
            )
        if side == "SELL" and rounded_sl <= rounded_entry:
            return ValidationResult(
                valid=False,
                reason=f"SL {rounded_sl} <= entry {rounded_entry} pour SELL {symbol}"
            )

    return ValidationResult(
        valid=True,
        volume_lots=volume_lots,
        stop_loss=rounded_sl,
        take_profit=rounded_tp,
        entry_price=rounded_entry,
    )


def get_broker_volume_info(broker, symbol: str) -> dict:
    """Retourne les contraintes de volume d'un symbole sur un broker.

    Utile pour le debugging et les logs.
    """
    sym_info = None
    if hasattr(broker, '_symbols'):
        for sinfo in broker._symbols.values():
            if sinfo.symbol == symbol:
                sym_info = sinfo
                break

    if sym_info is None:
        return {"symbol": symbol, "error": "Symbol not found in broker"}

    broker_type = type(broker).__name__
    centilots_factor = 100 if "CTrader" in broker_type else 1

    return {
        "symbol": symbol,
        "broker_type": broker_type,
        "min_volume_lots": sym_info.min_volume,
        "max_volume_lots": sym_info.max_volume,
        "volume_step_lots": sym_info.volume_step,
        "lot_size": sym_info.lot_size,
        "digits": sym_info.digits,
        "centilots_factor": centilots_factor,
        "min_broker_units": int(sym_info.min_volume * centilots_factor),
        "max_broker_units": int(sym_info.max_volume * centilots_factor),
    }
