#!/usr/bin/env python
"""
Arabesque — Debug pipeline.

Affiche le contrat exact de CombinedSignalGenerator :
  - colonnes produites par prepare()
  - exemples de signaux générés
  - champs de l'objet Signal
  - dict produit par _signal_to_webhook_dict()

Usage :
    python scripts/debug_pipeline.py
    python scripts/debug_pipeline.py --instrument BCHUSD --bars 200
    python scripts/debug_pipeline.py --instrument XRPUSD --show-signals 5
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Debug pipeline Arabesque")
    parser.add_argument("--instrument", default="BCHUSD")
    parser.add_argument("--bars", type=int, default=300)
    parser.add_argument("--show-signals", type=int, default=3, metavar="N")
    args = parser.parse_args()

    instrument = args.instrument
    sep = "─" * 60

    # ── 1. Champs du dataclass Signal ────────────────────────────────
    print(f"\n{sep}")
    print("1. CHAMPS DU DATACLASS Signal")
    print(sep)
    try:
        from arabesque.models import Signal
        for f in dataclasses.fields(Signal):
            if f.default is not dataclasses.MISSING:
                default = repr(f.default)
            elif f.default_factory is not dataclasses.MISSING:  # type: ignore
                default = "<factory>"
            else:
                default = "<required>"
            print(f"  {f.name:30s}  default={default}")
    except Exception as e:
        print(f"  ERREUR : {e}")

    # ── 2. Chargement des données ─────────────────────────────────────
    print(f"\n{sep}")
    print(f"2. CHARGEMENT — {instrument} ({args.bars} barres)")
    print(sep)
    df = None
    try:
        from arabesque.backtest.data import load_ohlc
        df = load_ohlc(instrument, prefer_parquet=True)
        if df is None or len(df) == 0:
            print(f"  ERREUR : aucune donnée pour {instrument}")
            sys.exit(1)
        df = df.tail(args.bars)
        print(f"  OK — {len(df)} barres")
        print(f"  Colonnes brutes : {df.columns.tolist()}")
        print(f"  Index dtype={df.index.dtype} | {df.index[0]} → {df.index[-1]}")
    except Exception as e:
        print(f"  ERREUR : {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # ── 3. prepare() ──────────────────────────────────────────────────
    print(f"\n{sep}")
    print("3. COLONNES APRÈS prepare()")
    print(sep)
    sig_gen = None
    df_prep = None
    try:
        from arabesque.backtest.signal_gen_combined import CombinedSignalGenerator
        sig_gen = CombinedSignalGenerator()
        df_prep = sig_gen.prepare(df)
        cols = df_prep.columns.tolist()
        print(f"  {len(cols)} colonnes :")
        for c in cols:
            v = df_prep[c].iloc[-1]
            print(f"    {c:30s}  last={v:.6g}" if isinstance(v, float) else f"    {c:30s}  last={v}")
        nan_cols = [c for c in cols if df_prep[c].isna().any()]
        if nan_cols:
            print(f"  ⚠ Colonnes avec NaN : {nan_cols}")
        else:
            print("  ✓ Aucune colonne avec NaN")
    except Exception as e:
        print(f"  ERREUR : {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # ── 4. generate_signals() ─────────────────────────────────────────
    print(f"\n{sep}")
    print("4. generate_signals()")
    print(sep)
    all_signals = []
    try:
        all_signals = sig_gen.generate_signals(df_prep, instrument)
        print(f"  {len(all_signals)} signal(s) sur {len(df_prep)} barres")
        if all_signals:
            for idx, sig in all_signals[:args.show_signals]:
                ts = df_prep.index[idx] if idx < len(df_prep) else "?"
                print(f"    [{idx}] {ts} | side={sig.side} | strategy={sig.strategy_type}")
                print(f"          sl={sig.sl:.6g} | tp_indicative={sig.tp_indicative:.6g} | rr={sig.rr:.3f}")
                print(f"          rsi={sig.rsi:.1f} | bb_width={sig.bb_width:.6g} | atr={sig.atr:.6g}")
        else:
            print("  (aucun signal sur cette période)")
    except Exception as e:
        print(f"  ERREUR : {e}")
        import traceback; traceback.print_exc()

    # ── 5. _signal_to_webhook_dict() ──────────────────────────────────
    print(f"\n{sep}")
    print("5. DICT _signal_to_webhook_dict() (dernier signal)")
    print(sep)
    if all_signals:
        try:
            _, sig = all_signals[-1]
            # Affichage direct du signal (Signal.from_webhook_json compatible)
            d = {
                "instrument": sig.instrument, "side": sig.side.value,
                "close": sig.close, "sl": sig.sl, "tp_indicative": sig.tp_indicative,
                "atr": sig.atr, "rsi": sig.rsi, "cmf": sig.cmf,
                "bb_lower": sig.bb_lower, "bb_mid": sig.bb_mid, "bb_upper": sig.bb_upper,
                "bb_width": sig.bb_width, "rr": sig.rr,
                "strategy_type": sig.strategy_type, "sub_type": sig.sub_type,
                "regime": sig.regime, "htf_adx": sig.htf_adx,
            }
            print(f"  {len(d)} clés :")
            for k, v in d.items():
                print(f"    {k:20s} = {v!r}")
        except Exception as e:
            print(f"  ERREUR : {e}")
            import traceback; traceback.print_exc()
    else:
        print("  (pas de signal disponible)")

    # ── 6. Résumé ─────────────────────────────────────────────────────
    print(f"\n{sep}")
    print("6. RÉSUMÉ")
    print(sep)
    last_idx = len(df_prep) - 1
    last_bar_sigs = [(i, s) for i, s in all_signals if i == last_idx]
    print(f"  Instrument              : {instrument}")
    print(f"  Barres chargées         : {len(df_prep)}")
    print(f"  Signaux totaux          : {len(all_signals)}")
    print(f"  Signaux dernière bougie : {len(last_bar_sigs)}")
    print()


if __name__ == "__main__":
    main()
