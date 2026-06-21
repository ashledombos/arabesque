"""Rappel one-shot : revue de l'observation cap=7 (max_open_positions 5→7).

Déployé le 2026-06-16 (DECISIONS « Application 2026-06-16 », HANDOFF). Critère de
revue : 2026-06-30 OU n≥30 trades extension+glissade propres. Ce script envoie un
rappel Telegram+ntfy à la date de revue, indépendamment de toute session Claude
(déclenché par le timer systemd `arabesque-cap7-review.timer`, persistant au reboot).

Ne décide rien : il rappelle juste de LANCER la revue (relire le verdict cap=7 et
trancher 7→10 / maintien / rollback→5 selon les seuils documentés).
"""
import asyncio
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "config" / "secrets.yaml"

BODY = """🔔 Revue cap=7 due (max_open_positions 5→7, déployé 2026-06-16)

À faire quand tu reprends la main :
1. Relancer un /bilan (régénère selection_coverage : couverture % + écart pris/ratés à n_pris≥40 ?).
2. Vérifier les seuils de ROLLBACK→5 : FTMO total DD < -8% · worst-day live < -10R · open_risk observé > 1.5% · invariants alert/critique · ≥3 SL crypto même jour.
3. Décider :
   • Tout propre + DD remonté (~-5%) → envisager cap=10 (sinon rester différé).
   • Un seuil franchi → rollback cap 7→5 (compte flat + commit config séparé + restart contrôlé).
   • Sinon → maintenir cap=7, continuer l'observation.
Réf : docs/DECISIONS.md (Application 2026-06-16) + HANDOFF.md + scripts/selection_coverage.py."""


def main() -> int:
    secrets = yaml.safe_load(SECRETS.read_text()) if SECRETS.exists() else {}
    channels = secrets.get("notifications", {}).get("channels", [])
    if not channels:
        print("Aucun canal de notification configuré.")
        return 1
    import apprise
    ap = apprise.Apprise()
    for ch in channels:
        if isinstance(ch, str):
            ap.add(ch)
    ok = asyncio.run(ap.async_notify(
        body=BODY,
        title="Arabesque — revue cap=7",
        body_format=apprise.NotifyFormat.TEXT,
    ))
    print(f"notif ok: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
