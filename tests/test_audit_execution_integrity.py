"""Tests des règles de classification de l'audit intégrité d'exécution.

Les règles doivent être :
- mutuellement exclusives pour le chiffrage principal (`primary`)
- multi-tag pour le diagnostic (`tags`)
- conservatrices sur les données pré-strict (be_source absent)
- robustes aux champs manquants
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.audit_execution_integrity import (
    CRITICAL_SIGNATURES,
    FORWARD_CUTOFF_UTC,
    MFE_THRESHOLD,
    SIGNATURES_PRIORITY,
    aggregate,
    build_notification_body,
    classify_exit,
    classify_non_exit_anomaly,
    compute_verdict,
    is_urgent_verdict,
)


def _exit(**overrides) -> dict:
    """Fabrique un événement exit minimal."""
    base = {
        "event": "exit",
        "ts": "2026-05-15T12:00:00+00:00",
        "trade_id": "tid-1",
        "instrument": "XAUUSD",
        "strategy": "extension",
        "side": "LONG",
        "entry_price": 100.0,
        "exit_price": 99.0,
        "sl": 99.0,
        "result_r": -1.0,
        "pnl_cash": -10.0,
        "mfe_r": 0.0,
        "be_set": False,
        "trailing_tier": 0,
        "exit_reason": "stop_loss",
        "broker_id": "ftmo_challenge",
        "position_id": "pos-1",
        "protection_level": "normal",
    }
    base.update(overrides)
    return base


def _enrich_ts(ev: dict) -> dict:
    """Ajoute le champ `_ts_dt` que `aggregate()` attend."""
    ev = dict(ev)
    ev["_ts_dt"] = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
    return ev


# ─────────────────────────────────────────────────────────────────────────────
# classify_exit — cas par signature
# ─────────────────────────────────────────────────────────────────────────────

class TestReconciledStopHighMfe:
    def test_dashusd_canonical_case(self):
        """DASHUSD 22/05 : reconciled_stop + MFE 1.86R + be inferred + feed dead."""
        ev = _exit(
            mfe_r=1.86,
            exit_reason="reconciled_stop_loss",
            exit_price_source="reconciled",
            be_set=True,
            be_source="inferred_from_mfe",
            spread_at_exit=0.0,
            broker_bid_at_exit=0.0,
            broker_ask_at_exit=0.0,
        )
        primary, tags = classify_exit(ev)
        assert primary == "RECONCILED_STOP_HIGH_MFE"
        assert "RECONCILED_STOP_HIGH_MFE" in tags
        assert "RECONCILED_HIGH_MFE" in tags
        assert "BE_MISSED" in tags
        assert "EXIT_NO_BROKER_QUOTE" in tags

    def test_reconciled_stop_low_mfe_is_not_flagged(self):
        ev = _exit(mfe_r=0.10, exit_reason="reconciled_stop_loss")
        primary, tags = classify_exit(ev)
        assert primary == "CLEAN"
        assert "RECONCILED_STOP_HIGH_MFE" not in tags

    def test_mfe_exactly_threshold_counts(self):
        ev = _exit(mfe_r=MFE_THRESHOLD, exit_reason="reconciled_stop_loss")
        primary, _ = classify_exit(ev)
        assert primary == "RECONCILED_STOP_HIGH_MFE"


class TestReconciledHighMfe:
    def test_reconciled_other_high_mfe(self):
        ev = _exit(
            mfe_r=0.5,
            exit_reason="reconciled_other",
            exit_price_source="reconciled",
        )
        primary, tags = classify_exit(ev)
        # `reconciled_other` ≠ stop, donc pas RECONCILED_STOP_HIGH_MFE
        assert primary == "RECONCILED_HIGH_MFE"
        assert "RECONCILED_STOP_HIGH_MFE" not in tags

    def test_exit_price_source_reconciled_with_normal_reason(self):
        """exit_reason normal mais exit_price_source=reconciled : compte aussi."""
        ev = _exit(
            mfe_r=0.4,
            exit_reason="stop_loss",
            exit_price_source="reconciled",
        )
        primary, _ = classify_exit(ev)
        assert primary == "RECONCILED_HIGH_MFE"


class TestBeMissed:
    def test_be_missed_with_be_source_not_armed(self):
        ev = _exit(
            mfe_r=0.4,
            exit_reason="stop_loss",
            be_set=False,
            be_source="not_armed",
        )
        primary, _ = classify_exit(ev)
        assert primary == "BE_MISSED"

    def test_be_missed_with_be_source_inferred(self):
        """be_set=True mais be_source=inferred → BE jamais armé broker."""
        ev = _exit(
            mfe_r=0.5,
            exit_reason="stop_loss",
            be_set=True,
            be_source="inferred_from_mfe",
        )
        primary, _ = classify_exit(ev)
        assert primary == "BE_MISSED"

    def test_be_missed_conservative_when_source_absent(self):
        """be_source absent (pré-strict) ET high MFE → flag conservatif."""
        ev = _exit(mfe_r=0.4, exit_reason="stop_loss", be_set=True)
        ev.pop("be_source", None)
        primary, _ = classify_exit(ev)
        assert primary == "BE_MISSED"

    def test_be_broker_armed_is_clean(self):
        """be_source=broker_armed = preuve d'armement → CLEAN."""
        ev = _exit(
            mfe_r=0.6,
            exit_reason="stop_loss",
            be_set=True,
            be_source="broker_armed",
            exit_price=100.05,  # exit au-dessus de l'entry, cohérent BE
            result_r=0.05,
        )
        primary, tags = classify_exit(ev)
        assert primary == "CLEAN"
        assert "BE_MISSED" not in tags

    def test_low_mfe_not_be_missed(self):
        ev = _exit(mfe_r=0.1, be_set=False, be_source="not_armed")
        primary, _ = classify_exit(ev)
        assert primary == "CLEAN"


class TestExitNoBrokerQuote:
    def test_spread_zero(self):
        ev = _exit(spread_at_exit=0.0)
        primary, tags = classify_exit(ev)
        assert primary == "EXIT_NO_BROKER_QUOTE"
        assert "EXIT_NO_BROKER_QUOTE" in tags

    def test_bid_ask_both_zero(self):
        ev = _exit(broker_bid_at_exit=0.0, broker_ask_at_exit=0.0)
        primary, _ = classify_exit(ev)
        assert primary == "EXIT_NO_BROKER_QUOTE"

    def test_normal_quote_is_clean(self):
        ev = _exit(
            spread_at_exit=0.5,
            broker_bid_at_exit=99.8,
            broker_ask_at_exit=100.2,
            mfe_r=0.1,  # évite MFE_ZERO_LOSER
            result_r=-0.5,
        )
        primary, _ = classify_exit(ev)
        assert primary == "CLEAN"

    def test_missing_quote_fields_are_not_flagged(self):
        """Pas de spread renseigné ≠ feed mort (vieille donnée)."""
        ev = _exit()
        primary, tags = classify_exit(ev)
        assert "EXIT_NO_BROKER_QUOTE" not in tags


class TestMfeZeroLoser:
    def test_mfe_zero_loser_is_informational(self):
        ev = _exit(mfe_r=0.0, result_r=-1.0)
        primary, tags = classify_exit(ev)
        # MFE_ZERO_LOSER est plus bas en priorité que CLEAN dans la mesure où
        # CLEAN est le défaut absolu — mais MFE_ZERO_LOSER étant présent dans
        # SIGNATURES_PRIORITY avant CLEAN, il devient primary.
        assert primary == "MFE_ZERO_LOSER"
        assert "MFE_ZERO_LOSER" in tags

    def test_mfe_zero_winner_is_clean(self):
        ev = _exit(mfe_r=0.0, result_r=0.5)
        primary, _ = classify_exit(ev)
        assert primary == "CLEAN"

    def test_mfe_zero_loser_is_not_critical(self):
        """Doit rester informationnel, pas inclus dans CRITICAL_SIGNATURES."""
        assert "MFE_ZERO_LOSER" not in CRITICAL_SIGNATURES


# ─────────────────────────────────────────────────────────────────────────────
# Priorité — mutuelle exclusion
# ─────────────────────────────────────────────────────────────────────────────

class TestPriorityMutualExclusion:
    def test_signatures_priority_contains_all(self):
        assert "CLEAN" in SIGNATURES_PRIORITY
        for sig in CRITICAL_SIGNATURES:
            assert sig in SIGNATURES_PRIORITY

    def test_reconciled_stop_beats_be_missed(self):
        ev = _exit(
            mfe_r=1.0,
            exit_reason="reconciled_stop_loss",
            be_set=False,
            be_source="not_armed",
        )
        primary, tags = classify_exit(ev)
        assert primary == "RECONCILED_STOP_HIGH_MFE"
        assert "BE_MISSED" in tags  # tag présent, mais primary = la pire

    def test_reconciled_high_mfe_beats_be_missed(self):
        ev = _exit(
            mfe_r=0.6,
            exit_reason="reconciled_other",
            exit_price_source="reconciled",
            be_set=False,
            be_source="not_armed",
        )
        primary, tags = classify_exit(ev)
        assert primary == "RECONCILED_HIGH_MFE"
        assert "BE_MISSED" in tags

    def test_be_missed_beats_no_broker_quote(self):
        ev = _exit(
            mfe_r=0.5,
            be_set=False,
            be_source="not_armed",
            spread_at_exit=0.0,
        )
        primary, tags = classify_exit(ev)
        assert primary == "BE_MISSED"
        assert "EXIT_NO_BROKER_QUOTE" in tags

    def test_no_quote_alone_beats_clean(self):
        ev = _exit(spread_at_exit=0.0)
        primary, _ = classify_exit(ev)
        assert primary == "EXIT_NO_BROKER_QUOTE"


# ─────────────────────────────────────────────────────────────────────────────
# Anomalies hors-exit
# ─────────────────────────────────────────────────────────────────────────────

class TestNonExitAnomalies:
    def test_protection_check_failed(self):
        ev = {"event": "protection_check", "confirmed": False}
        assert classify_non_exit_anomaly(ev) == "PROTECTION_CHECK_FAILED"

    def test_protection_check_confirmed_is_not_flagged(self):
        ev = {"event": "protection_check", "confirmed": True}
        assert classify_non_exit_anomaly(ev) is None

    def test_risk_integrity_over_critical(self):
        ev = {"event": "risk_integrity_check", "status": "over_risk_critical"}
        assert classify_non_exit_anomaly(ev) == "RISK_INTEGRITY_OVER_RISK_CRITICAL"

    def test_risk_integrity_under(self):
        ev = {"event": "risk_integrity_check", "status": "under_risk"}
        assert classify_non_exit_anomaly(ev) == "RISK_INTEGRITY_UNDER_RISK"

    def test_risk_integrity_unknown_status_ignored(self):
        ev = {"event": "risk_integrity_check", "status": "ok"}
        assert classify_non_exit_anomaly(ev) is None

    def test_exit_invalidated(self):
        assert classify_non_exit_anomaly({"event": "exit_invalidated_by_bug"}) \
            == "EXIT_INVALIDATED_BY_BUG"

    def test_emergency_close(self):
        assert classify_non_exit_anomaly({"event": "emergency_close_all"}) \
            == "EMERGENCY_CLOSE_ALL"

    def test_orphan_cleanup(self):
        assert classify_non_exit_anomaly({"event": "orphan_cleanup"}) \
            == "ORPHAN_CLEANUP"

    def test_unrelated_event_ignored(self):
        assert classify_non_exit_anomaly({"event": "entry"}) is None
        assert classify_non_exit_anomaly({"event": "be_polling_pass"}) is None


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation + verdict
# ─────────────────────────────────────────────────────────────────────────────

class TestAggregation:
    def test_aggregate_counts_exits_and_anomalies(self):
        events = [
            # Trade sans flag : MFE>0 et winner → CLEAN
            _exit(trade_id="t1", ts="2026-05-10T10:00:00+00:00",
                  mfe_r=0.1, result_r=0.05),
            _exit(trade_id="t2", ts="2026-05-10T11:00:00+00:00",
                  mfe_r=0.5, exit_reason="reconciled_stop_loss"),
            _exit(trade_id="t3", ts="2026-05-11T10:00:00+00:00",
                  mfe_r=0.4, be_set=False, be_source="not_armed"),
            {"event": "protection_check", "ts": "2026-05-10T12:00:00+00:00",
             "confirmed": False, "broker_id": "ftmo_challenge", "instrument": "X"},
        ]
        events = [_enrich_ts(e) for e in events]
        agg = aggregate(events)

        assert agg["overall"].n_exits == 3
        assert agg["overall"].primary_counts["RECONCILED_STOP_HIGH_MFE"] == 1
        assert agg["overall"].primary_counts["BE_MISSED"] == 1
        assert agg["overall"].primary_counts["CLEAN"] == 1
        assert agg["overall"].non_exit_anomalies["PROTECTION_CHECK_FAILED"] == 1

    def test_aggregate_strategy_alias_trend_to_extension(self):
        ev = _enrich_ts(_exit(strategy="trend"))
        agg = aggregate([ev])
        # On vérifie via by_dim que la clé est "extension"
        keys = list(agg["by_dim"].keys())
        assert any(k[2] == "extension" for k in keys)

    def test_be_missed_loss_vs_nonloss_decomposition(self):
        """Cas XAUUSD 26/05 +1.18R : BE_MISSED rattrapé par operator → non-loss.
        La signature doit rester comptée (signal de bug), mais le coût net (R)
        ne doit inclure que les pertes."""
        be_missed_loss = _enrich_ts(_exit(
            trade_id="loss-1",
            mfe_r=0.5,
            be_set=False,
            be_source="not_armed",
            result_r=-1.0,
        ))
        be_missed_nonloss = _enrich_ts(_exit(
            trade_id="nonloss-1",
            mfe_r=1.79,
            be_set=False,
            be_source="not_armed",
            result_r=1.18,  # rattrapé
        ))
        agg = aggregate([be_missed_loss, be_missed_nonloss])
        ov = agg["overall"]

        # Signature comptée pour les deux
        assert ov.primary_counts["BE_MISSED"] == 2
        # Mais le coût net = seulement la perte
        assert ov.primary_loss_counts["BE_MISSED"] == 1
        assert ov.primary_loss_sum_r["BE_MISSED"] == pytest.approx(-1.0)
        assert ov.primary_nonloss_counts["BE_MISSED"] == 1
        assert "nonloss-1" in ov.primary_nonloss_trade_ids["BE_MISSED"]
        assert "loss-1" not in ov.primary_nonloss_trade_ids["BE_MISSED"]

    def test_verdict_flags_nonloss_critical_as_open_post_cutoff(self):
        """Un BE_MISSED non-loss post-cutoff reste un signal de bug → 'open'.
        On ne masque pas la signature parce que le marché a été clément."""
        nonloss_post = _enrich_ts(_exit(
            trade_id="post-nonloss",
            ts="2026-05-30T10:00:00+00:00",
            mfe_r=1.5,
            be_set=False,
            be_source="not_armed",
            result_r=0.5,
        ))
        agg = aggregate([nonloss_post])
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        v = compute_verdict(agg, FORWARD_CUTOFF_UTC, now)
        assert v["per_signature"]["BE_MISSED"]["status"] == "open"
        assert v["overall_status"] == "RED"

    def test_multi_tag_listed_separately(self):
        ev = _enrich_ts(_exit(
            mfe_r=1.0,
            exit_reason="reconciled_stop_loss",
            be_set=False,
            spread_at_exit=0.0,
        ))
        agg = aggregate([ev])
        assert len(agg["overall"].multi_tag_examples) == 1
        ex = agg["overall"].multi_tag_examples[0]
        assert ex["primary"] == "RECONCILED_STOP_HIGH_MFE"
        assert len(ex["tags"]) >= 3


class TestVerdict:
    def _agg_with_critical_event(self, ts_iso: str, sig_setup: dict) -> dict:
        ev = _enrich_ts(_exit(ts=ts_iso, **sig_setup))
        return aggregate([ev])

    def test_verdict_open_when_post_cutoff_occurrence(self):
        # Post-cutoff = après 2026-05-29 20:14 UTC
        agg = self._agg_with_critical_event(
            "2026-05-30T10:00:00+00:00",
            {"mfe_r": 1.0, "exit_reason": "reconciled_stop_loss"},
        )
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)  # 11j après cutoff
        v = compute_verdict(agg, FORWARD_CUTOFF_UTC, now)
        assert v["overall_status"] == "RED"
        assert v["per_signature"]["RECONCILED_STOP_HIGH_MFE"]["status"] == "open"

    def test_verdict_monitoring_before_required_days(self):
        # Cas pré-cutoff seulement, et on est < 7j après cutoff
        agg = self._agg_with_critical_event(
            "2026-05-22T10:00:00+00:00",
            {"mfe_r": 1.0, "exit_reason": "reconciled_stop_loss"},
        )
        now = datetime(2026, 5, 31, tzinfo=timezone.utc)  # ~1.5j après cutoff
        v = compute_verdict(agg, FORWARD_CUTOFF_UTC, now)
        assert v["overall_status"] == "MONITORING"
        assert v["per_signature"]["RECONCILED_STOP_HIGH_MFE"]["status"] == "monitoring"

    def test_verdict_fixed_forward_after_required_days_no_post(self):
        # Pré-cutoff occurrence + ≥7j forward + zéro post
        agg = self._agg_with_critical_event(
            "2026-05-22T10:00:00+00:00",
            {"mfe_r": 1.0, "exit_reason": "reconciled_stop_loss"},
        )
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        v = compute_verdict(agg, FORWARD_CUTOFF_UTC, now)
        assert v["overall_status"] == "GREEN"
        assert v["per_signature"]["RECONCILED_STOP_HIGH_MFE"]["status"] == "fixed_forward"

    def test_verdict_no_occurrence_when_nothing_ever(self):
        # Aucun trade critique nulle part
        agg = aggregate([_enrich_ts(_exit(mfe_r=0.1))])
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        v = compute_verdict(agg, FORWARD_CUTOFF_UTC, now)
        assert v["overall_status"] == "GREEN"
        for info in v["per_signature"].values():
            assert info["status"] == "no_occurrence"

    def test_verdict_post_trade_ids_listed(self):
        agg = self._agg_with_critical_event(
            "2026-05-30T10:00:00+00:00",
            {"trade_id": "post-tid", "mfe_r": 1.0,
             "exit_reason": "reconciled_stop_loss"},
        )
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        v = compute_verdict(agg, FORWARD_CUTOFF_UTC, now)
        assert v["per_signature"]["RECONCILED_STOP_HIGH_MFE"]["post_trade_ids"] == ["post-tid"]


class TestNotificationSummary:
    def test_red_verdict_is_urgent(self):
        agg = aggregate([_enrich_ts(_exit(
            ts="2026-05-30T10:00:00+00:00",
            mfe_r=1.0,
            exit_reason="reconciled_stop_loss",
        ))])
        verdict = compute_verdict(
            agg, FORWARD_CUTOFF_UTC, datetime(2026, 6, 10, tzinfo=timezone.utc)
        )
        assert verdict["overall_status"] == "RED"
        assert is_urgent_verdict(verdict) is True

    def test_monitoring_verdict_is_not_urgent(self):
        agg = aggregate([_enrich_ts(_exit(
            ts="2026-05-22T10:00:00+00:00",
            mfe_r=1.0,
            exit_reason="reconciled_stop_loss",
        ))])
        verdict = compute_verdict(
            agg, FORWARD_CUTOFF_UTC, datetime(2026, 5, 31, tzinfo=timezone.utc)
        )
        assert verdict["overall_status"] == "MONITORING"
        assert is_urgent_verdict(verdict) is False

    def test_notification_body_is_short_and_points_to_report(self):
        since = datetime(2026, 5, 23, tzinfo=timezone.utc)
        until = datetime(2026, 5, 30, tzinfo=timezone.utc)
        agg = aggregate([_enrich_ts(_exit(
            ts="2026-05-30T10:00:00+00:00",
            trade_id="post-tid",
            mfe_r=1.0,
            exit_reason="reconciled_stop_loss",
        ))])
        verdict = compute_verdict(
            agg, FORWARD_CUTOFF_UTC, datetime(2026, 6, 10, tzinfo=timezone.utc)
        )
        body = build_notification_body(
            agg, verdict, since, until, "logs/execution_integrity_latest.md"
        )
        assert "Statut: 🔴 RED" in body
        assert "Signatures critiques:" in body
        assert "post-tid" in body
        assert "logs/execution_integrity_latest.md" in body
