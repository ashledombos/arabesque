# data_orchestrator.py
import argparse
import re
import time
import os
from datetime import datetime

import pandas as pd

from arabesque.data.backends import (
    dukascopy_update_min1,
    ccxt_update_min1,
    ccxt_key,
    derive_timeframes,
    DEFAULT_PRICE_SCALE,
)


def parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise SystemExit(f"Date invalide : {s} (format attendu : YYYY-MM-DD)")


def load_instruments_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path).fillna("")
    cols = set(df.columns)

    if "ftmo_symbol" in cols and "data_symbol" in cols and "source" in cols:
        # nouveau format
        if "exchange" not in df.columns:
            df["exchange"] = ""
        if "price_scale" not in df.columns:
            df["price_scale"] = ""
        return df[["ftmo_symbol", "source", "data_symbol", "exchange", "price_scale"]]

    if "symbol" in cols and "source" in cols:
        # ancien format : symbol == ftmo_symbol == data_symbol
        df["ftmo_symbol"] = df["symbol"]
        df["data_symbol"] = df["symbol"]
        if "exchange" not in df.columns:
            df["exchange"] = ""
        if "price_scale" not in df.columns:
            df["price_scale"] = ""
        return df[["ftmo_symbol", "source", "data_symbol", "exchange", "price_scale"]]

    raise SystemExit(
        "CSV instruments invalide : colonnes attendues :\n"
        "- nouveau format : ftmo_symbol, source, data_symbol, exchange, price_scale\n"
        "- ancien format : symbol, source, exchange?, price_scale?"
    )


def purge_key(root: str, provider: str, key: str) -> int:
    """Supprime min1 + derived pour une clé. Retourne le nombre de fichiers supprimés."""
    deleted = 0
    min1_path = os.path.join(root, provider, "min1", f"{key}.parquet")

    der_dir = os.path.join(root, provider, "derived")
    # fichiers dérivés typiques : KEY_5m.parquet, KEY_1h.parquet, etc.
    # on supprime tout ce qui commence par KEY_
    der_prefix = f"{key}_"

    paths = []
    paths.append(min1_path)

    if os.path.isdir(der_dir):
        for name in os.listdir(der_dir):
            if name.startswith(der_prefix) and name.endswith(".parquet"):
                paths.append(os.path.join(der_dir, name))

    for p in paths:
        if os.path.exists(p):
            os.remove(p)
            deleted += 1

    return deleted


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", default="instruments.csv")
    ap.add_argument("--root", default="data")

    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)

    ap.add_argument("--derive", nargs="*", default=["5m", "1h"])
    ap.add_argument("--derive-only", action="store_true", help="Recalcul des dérivés uniquement, sans téléchargement")
    ap.add_argument("--plan", action="store_true", help="Affichage simple (sans téléchargement)")

    ap.add_argument("--sleep-ms", type=int, default=0, help="Pause entre requêtes (ms)")
    ap.add_argument("--sleep-between", type=int, default=0, help="Pause entre instruments (secondes)")

    ap.add_argument("--timeout-s", type=int, default=30)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--max-fail-streak", type=int, default=10)

    ap.add_argument("--only", choices=["dukascopy", "ccxt"], help="Ne traiter qu’une source")
    ap.add_argument("--filter", help="Regex appliquée sur ftmo_symbol (ex : '^(EUR|USD|GBP)')")

    # purge
    ap.add_argument("--purge", action="store_true", help="Purger les caches (min1 + derived) avant traitement")
    ap.add_argument("--purge-provider", choices=["dukascopy", "ccxt", "all"], default=None,
                    help="Limiter la purge à une source (sinon : selon --only ou selon les lignes filtrées)")
    ap.add_argument("--purge-only", action="store_true", help="Purger puis sortir, sans télécharger")

    args = ap.parse_args()

    start_dt = parse_date(args.start)
    end_dt = parse_date(args.end)

    df = load_instruments_csv(args.instruments)
    rx = re.compile(args.filter) if args.filter else None

    # ───────────────── purge (optionnelle) ─────────────────
    if args.purge:
        purged_total = 0

        for _, row in df.iterrows():
            ftmo_symbol = str(row["ftmo_symbol"]).strip()
            source = str(row["source"]).strip().lower()
            data_symbol = str(row["data_symbol"]).strip()
            exchange = str(row.get("exchange", "")).strip() or "binance"

            if not ftmo_symbol or not source or not data_symbol:
                continue
            if rx and not rx.search(ftmo_symbol):
                continue

            # filtrage provider de purge
            if args.purge_provider and args.purge_provider != "all" and source != args.purge_provider:
                continue

            # si pas de purge_provider, respecter --only si présent
            if (not args.purge_provider) and args.only and source != args.only:
                continue

            if source == "dukascopy":
                key = data_symbol.upper()
            elif source == "ccxt":
                key = ccxt_key(data_symbol, exchange)
            else:
                continue

            n = purge_key(args.root, source, key)
            if n:
                print(f"[PURGE] {source} ; {ftmo_symbol} ; key={key} ; {n} fichier(s)")
                purged_total += n

        print(f"[PURGE] total : {purged_total} fichier(s)")
        if args.purge_only:
            print("Terminé.")
            return

    # ───────────────── traitement normal ─────────────────
    for _, row in df.iterrows():
        ftmo_symbol = str(row["ftmo_symbol"]).strip()
        source = str(row["source"]).strip().lower()
        data_symbol = str(row["data_symbol"]).strip()
        exchange = str(row.get("exchange", "")).strip() or "binance"
        price_scale_s = str(row.get("price_scale", "")).strip()

        if not ftmo_symbol or not source or not data_symbol:
            continue
        if rx and not rx.search(ftmo_symbol):
            continue
        if args.only and source != args.only:
            continue

        if source == "dukascopy":
            key = data_symbol.upper()
        elif source == "ccxt":
            key = ccxt_key(data_symbol, exchange)
        else:
            print(f"[IGN] {ftmo_symbol} : source inconnue : {source}")
            continue

        if args.plan:
            print(f"[PLAN] {source} ; {ftmo_symbol} → {data_symbol} ; key={key}")
            continue

        if args.derive_only:
            d = derive_timeframes(args.root, source, key, args.derive)
            print(f"[DERIVE] {source} ; {ftmo_symbol} ; {d}")
            if args.sleep_between:
                time.sleep(args.sleep_between)
            continue

        if source == "dukascopy":
            ps = float(price_scale_s) if price_scale_s else DEFAULT_PRICE_SCALE
            print(f"[Dukascopy] {ftmo_symbol} → {data_symbol}")
            r = dukascopy_update_min1(
                root=args.root,
                data_symbol=data_symbol,
                key=key,
                start_dt=start_dt,
                end_dt=end_dt,
                price_scale=ps,
                sleep_ms=args.sleep_ms,
                timeout_s=args.timeout_s,
                retries=args.retries,
                max_fail_streak=args.max_fail_streak,
            )
            print(r)

        elif source == "ccxt":
            print(f"[CCXT] {ftmo_symbol} → {data_symbol} ({exchange})")
            r = ccxt_update_min1(
                root=args.root,
                data_symbol=data_symbol,
                exchange_name=exchange,
                key=key,
                start_dt=start_dt,
                end_dt=end_dt,
                sleep_ms=args.sleep_ms,
            )
            print(r)

        if args.derive:
            d = derive_timeframes(args.root, source, key, args.derive)
            print(f"[DERIVE] {source} ; {ftmo_symbol} ; {d}")

        if args.sleep_between:
            time.sleep(args.sleep_between)

    print("Terminé.")


if __name__ == "__main__":
    main()

