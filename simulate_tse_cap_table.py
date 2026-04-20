#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

import numpy as np
from openpyxl import load_workbook


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Instrument:
    layer: str
    rank: int
    priority_label: str
    name: str
    principal: float
    maturity: str
    interest_type: str
    is_pik: bool = False
    pik_rate: float = 0.0  # annual decimal, e.g. 0.139 for SOFR 5.3% + 8.5% spread


@dataclass(frozen=True)
class CapTableModel:
    workbook_name: str
    instruments: list[Instrument]
    base_ebitda: float
    multiple_low: float
    multiple_mid: float
    multiple_high: float
    listed_debt: float
    other_claims: float
    total_debt_to_clear: float


@dataclass(frozen=True)
class SimulationAssumptions:
    runs: int
    seed: int
    ebitda_cv: float
    debt_volatility: float
    # New: distressed / clean run-rate / recovery EBITDA overrides
    ebitda_distressed: float   # annualised Q3/Q4 exit rate (market-implied)
    ebitda_clean: float        # reported adj. EBITDA minus one-time items
    ebitda_recovery: float     # normalised demand scenario
    # Market price of 1L debt (cents on the dollar)
    market_price_1l: float
    # Discount rate for implied fair-value calculation (decimal)
    discount_rate: float
    # Estimated months to restructuring resolution
    resolution_months: int
    # Estimated Chapter 11 professional fees ($M)
    ch11_costs: float


LAYER_ORDER = {
    "Super": 1,
    "1L": 2,
    "2L": 3,
    "Junior": 4,
    "Equity": 5,
}

# TLB PIK details — hardcoded for TSE; override via CLI if needed
TLB_NAME_FRAGMENT = "term loan b"
SOFR = 0.053          # current SOFR approximation
TLB_SPREAD = 0.085    # 8.5% credit spread
TLB_PIK_RATE = SOFR + TLB_SPREAD   # ~13.8% p.a.


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monte Carlo recovery analysis on the TSE cap table workbook."
    )
    parser.add_argument("--input", default="TSE Cap Table .xlsx")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--runs", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--ebitda-cv", type=float, default=0.22)
    parser.add_argument("--debt-volatility", type=float, default=0.03)
    # EBITDA case overrides
    parser.add_argument("--ebitda-distressed", type=float, default=72.5,
                        help="Distressed EBITDA ($M): annualised Q3/Q4 exit run-rate")
    parser.add_argument("--ebitda-clean", type=float, default=135.5,
                        help="Clean run-rate EBITDA ($M): reported adj. minus one-time items")
    parser.add_argument("--ebitda-recovery", type=float, default=225.0,
                        help="Recovery EBITDA ($M): normalised demand / management target")
    # Market / discount inputs
    parser.add_argument("--market-price-1l", type=float, default=11.0,
                        help="Market trading price of 1L debt (cents on the dollar)")
    parser.add_argument("--discount-rate", type=float, default=20.0,
                        help="Distressed discount rate %% for implied price calc")
    parser.add_argument("--resolution-months", type=int, default=12,
                        help="Estimated months to restructuring resolution")
    parser.add_argument("--ch11-costs", type=float, default=75.0,
                        help="Estimated Chapter 11 professional fees ($M)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Workbook helpers
# ---------------------------------------------------------------------------

def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return str(value).strip()


def to_float(value: Any) -> float:
    if value in (None, "", "#DIV/0!"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def canonicalize_layer(priority_label: str) -> tuple[str, int]:
    normalized = priority_label.lower()
    if "super" in normalized:
        return "Super", LAYER_ORDER["Super"]
    if "1st" in normalized or "senior secured" in normalized:
        return "1L", LAYER_ORDER["1L"]
    if "2nd" in normalized or "2nd lien" in normalized:
        return "2L", LAYER_ORDER["2L"]
    if "junior" in normalized:
        return "Junior", LAYER_ORDER["Junior"]
    if "equity" in normalized:
        return "Equity", LAYER_ORDER["Equity"]
    raise ValueError(f"Unsupported priority label: {priority_label!r}")


def maybe_layer(priority_label: str) -> tuple[str, int] | None:
    try:
        return canonicalize_layer(priority_label)
    except ValueError:
        return None


def load_cap_table(workbook_path: Path) -> CapTableModel:
    wb = load_workbook(workbook_path, data_only=True)
    ws = wb[wb.sheetnames[0]]

    instruments: list[Instrument] = []
    base_ebitda = 0.0
    multiple_low = 0.0
    multiple_mid = 0.0
    multiple_high = 0.0
    total_debt_to_clear = 0.0
    other_claims = 0.0
    in_stack_section = True

    for row in ws.iter_rows(values_only=True):
        priority = normalize_text(row[0])
        raw_instrument_name = row[1]
        instrument_name = normalize_text(raw_instrument_name)
        principal = to_float(row[2])
        maturity = normalize_text(row[3])
        interest_type = normalize_text(row[4])

        parsed_layer = maybe_layer(priority)
        if instrument_name == "Total Debt to Clear" or priority == "Adjusted EBITDA":
            in_stack_section = False

        if (
            in_stack_section
            and instrument_name
            and isinstance(raw_instrument_name, str)
            and "total" not in instrument_name.lower()
            and parsed_layer
        ):
            layer, rank = parsed_layer
            layer = str(layer)
            is_pik = "pik" in interest_type.lower() or "pik" in instrument_name.lower()
            pik_rate = TLB_PIK_RATE if (
                is_pik or TLB_NAME_FRAGMENT in instrument_name.lower()
            ) else 0.0
            instruments.append(
                Instrument(
                    layer=layer,
                    rank=rank,
                    priority_label=priority,
                    name=instrument_name,
                    principal=principal,
                    maturity=maturity or "n/a",
                    interest_type=interest_type or "n/a",
                    is_pik=is_pik,
                    pik_rate=pik_rate,
                )
            )

        if priority == "Adjusted EBITDA":
            base_ebitda = to_float(row[1])
        elif priority == "Low (distressed case)":
            multiple_low = to_float(row[1])
        elif priority == "Mid":
            multiple_mid = to_float(row[1])
        elif priority == "High (healthy case)":
            multiple_high = to_float(row[1])
        elif instrument_name == "Total Debt to Clear":
            total_debt_to_clear = to_float(row[2])
        elif priority == "Other":
            other_claims = to_float(row[2])

    if not instruments:
        raise ValueError("No instruments were found in the workbook.")

    listed_debt = sum(item.principal for item in instruments)

    return CapTableModel(
        workbook_name=workbook_path.name,
        instruments=sorted(instruments, key=lambda item: (item.rank, item.name)),
        base_ebitda=base_ebitda,
        multiple_low=multiple_low,
        multiple_mid=multiple_mid,
        multiple_high=multiple_high,
        listed_debt=listed_debt,
        other_claims=other_claims,
        total_debt_to_clear=total_debt_to_clear or (listed_debt + other_claims),
    )


def recovery_pct(i: int, layer: str, model: CapTableModel, assumptions: SimulationAssumptions, layer_totals: dict[str, float], layer_balances: dict[str, list[float]], ch11_costs: float, ordered_layers: list[str], ev_gross: float) -> float:
    # Get the current claim for this layer
    current_claim = layer_totals[layer]
    # Get the balance at exit for this layer
    balance_at_exit = layer_balances[layer][i]
    # Calculate the recovery
    if balance_at_exit <= 0:
        return 0.0
    # Apply waterfall
    distributable_ev = ev_gross - ch11_costs
    if distributable_ev <= 0:
        return 0.0
    # Find the fulcrum
    fulcrum_layer = None
    cumulative_claim = 0.0
    for l in ordered_layers:
        cumulative_claim += layer_totals[l]
        if cumulative_claim > distributable_ev:
            fulcrum_layer = l
            break
    # Calculate recovery
    if layer == fulcrum_layer:
        recovery = distributable_ev / balance_at_exit
    else:
        recovery = min(1.0, distributable_ev / cumulative_claim)
    return min(1.0, max(0.0, recovery))


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def lognormal_samples(
    mean: float,
    coefficient_of_variation: float,
    size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    sigma2 = math.log(1 + coefficient_of_variation**2)
    sigma = math.sqrt(sigma2)
    mu = math.log(mean) - sigma2 / 2
    return rng.lognormal(mean=mu, sigma=sigma, size=size)


def summarize_distribution(values: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(np.mean(values)),
        "p10": float(np.percentile(values, 10)),
        "p25": float(np.percentile(values, 25)),
        "p50": float(np.percentile(values, 50)),
        "p75": float(np.percentile(values, 75)),
        "p90": float(np.percentile(values, 90)),
    }


def build_histogram(values: np.ndarray, bins: int) -> list[dict[str, float]]:
    counts, edges = np.histogram(values, bins=bins)
    return [
        {"start": float(edges[i]), "end": float(edges[i + 1]), "count": int(c)}
        for i, c in enumerate(counts)
    ]


# ---------------------------------------------------------------------------
# PIK accretion
# ---------------------------------------------------------------------------

def pik_accreted_balance(principal: float, rate: float, months: int) -> float:
    """Compound a PIK balance monthly for `months` months."""
    return principal * ((1 + rate / 12) ** months)


def build_pik_accretion_table(
    instruments: list[Instrument],
    resolution_months: int,
) -> list[dict[str, Any]]:
    """Return per-instrument PIK accretion over the resolution timeline."""
    rows = []
    for inst in instruments:
        if inst.pik_rate <= 0:
            continue
        accreted = pik_accreted_balance(inst.principal, inst.pik_rate, resolution_months)
        rows.append(
            {
                "instrument": inst.name,
                "layer": inst.layer,
                "original_balance": inst.principal,
                "accreted_balance": accreted,
                "additional_debt": accreted - inst.principal,
                "pik_rate_pct": inst.pik_rate * 100,
                "months": resolution_months,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Waterfall
# ---------------------------------------------------------------------------

def run_waterfall(
    layer_names: list[str],
    layer_balances: dict[str, np.ndarray],
    enterprise_value: np.ndarray,
    other_claims: np.ndarray,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], np.ndarray]:
    remaining_value = enterprise_value.copy()
    recovered_amounts: dict[str, np.ndarray] = {}
    recovery_rates: dict[str, np.ndarray] = {}

    for layer in layer_names:
        balance = layer_balances[layer]
        recovered = np.minimum(balance, remaining_value)
        recovered_amounts[layer] = recovered
        recovery_rates[layer] = np.divide(
            recovered,
            balance,
            out=np.zeros_like(recovered),
            where=balance > 0,
        )
        remaining_value = np.maximum(remaining_value - recovered, 0)

    equity_value = np.maximum(remaining_value - other_claims, 0)
    return recovered_amounts, recovery_rates, equity_value


def scenario_waterfall(
    ev: float,
    layer_totals: dict[str, float],
    other_claims: float,
    ch11_costs: float = 0.0,
) -> list[dict[str, float | str]]:
    """Deterministic waterfall. ch11_costs reduces EV before distribution."""
    remaining = max(ev - ch11_costs, 0.0)
    rows: list[dict[str, float | str]] = []
    for layer in ("Super", "1L", "2L"):
        debt = layer_totals.get(layer, 0.0)
        recovered = min(remaining, debt)
        recovery_pct = recovered / debt if debt else 0.0
        rows.append(
            {
                "layer": layer,
                "claim": debt,
                "recovered": recovered,
                "recovery_pct": recovery_pct,
            }
        )
        remaining = max(remaining - recovered, 0.0)

    reserve_recovered = min(remaining, other_claims)
    rows.append(
        {
            "layer": "Other claims reserve",
            "claim": other_claims,
            "recovered": reserve_recovered,
            "recovery_pct": reserve_recovered / other_claims if other_claims else 0.0,
        }
    )
    remaining = max(remaining - other_claims, 0.0)
    rows.append(
        {
            "layer": "Equity residual",
            "claim": 0.0,
            "recovered": remaining,
            "recovery_pct": 0.0,
        }
    )
    return rows


# ---------------------------------------------------------------------------
# Sensitivity matrix
# ---------------------------------------------------------------------------

def build_sensitivity_matrix(model: CapTableModel) -> dict[str, Any]:
    ebitda_values = [model.base_ebitda * f for f in (0.75, 0.9, 1.0, 1.1, 1.25)]
    multiple_values = [
        model.multiple_low,
        statistics.mean([model.multiple_low, model.multiple_mid]),
        model.multiple_mid,
        statistics.mean([model.multiple_mid, model.multiple_high]),
        model.multiple_high,
    ]
    layer_totals: dict[str, float] = {}
    for inst in model.instruments:
        layer_totals[inst.layer] = layer_totals.get(inst.layer, 0.0) + inst.principal

    cells = []
    max_equity = 0.0
    for ebitda in ebitda_values:
        row = []
        for multiple in multiple_values:
            ev = ebitda * multiple
            remaining = ev
            for layer in ("Super", "1L", "2L"):
                remaining = max(remaining - layer_totals.get(layer, 0.0), 0.0)
            equity = max(remaining - model.other_claims, 0.0)
            second_lien_recovered = min(
                max(ev - layer_totals.get("Super", 0.0) - layer_totals.get("1L", 0.0), 0.0),
                layer_totals.get("2L", 0.0),
            )
            second_lien_recovery = (
                second_lien_recovered / layer_totals.get("2L", 1.0)
                if layer_totals.get("2L", 0.0)
                else 0.0
            )
            row.append(
                {
                    "enterprise_value": ev,
                    "equity_value": equity,
                    "second_lien_recovery": second_lien_recovery,
                }
            )
            max_equity = max(max_equity, equity)
        cells.append(row)

    return {
        "ebitda_values": ebitda_values,
        "multiple_values": multiple_values,
        "cells": cells,
        "max_equity": max_equity,
    }


# ---------------------------------------------------------------------------
# Fulcrum security identification
# ---------------------------------------------------------------------------

def identify_fulcrum(
    ev: float,
    layer_totals: dict[str, float],
    other_claims: float,
) -> dict[str, Any]:
    """
    Walk the waterfall and find the tranche where EV is exhausted.
    The fulcrum is the first tranche with partial (0 < recovery < 100%) recovery.
    Holders of the fulcrum tranche become new equity owners in reorganisation.
    """
    remaining = ev
    results = []
    fulcrum_layer: str | None = None

    for layer in ("Super", "1L", "2L"):
        claim = layer_totals.get(layer, 0.0)
        if claim == 0:
            continue
        recovered = min(remaining, claim)
        pct = recovered / claim if claim else 0.0
        is_fulcrum = 0.0 < pct < 0.999 and fulcrum_layer is None
        if is_fulcrum:
            fulcrum_layer = layer
        results.append(
            {
                "layer": layer,
                "claim": claim,
                "recovered": recovered,
                "recovery_pct": pct,
                "is_fulcrum": is_fulcrum,
                "status": (
                    "FULCRUM — partial recovery, becomes new equity"
                    if is_fulcrum
                    else ("Money-good" if pct >= 0.999 else "Wiped out")
                ),
            }
        )
        remaining = max(remaining - recovered, 0.0)

    equity = max(remaining - other_claims, 0.0)
    results.append(
        {
            "layer": "Equity",
            "claim": 0.0,
            "recovered": equity,
            "recovery_pct": 0.0,
            "is_fulcrum": False,
            "status": "Wiped out" if equity == 0 else "Has value",
        }
    )

    return {
        "waterfall": results,
        "fulcrum_layer": fulcrum_layer,
        "ev_used": ev,
    }


# ---------------------------------------------------------------------------
# EBITDA scenario analysis
# ---------------------------------------------------------------------------

def build_ebitda_scenarios(
    model: CapTableModel,
    assumptions: SimulationAssumptions,
) -> list[dict[str, Any]]:
    """
    Three EBITDA cases with full waterfall at mid multiple.
    Each case explains what drives the EBITDA estimate.
    """
    layer_totals: dict[str, float] = {}
    for inst in model.instruments:
        layer_totals[inst.layer] = layer_totals.get(inst.layer, 0.0) + inst.principal

    cases = [
        {
            "label": "Distressed",
            "ebitda": assumptions.ebitda_distressed,
            "rationale": (
                "Annualised Q3/Q4 exit run-rate (~$30M/qtr). "
                "Market-implied at 11c. Structural demand weakness, "
                "tariff headwinds, Asian import pressure."
            ),
            "color": "bad",
        },
        {
            "label": "Clean run-rate",
            "ebitda": assumptions.ebitda_clean,
            "rationale": (
                "Reported $162.5M adj. EBITDA minus $27M one-time PC "
                "licensing income. Best estimate of recurring earning power "
                "at current volume levels."
            ),
            "color": "warn",
        },
        {
            "label": "Recovery",
            "ebitda": assumptions.ebitda_recovery,
            "rationale": (
                "Normalised demand (+10% volume = ~$100M EBITDA per mgmt). "
                "Trade certainty, rate cuts, European reshoring. "
                "This is what reorganised equity holders are betting on."
            ),
            "color": "good",
        },
    ]

    results = []
    for case in cases:
        ebitda = case["ebitda"]
        ev = ebitda * model.multiple_mid
        ev_after_costs = max(ev - assumptions.ch11_costs, 0.0)
        waterfall = scenario_waterfall(ev, layer_totals, model.other_claims, assumptions.ch11_costs)
        fulcrum = identify_fulcrum(ev_after_costs, layer_totals, model.other_claims)
        results.append(
            {
                "label": case["label"],
                "ebitda": ebitda,
                "rationale": case["rationale"],
                "color": case["color"],
                "multiple": model.multiple_mid,
                "ev_gross": ev,
                "ch11_costs": assumptions.ch11_costs,
                "ev_net": ev_after_costs,
                "waterfall": waterfall,
                "fulcrum_layer": fulcrum["fulcrum_layer"],
            }
        )
    return results


# ---------------------------------------------------------------------------
# Implied trading price vs market price
# ---------------------------------------------------------------------------

def build_implied_price_analysis(
    ebitda_scenarios: list[dict[str, Any]],
    assumptions: SimulationAssumptions,
    layer_totals: dict[str, float],
) -> dict[str, Any]:
    """
    For each EBITDA scenario, compute:
      - expected 1L recovery (cents)
      - implied fair value = PV of that recovery at discount rate
      - gap vs actual market price of 11c
      - implied return if bought at market
    """
    dr = assumptions.discount_rate / 100.0
    years = assumptions.resolution_months / 12.0
    discount_factor = 1 / ((1 + dr) ** years)

    rows = []
    for sc in ebitda_scenarios:
        wf = sc["waterfall"]
        l1_row = next((r for r in wf if r["layer"] == "1L"), None)
        recovery_pct = l1_row["recovery_pct"] if l1_row else 0.0
        recovery_cents = recovery_pct * 100.0
        implied_price = recovery_cents * discount_factor
        market_price = assumptions.market_price_1l
        gap = implied_price - market_price
        implied_return = (recovery_cents / market_price - 1) * 100.0 if market_price > 0 else 0.0
        rows.append(
            {
                "scenario": sc["label"],
                "ebitda": sc["ebitda"],
                "ev_net": sc["ev_net"],
                "recovery_pct": recovery_pct,
                "recovery_cents": recovery_cents,
                "implied_fair_value": implied_price,
                "market_price": market_price,
                "gap_cents": gap,
                "implied_gross_return_pct": implied_return,
                "discount_rate": assumptions.discount_rate,
                "resolution_months": assumptions.resolution_months,
            }
        )

    # Back-solve: what EBITDA does the market imply?
    # Market EV = Super + (market_price/100) * 1L_principal
    l1_claim = layer_totals.get("1L", 0.0)
    super_claim = layer_totals.get("Super", 0.0)
    market_implied_ev = super_claim + (assumptions.market_price_1l / 100.0) * l1_claim
    mid_mult = ebitda_scenarios[0]["multiple"] if ebitda_scenarios else 6.0
    market_implied_ebitda = market_implied_ev / mid_mult if mid_mult else 0.0

    return {
        "scenarios": rows,
        "market_price": assumptions.market_price_1l,
        "discount_rate": assumptions.discount_rate,
        "resolution_months": assumptions.resolution_months,
        "discount_factor": discount_factor,
        "market_implied_ev": market_implied_ev,
        "market_implied_ebitda": market_implied_ebitda,
        "market_implied_multiple": mid_mult,
    }


# ---------------------------------------------------------------------------
# Chapter 11 cost leakage
# ---------------------------------------------------------------------------

def build_ch11_leakage(
    ebitda_scenarios: list[dict[str, Any]],
    ch11_costs: float,
    layer_totals: dict[str, float],
    other_claims: float,
) -> list[dict[str, Any]]:
    """Show how Ch11 costs reduce 1L recovery across scenarios."""
    rows = []
    for sc in ebitda_scenarios:
        ev_gross = sc["ev_gross"]
        # without costs
        wf_no_cost = scenario_waterfall(ev_gross, layer_totals, other_claims, 0.0)
        l1_no_cost = next((r for r in wf_no_cost if r["layer"] == "1L"), None)
        rec_no_cost = l1_no_cost["recovery_pct"] if l1_no_cost else 0.0
        # with costs
        wf_with_cost = scenario_waterfall(ev_gross, layer_totals, other_claims, ch11_costs)
        l1_with_cost = next((r for r in wf_with_cost if r["layer"] == "1L"), None)
        rec_with_cost = l1_with_cost["recovery_pct"] if l1_with_cost else 0.0
        rows.append(
            {
                "scenario": sc["label"],
                "ev_gross": ev_gross,
                "ch11_costs": ch11_costs,
                "ev_net": sc["ev_net"],
                "l1_recovery_without_costs": rec_no_cost,
                "l1_recovery_with_costs": rec_with_cost,
                "recovery_drag_pp": rec_no_cost - rec_with_cost,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Core Monte Carlo
# ---------------------------------------------------------------------------

def simulate(model: CapTableModel, assumptions: SimulationAssumptions) -> dict[str, Any]:
    rng = np.random.default_rng(assumptions.seed)
    runs = assumptions.runs

    ebitda = lognormal_samples(model.base_ebitda, assumptions.ebitda_cv, runs, rng)
    exit_multiple = rng.triangular(model.multiple_low, model.multiple_mid, model.multiple_high, runs)
    debt_scale = np.clip(
        1 + rng.normal(0, assumptions.debt_volatility, runs),
        0.75,
        1.35,
    )

    enterprise_value = ebitda * exit_multiple
    other_claims = model.other_claims * debt_scale

    layer_totals: dict[str, float] = {}
    for instrument in model.instruments:
        layer_totals[instrument.layer] = layer_totals.get(instrument.layer, 0.0) + instrument.principal

    ordered_layers = [layer for layer in ("Super", "1L", "2L") if layer in layer_totals]
    layer_balances = {layer: layer_totals[layer] * debt_scale for layer in ordered_layers}

    recovered_amounts, recovery_rates, equity_value = run_waterfall(
        ordered_layers,
        layer_balances,
        enterprise_value,
        other_claims,
    )

    total_debt_to_clear = model.total_debt_to_clear * debt_scale
    insolvency_probability = float(np.mean(enterprise_value < total_debt_to_clear))

    layer_stats: list[dict[str, Any]] = []
    for layer in ordered_layers:
        balance = layer_totals[layer]
        rate = recovery_rates[layer]
        recovered = recovered_amounts[layer]
        layer_stats.append(
            {
                "layer": layer,
                "current_claim": balance,
                "expected_claim_at_exit": float(np.mean(layer_balances[layer])),
                "expected_recovered": float(np.mean(recovered)),
                "expected_recovery_pct": float(np.mean(rate)),
                "money_good_probability": float(np.mean(rate >= 0.999)),
                "recovery_distribution": summarize_distribution(rate),
            }
        )

    instrument_stats: list[dict[str, Any]] = []
    layer_rate_lookup = {row["layer"]: recovery_rates[row["layer"]] for row in layer_stats}
    layer_balance_lookup = {layer: layer_balances[layer] for layer in ordered_layers}
    for instrument in model.instruments:
        if instrument.layer not in layer_rate_lookup:
            continue
        claim_share = instrument.principal / layer_totals[instrument.layer]
        recovered = recovered_amounts[instrument.layer] * claim_share
        rate = layer_rate_lookup[instrument.layer]
        instrument_stats.append(
            {
                "layer": instrument.layer,
                "instrument": instrument.name,
                "principal": instrument.principal,
                "maturity": instrument.maturity,
                "interest_type": instrument.interest_type,
                "is_pik": instrument.is_pik,
                "pik_rate_pct": instrument.pik_rate * 100,
                "expected_claim_at_exit": float(np.mean(layer_balance_lookup[instrument.layer] * claim_share)),
                "expected_recovered": float(np.mean(recovered)),
                "expected_recovery_pct": float(np.mean(rate)),
                "money_good_probability": float(np.mean(rate >= 0.999)),
                "recovery_distribution": summarize_distribution(rate),
            }
        )

    # Deterministic reference scenarios (original: distressed / base / healthy)
    low_ev = model.base_ebitda * model.multiple_low
    base_ev = model.base_ebitda * model.multiple_mid
    high_ev = model.base_ebitda * model.multiple_high
    scenarios = [
        {
            "label": "Distressed",
            "enterprise_value": low_ev,
            "waterfall": scenario_waterfall(low_ev, layer_totals, model.other_claims),
        },
        {
            "label": "Base",
            "enterprise_value": base_ev,
            "waterfall": scenario_waterfall(base_ev, layer_totals, model.other_claims),
        },
        {
            "label": "Healthy",
            "enterprise_value": high_ev,
            "waterfall": scenario_waterfall(high_ev, layer_totals, model.other_claims),
        },
    ]

    # ---- New analyses ----
    pik_accretion = build_pik_accretion_table(model.instruments, assumptions.resolution_months)
    ebitda_scenarios = build_ebitda_scenarios(model, assumptions)
    implied_price = build_implied_price_analysis(ebitda_scenarios, assumptions, layer_totals)
    ch11_leakage = build_ch11_leakage(ebitda_scenarios, assumptions.ch11_costs, layer_totals, model.other_claims)

    # Fulcrum at base EV (net of Ch11 costs)
    base_ev_net = max(base_ev - assumptions.ch11_costs, 0.0)
    fulcrum_base = identify_fulcrum(base_ev_net, layer_totals, model.other_claims)

    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "workbook": {
            "name": model.workbook_name,
            "base_ebitda": model.base_ebitda,
            "multiple_low": model.multiple_low,
            "multiple_mid": model.multiple_mid,
            "multiple_high": model.multiple_high,
            "listed_debt": model.listed_debt,
            "other_claims": model.other_claims,
            "total_debt_to_clear": model.total_debt_to_clear,
            "instruments": [asdict(inst) for inst in model.instruments],
        },
        "assumptions": asdict(assumptions),
        "summary": {
            "insolvency_probability": insolvency_probability,
            "positive_equity_probability": float(np.mean(equity_value > 0)),
            "expected_equity_value": float(np.mean(equity_value)),
            "equity_distribution": summarize_distribution(equity_value),
            "enterprise_value_distribution": summarize_distribution(enterprise_value),
            "ebitda_distribution": summarize_distribution(ebitda),
            "exit_multiple_distribution": summarize_distribution(exit_multiple),
        },
        "charts": {
            "enterprise_value_histogram": build_histogram(enterprise_value, bins=18),
            "equity_histogram": build_histogram(equity_value, bins=14),
        },
        "layer_stats": layer_stats,
        "instrument_stats": instrument_stats,
        "scenarios": scenarios,
        "sensitivity": build_sensitivity_matrix(model),
        # ---- New ----
        "pik_accretion": pik_accretion,
        "ebitda_scenarios": ebitda_scenarios,
        "implied_price": implied_price,
        "ch11_leakage": ch11_leakage,
        "fulcrum_base": fulcrum_base,
    }


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def json_for_html(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).replace("</", "<\\/")


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def render_report(report: dict[str, Any]) -> str:
    data_blob = json_for_html(report)
    title = escape(report["workbook"]["name"])
    generated_at = escape(report["generated_at"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - TSE Cap Table Simulation</title>
<style>
*,*::before,*::after{{box-sizing:border-box}}
:root{{
  --bg:#f6f4ef;
  --paper:#ffffff;
  --paper-strong:#fcfbf8;
  --border:#ddd6ca;
  --ink:#1e1b16;
  --muted:#746b5f;
  --accent:#20262d;
  --accent-soft:#e8ecef;
  --good:#246a4a;
  --warn:#a4631b;
  --bad:#8b3d35;
  --fulcrum:#185fa5;
  --shadow:0 22px 60px rgba(28,24,18,.08);
}}
html{{
  background:radial-gradient(circle at top left,rgba(232,236,239,.9),transparent 28rem),
    linear-gradient(180deg,#fbfaf8 0%,#f3efe7 100%);
  min-height:100%;
}}
body{{margin:0;color:var(--ink);font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;min-height:100vh}}
.shell{{max-width:1320px;margin:0 auto;padding:32px 22px 56px}}
.hero{{background:linear-gradient(180deg,rgba(255,255,255,.95),rgba(251,249,245,.98));border:1px solid rgba(221,214,202,.9);border-radius:28px;box-shadow:var(--shadow);padding:32px;position:relative;overflow:hidden}}
.hero::after{{content:"";position:absolute;inset:auto -80px -90px auto;width:280px;height:280px;border-radius:50%;background:radial-gradient(circle,rgba(232,236,239,.95),rgba(232,236,239,0))}}
.eyebrow,.label,.small,.table th,.table td,.axis-label,.cell-note{{font-family:"SFMono-Regular","IBM Plex Mono","Menlo",monospace}}
.eyebrow{{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin-bottom:16px}}
.hero h1{{font-size:clamp(2.3rem,5vw,4.6rem);line-height:.95;letter-spacing:-.05em;margin:0;max-width:12ch}}
.hero p{{max-width:48rem;font-size:1.02rem;line-height:1.7;color:#453d33;margin:18px 0 0}}
.hero-grid{{display:grid;grid-template-columns:1.6fr .9fr;gap:22px;align-items:end}}
.meta-card{{background:rgba(250,248,244,.92);border:1px solid var(--border);border-radius:22px;padding:18px 18px 16px;position:relative;z-index:1}}
.meta-line{{display:flex;justify-content:space-between;gap:14px;padding:10px 0;border-bottom:1px solid rgba(221,214,202,.8)}}
.meta-line:last-child{{border-bottom:none;padding-bottom:0}}
.meta-key{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.12em}}
.meta-value{{font-size:13px;text-align:right}}
.grid{{display:grid;gap:18px;margin-top:18px}}
.metrics{{grid-template-columns:repeat(4,minmax(0,1fr))}}
.card{{background:rgba(255,255,255,.92);border:1px solid rgba(221,214,202,.92);border-radius:24px;padding:22px;box-shadow:0 10px 28px rgba(28,24,18,.04)}}
.metric-value{{font-size:clamp(1.8rem,4vw,3rem);line-height:.95;letter-spacing:-.05em}}
.metric-value.good{{color:var(--good)}}
.metric-value.warn{{color:var(--warn)}}
.metric-value.bad{{color:var(--bad)}}
.label{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-top:8px}}
.section{{margin-top:18px}}
.split{{display:grid;grid-template-columns:1.08fr .92fr;gap:18px}}
.three-col{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}}
.histogram{{display:flex;align-items:flex-end;gap:6px;height:280px;margin-top:18px;padding-top:10px}}
.bar{{flex:1;border-radius:14px 14px 4px 4px;background:linear-gradient(180deg,#20262d,#cfd7dc);position:relative;min-width:12px}}
.bar::after{{content:attr(data-tip);position:absolute;left:50%;bottom:calc(100% + 10px);transform:translateX(-50%);background:#1f252c;color:#fff;padding:7px 8px;border-radius:8px;font-size:10px;line-height:1.4;opacity:0;pointer-events:none;transition:opacity .15s;white-space:nowrap;z-index:10}}
.bar:hover::after{{opacity:1}}
.axis{{display:flex;justify-content:space-between;margin-top:12px;color:var(--muted);font-size:11px}}
.recovery-list{{display:grid;gap:12px;margin-top:16px}}
.recovery-row{{display:grid;grid-template-columns:140px 1fr 110px;gap:16px;align-items:center;padding:14px 16px;border:1px solid rgba(221,214,202,.9);border-radius:18px;background:linear-gradient(180deg,#fff,#fcfaf6)}}
.recovery-name{{font-size:1rem}}
.track{{height:14px;background:#efe9de;border-radius:999px;overflow:hidden}}
.fill{{height:100%;border-radius:999px;background:linear-gradient(90deg,#20262d,#5d7688)}}
.recovery-meta{{text-align:right}}
.kpi-inline{{display:flex;gap:20px;flex-wrap:wrap;margin-top:14px;color:#4f483f}}
.kpi-inline span{{display:block}}
.kpi-inline small{{font-family:"SFMono-Regular","IBM Plex Mono","Menlo",monospace;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.1em}}
.scenario-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}
.scenario-card{{padding:18px;border-radius:22px;border:1px solid rgba(221,214,202,.9);background:linear-gradient(180deg,#ffffff,#fbfaf7)}}
.scenario-card h3{{margin:0 0 8px;font-size:1.35rem;letter-spacing:-.03em}}
.stack{{display:grid;gap:10px;margin-top:14px}}
.stack-row{{display:grid;grid-template-columns:1fr auto;gap:14px;padding-top:10px;border-top:1px solid rgba(221,214,202,.85)}}
.stack-row:first-child{{border-top:none;padding-top:0}}
.table-wrap{{overflow:auto;margin-top:14px}}
.table{{width:100%;border-collapse:collapse}}
.table th{{font-size:10px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);text-align:left;padding:0 0 10px}}
.table td{{padding:12px 0;border-top:1px solid rgba(221,214,202,.85);font-size:13px;vertical-align:top}}
.table td strong{{display:block;font-size:14px;margin-bottom:3px}}
.heatmap{{display:grid;grid-template-columns:120px repeat(5,1fr);gap:8px;margin-top:18px;align-items:stretch}}
.heatmap-header{{font-size:11px;color:var(--muted);padding:10px 8px}}
.heatmap-label{{display:flex;align-items:center;font-size:11px;color:var(--muted);padding-left:4px}}
.cell{{padding:12px;border-radius:16px;border:1px solid rgba(221,214,202,.85);min-height:96px;display:flex;flex-direction:column;justify-content:space-between}}
.cell-note{{font-size:10px;line-height:1.5}}
/* ---- new sections ---- */
.ebitda-card{{padding:20px;border-radius:22px;border:1px solid rgba(221,214,202,.9);background:linear-gradient(180deg,#fff,#fbfaf7)}}
.ebitda-card .case-label{{font-size:1.1rem;font-weight:700;letter-spacing:-.02em;margin-bottom:6px}}
.ebitda-card .rationale{{font-size:12px;color:var(--muted);font-family:"SFMono-Regular","IBM Plex Mono","Menlo",monospace;line-height:1.6;margin-bottom:14px}}
.ebitda-card .wf-row{{display:flex;justify-content:space-between;font-size:13px;padding:6px 0;border-top:1px solid rgba(221,214,202,.7)}}
.ebitda-card .wf-row:first-of-type{{border-top:none}}
.ebitda-card .wf-layer{{color:var(--muted)}}
.fulcrum-banner{{margin-top:10px;padding:8px 12px;border-radius:10px;background:rgba(24,95,165,.08);border:1px solid rgba(24,95,165,.22);font-size:12px;color:var(--fulcrum);font-family:"SFMono-Regular","IBM Plex Mono","Menlo",monospace}}
.implied-table{{width:100%;border-collapse:collapse;margin-top:14px}}
.implied-table th{{font-size:10px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);text-align:left;padding-bottom:10px}}
.implied-table td{{font-size:13px;padding:10px 0;border-top:1px solid rgba(221,214,202,.85)}}
.implied-table .gap-pos{{color:var(--good);font-weight:700}}
.implied-table .gap-neg{{color:var(--bad);font-weight:700}}
.pik-table{{width:100%;border-collapse:collapse;margin-top:14px}}
.pik-table th{{font-size:10px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);text-align:left;padding-bottom:10px}}
.pik-table td{{font-size:13px;padding:10px 0;border-top:1px solid rgba(221,214,202,.85)}}
.leakage-row{{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-top:1px solid rgba(221,214,202,.85);font-size:13px}}
.leakage-row:first-child{{border-top:none}}
.badge{{display:inline-block;padding:2px 8px;border-radius:6px;font-size:10px;font-family:"SFMono-Regular","IBM Plex Mono","Menlo",monospace;font-weight:700}}
.badge-fulcrum{{background:rgba(24,95,165,.12);color:var(--fulcrum)}}
.badge-good{{background:rgba(36,106,74,.1);color:var(--good)}}
.badge-bad{{background:rgba(139,61,53,.1);color:var(--bad)}}
.market-implied-box{{margin-top:18px;padding:16px 20px;border-radius:18px;background:rgba(28,24,18,.04);border:1px solid rgba(221,214,202,.9)}}
.market-implied-box .mi-label{{font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted)}}
.market-implied-box .mi-value{{font-size:1.6rem;letter-spacing:-.04em;margin-top:4px}}
.footer{{margin-top:18px;color:var(--muted);font-size:12px;text-align:right}}
@media(max-width:1024px){{.hero-grid,.split,.scenario-grid,.metrics,.three-col{{grid-template-columns:1fr 1fr}}}}
@media(max-width:760px){{
  .shell{{padding:18px 14px 40px}}
  .hero,.card{{padding:20px}}
  .hero-grid,.split,.scenario-grid,.metrics,.three-col{{grid-template-columns:1fr}}
  .recovery-row{{grid-template-columns:1fr;gap:10px}}
  .recovery-meta{{text-align:left}}
  .heatmap{{grid-template-columns:1fr}}
  .heatmap-header{{display:none}}
  .heatmap-label{{padding-left:0}}
}}
</style>
</head>
<body>
<div class="shell">

  <section class="hero">
    <div class="hero-grid">
      <div>
        <div class="eyebrow">TSE cap table recovery engine</div>
        <h1>Monte Carlo exit waterfall.</h1>
        <p>
          Analysis over <strong>{title}</strong> — Monte Carlo recovery simulation,
          three EBITDA cases, PIK accretion, fulcrum security identification,
          Chapter 11 cost leakage, and implied 1L trading price vs market.
        </p>
      </div>
      <div class="meta-card">
        <div class="meta-line"><div class="meta-key">Generated</div><div class="meta-value">{generated_at}</div></div>
        <div class="meta-line"><div class="meta-key">Runs</div><div class="meta-value" id="runs"></div></div>
        <div class="meta-line"><div class="meta-key">EBITDA base</div><div class="meta-value" id="ebitda-base"></div></div>
        <div class="meta-line"><div class="meta-key">Multiple range</div><div class="meta-value" id="multiple-range"></div></div>
        <div class="meta-line"><div class="meta-key">Debt to clear</div><div class="meta-value" id="debt-total"></div></div>
        <div class="meta-line"><div class="meta-key">1L market price</div><div class="meta-value" id="market-price"></div></div>
      </div>
    </div>
  </section>

  <section class="grid metrics">
    <article class="card">
      <div class="metric-value bad" id="insolvency-prob"></div>
      <div class="label">Probability EV fails to clear debt</div>
    </article>
    <article class="card">
      <div class="metric-value" id="median-ev"></div>
      <div class="label">Median enterprise value</div>
    </article>
    <article class="card">
      <div class="metric-value good" id="equity-prob"></div>
      <div class="label">Probability equity has value</div>
    </article>
    <article class="card">
      <div class="metric-value warn" id="expected-equity"></div>
      <div class="label">Expected residual equity</div>
    </article>
  </section>

  <section class="grid split section">
    <article class="card">
      <div class="eyebrow">Enterprise value distribution</div>
      <div class="metric-value" id="mean-ev"></div>
      <div class="label">Mean simulated EV</div>
      <div class="kpi-inline">
        <span><strong id="ev-p10"></strong><br><small>P10</small></span>
        <span><strong id="ev-p50"></strong><br><small>P50</small></span>
        <span><strong id="ev-p90"></strong><br><small>P90</small></span>
      </div>
      <div class="histogram" id="ev-histogram"></div>
      <div class="axis">
        <div class="axis-label" id="ev-axis-start"></div>
        <div class="axis-label" id="ev-axis-end"></div>
      </div>
    </article>
    <article class="card">
      <div class="eyebrow">Layer recovery outlook</div>
      <div class="recovery-list" id="layer-recoveries"></div>
    </article>
  </section>

  <!-- EBITDA scenarios -->
  <section class="card section">
    <div class="eyebrow">Three EBITDA cases — what should the model use?</div>
    <div class="three-col" id="ebitda-scenario-grid" style="margin-top:14px"></div>
    <div class="market-implied-box">
      <div class="mi-label">Market-implied EBITDA (derived from 11c 1L trading price)</div>
      <div class="mi-value" id="market-implied-ebitda"></div>
      <div style="font-size:12px;color:var(--muted);margin-top:6px;font-family:'SFMono-Regular',monospace">
        Assumes: Super ($<span id="mi-super"></span>M) paid in full, then 11c × 1L face recovers remainder.
        Divide by <span id="mi-mult"></span>x mid multiple to back-solve EBITDA.
        Market is pricing the distressed case.
      </div>
    </div>
  </section>

  <!-- Fulcrum security -->
  <section class="grid split section">
    <article class="card">
      <div class="eyebrow">Fulcrum security — base case EV (net of Ch.11 costs)</div>
      <div id="fulcrum-rows" style="margin-top:14px"></div>
      <div style="margin-top:14px;font-size:13px;line-height:1.7;color:#453d33">
        The fulcrum tranche receives partial recovery and converts to equity in the reorganised company.
        Buying the fulcrum below implied fair value is the core distressed investment thesis.
      </div>
    </article>
    <article class="card">
      <div class="eyebrow">Chapter 11 cost leakage</div>
      <div style="font-size:13px;color:var(--muted);margin-bottom:10px">
        Professional fees (~$<span id="ch11-cost-display"></span>M) reduce distributable EV before waterfall.
        Leakage is felt entirely by the fulcrum and junior tranches.
      </div>
      <div id="leakage-rows"></div>
    </article>
  </section>

  <!-- Implied trading price -->
  <section class="card section">
    <div class="eyebrow">Implied fair value vs market price (1L debt)</div>
    <div style="font-size:13px;color:#453d33;line-height:1.7;margin-bottom:6px">
      Implied fair value = expected recovery discounted at
      <strong id="dr-display"></strong>% over <strong id="res-months-display"></strong> months.
      Gap = implied fair value minus <strong>11c</strong> market price. Positive gap = 1L is cheap vs model.
    </div>
    <table class="implied-table">
      <thead>
        <tr>
          <th>EBITDA case</th>
          <th>EBITDA ($M)</th>
          <th>Net EV ($M)</th>
          <th>1L recovery</th>
          <th>Recovery (¢)</th>
          <th>Implied fair value (¢)</th>
          <th>Market price (¢)</th>
          <th>Gap (¢)</th>
          <th>Gross return if bought at 11¢</th>
        </tr>
      </thead>
      <tbody id="implied-table-body"></tbody>
    </table>
  </section>

  <!-- PIK accretion -->
  <section class="card section">
    <div class="eyebrow">PIK accretion — delay compounds the debt</div>
    <div style="font-size:13px;color:#453d33;line-height:1.7;margin-bottom:6px">
      Term Loan B carries SOFR + 8.5% (~<strong id="pik-rate-display"></strong>% p.a.) with a PIK toggle.
      Every month of restructuring delay grows the outstanding balance, compressing recovery for all 1L holders.
    </div>
    <table class="pik-table" id="pik-table">
      <thead>
        <tr>
          <th>Instrument</th>
          <th>Layer</th>
          <th>Original balance ($M)</th>
          <th>PIK rate (% p.a.)</th>
          <th>Resolution (months)</th>
          <th>Accreted balance ($M)</th>
          <th>Additional debt ($M)</th>
        </tr>
      </thead>
      <tbody id="pik-tbody"></tbody>
    </table>
  </section>

  <!-- Original deterministic scenarios -->
  <section class="grid split section">
    <article class="card">
      <div class="eyebrow">Deterministic reference cases</div>
      <div class="scenario-grid" id="scenario-grid"></div>
    </article>
    <article class="card">
      <div class="eyebrow">Sensitivity map</div>
      <div class="label">Each cell: EV, 2L recovery, residual equity.</div>
      <div class="heatmap" id="heatmap"></div>
    </article>
  </section>

  <!-- Instrument table -->
  <section class="card section">
    <div class="eyebrow">Instrument detail</div>
    <div class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Instrument</th><th>Layer</th><th>Principal</th><th>Maturity</th>
            <th>Interest</th><th>PIK</th><th>Expected recovery</th><th>Money-good</th>
          </tr>
        </thead>
        <tbody id="instrument-table"></tbody>
      </table>
    </div>
  </section>

  <div class="footer">Generated by <code>simulate_tse_cap_table.py</code> &mdash; TSE distressed credit model.</div>
</div>

<script id="report-data" type="application/json">{data_blob}</script>
<script>
const report = JSON.parse(document.getElementById('report-data').textContent);

const money = v => `$${{Number(v).toLocaleString(undefined,{{minimumFractionDigits:1,maximumFractionDigits:1}})}}M`;
const pct   = v => `${{(Number(v)*100).toFixed(1)}}%`;
const cents = v => `${{Number(v).toFixed(1)}}¢`;
const clamp = (v,a,b) => Math.min(b,Math.max(a,v));
const fix1  = v => Number(v).toFixed(1);

// Meta
document.getElementById('runs').textContent           = report.assumptions.runs.toLocaleString();
document.getElementById('ebitda-base').textContent    = money(report.workbook.base_ebitda);
document.getElementById('multiple-range').textContent = `${{report.workbook.multiple_low.toFixed(1)}}x to ${{report.workbook.multiple_high.toFixed(1)}}x`;
document.getElementById('debt-total').textContent     = money(report.workbook.total_debt_to_clear);
document.getElementById('market-price').textContent   = `${{report.assumptions.market_price_1l.toFixed(0)}}¢`;

// Summary
document.getElementById('insolvency-prob').textContent  = pct(report.summary.insolvency_probability);
document.getElementById('median-ev').textContent        = money(report.summary.enterprise_value_distribution.p50);
document.getElementById('equity-prob').textContent      = pct(report.summary.positive_equity_probability);
document.getElementById('expected-equity').textContent  = money(report.summary.expected_equity_value);
document.getElementById('mean-ev').textContent          = money(report.summary.enterprise_value_distribution.mean);
document.getElementById('ev-p10').textContent           = money(report.summary.enterprise_value_distribution.p10);
document.getElementById('ev-p50').textContent           = money(report.summary.enterprise_value_distribution.p50);
document.getElementById('ev-p90').textContent           = money(report.summary.enterprise_value_distribution.p90);

// EV histogram
const evHistogram = report.charts.enterprise_value_histogram;
const maxCount = Math.max(...evHistogram.map(b=>b.count),1);
const histEl = document.getElementById('ev-histogram');
evHistogram.forEach(bin=>{{
  const bar = document.createElement('div');
  bar.className='bar';
  bar.style.height=`${{Math.max(8,(bin.count/maxCount)*100)}}%`;
  bar.dataset.tip=`${{money(bin.start)}} – ${{money(bin.end)}} · ${{bin.count.toLocaleString()}} runs`;
  histEl.appendChild(bar);
}});
document.getElementById('ev-axis-start').textContent = money(evHistogram[0].start);
document.getElementById('ev-axis-end').textContent   = money(evHistogram[evHistogram.length-1].end);

// Layer recoveries
const layerRecoveries = document.getElementById('layer-recoveries');
report.layer_stats.forEach(layer=>{{
  const row = document.createElement('div');
  row.className='recovery-row';
  row.innerHTML=`
    <div>
      <div class="label">${{layer.layer}}</div>
      <div class="recovery-name">${{money(layer.current_claim)}}</div>
    </div>
    <div>
      <div class="track"><div class="fill" style="width:${{clamp(layer.expected_recovery_pct*100,0,100)}}%"></div></div>
      <div class="kpi-inline">
        <span><strong>${{pct(layer.recovery_distribution.p10)}}</strong><br><small>P10</small></span>
        <span><strong>${{pct(layer.recovery_distribution.p50)}}</strong><br><small>P50</small></span>
        <span><strong>${{pct(layer.recovery_distribution.p90)}}</strong><br><small>P90</small></span>
      </div>
    </div>
    <div class="recovery-meta">
      <div><strong>${{pct(layer.expected_recovery_pct)}}</strong></div>
      <div class="label">Expected recovery</div>
      <div style="margin-top:8px"><strong>${{pct(layer.money_good_probability)}}</strong></div>
      <div class="label">Money-good</div>
    </div>`;
  layerRecoveries.appendChild(row);
}});

// EBITDA scenarios
const esgrid = document.getElementById('ebitda-scenario-grid');
const colorMap = {{good:'var(--good)',warn:'var(--warn)',bad:'var(--bad)'}};
report.ebitda_scenarios.forEach(sc=>{{
  const card = document.createElement('div');
  card.className='ebitda-card';
  const wfRows = sc.waterfall.map(r=>`
    <div class="wf-row">
      <span class="wf-layer">${{r.layer}}</span>
      <span>${{r.layer==='Equity residual'?money(r.recovered):`${{money(r.recovered)}} · ${{pct(r.recovery_pct)}}`}}</span>
    </div>`).join('');
  const fulcrumHtml = sc.fulcrum_layer
    ? `<div class="fulcrum-banner">Fulcrum: <strong>${{sc.fulcrum_layer}}</strong> — partial recovery, becomes new equity</div>`
    : `<div class="fulcrum-banner" style="background:rgba(139,61,53,.06);border-color:rgba(139,61,53,.2);color:var(--bad)">No fulcrum — EV insufficient to reach any partial tranche</div>`;
  card.innerHTML=`
    <div class="case-label" style="color:${{colorMap[sc.color]||'var(--ink)'}}">
      ${{sc.label}} — ${{money(sc.ebitda)}} EBITDA
    </div>
    <div class="rationale">${{sc.rationale}}</div>
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px;font-family:monospace">
      EV gross: ${{money(sc.ev_gross)}} &nbsp;|&nbsp; Ch.11 costs: ${{money(sc.ch11_costs)}} &nbsp;|&nbsp; Net: ${{money(sc.ev_net)}}
    </div>
    ${{wfRows}}
    ${{fulcrumHtml}}`;
  esgrid.appendChild(card);
}});

// Market-implied EBITDA
const ip = report.implied_price;
document.getElementById('market-implied-ebitda').textContent   = money(ip.market_implied_ebitda);
document.getElementById('mi-super').textContent                = fix1(report.workbook.instruments.filter(i=>i.layer==='Super').reduce((s,i)=>s+i.principal,0));
document.getElementById('mi-mult').textContent                 = fix1(ip.market_implied_multiple);

// Fulcrum security
const fRows = document.getElementById('fulcrum-rows');
report.fulcrum_base.waterfall.forEach(r=>{{
  const isFulcrum = r.is_fulcrum;
  const div = document.createElement('div');
  div.style.cssText='display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-top:1px solid rgba(221,214,202,.85);font-size:13px';
  if(isFulcrum) div.style.background='rgba(24,95,165,.05)';
  div.innerHTML=`
    <span style="width:120px;color:var(--muted)">${{r.layer}}</span>
    <span style="flex:1;text-align:center">${{r.claim>0?money(r.claim):'—'}}</span>
    <span style="width:80px;text-align:right">${{r.claim>0?pct(r.recovery_pct):'—'}}</span>
    <span style="width:160px;text-align:right">
      <span class="badge ${{isFulcrum?'badge-fulcrum':r.recovery_pct>=0.999?'badge-good':'badge-bad'}}">${{r.status.split(' —')[0]}}</span>
    </span>`;
  fRows.appendChild(div);
}});

// Ch11 leakage
document.getElementById('ch11-cost-display').textContent = fix1(report.assumptions.ch11_costs);
const leakageRows = document.getElementById('leakage-rows');
report.ch11_leakage.forEach(r=>{{
  const drag = (r.recovery_drag_pp*100).toFixed(1);
  const div = document.createElement('div');
  div.className='leakage-row';
  div.innerHTML=`
    <span style="font-weight:700">${{r.scenario}}</span>
    <span style="color:var(--muted);font-size:12px">Without costs: ${{pct(r.l1_recovery_without_costs)}}</span>
    <span style="color:var(--muted);font-size:12px">With costs: ${{pct(r.l1_recovery_with_costs)}}</span>
    <span style="color:var(--bad);font-size:12px;font-weight:700">&#8722;${{drag}}pp</span>`;
  leakageRows.appendChild(div);
}});

// Implied price table
document.getElementById('dr-display').textContent        = fix1(ip.discount_rate);
document.getElementById('res-months-display').textContent = ip.resolution_months;
const itbody = document.getElementById('implied-table-body');
ip.scenarios.forEach(r=>{{
  const tr = document.createElement('tr');
  const gapCls = r.gap_cents>=0?'gap-pos':'gap-neg';
  const gapSign = r.gap_cents>=0?'+':'';
  tr.innerHTML=`
    <td><strong>${{r.scenario}}</strong></td>
    <td>${{money(r.ebitda)}}</td>
    <td>${{money(r.ev_net)}}</td>
    <td>${{pct(r.recovery_pct)}}</td>
    <td>${{cents(r.recovery_cents)}}</td>
    <td>${{cents(r.implied_fair_value)}}</td>
    <td>11.0¢</td>
    <td class="${{gapCls}}">${{gapSign}}${{fix1(r.gap_cents)}}¢</td>
    <td class="${{r.implied_gross_return_pct>=0?'gap-pos':'gap-neg'}}">${{gapSign}}${{fix1(r.implied_gross_return_pct)}}%</td>`;
  itbody.appendChild(tr);
}});

// PIK accretion
if(report.pik_accretion && report.pik_accretion.length>0){{
  document.getElementById('pik-rate-display').textContent = fix1(report.pik_accretion[0].pik_rate_pct);
  const pikTbody = document.getElementById('pik-tbody');
  report.pik_accretion.forEach(r=>{{
    const tr = document.createElement('tr');
    tr.innerHTML=`
      <td><strong>${{r.instrument}}</strong></td>
      <td>${{r.layer}}</td>
      <td>${{money(r.original_balance)}}</td>
      <td>${{fix1(r.pik_rate_pct)}}%</td>
      <td>${{r.months}}</td>
      <td><strong>${{money(r.accreted_balance)}}</strong></td>
      <td style="color:var(--bad)">${{money(r.additional_debt)}}</td>`;
    pikTbody.appendChild(tr);
  }});
}} else {{
  document.getElementById('pik-rate-display').textContent = '13.8';
  document.getElementById('pik-table').innerHTML='<p style="font-size:13px;color:var(--muted);padding:14px 0">No PIK instruments detected in workbook.</p>';
}}

// Original scenarios
const scenarioGrid = document.getElementById('scenario-grid');
report.scenarios.forEach(scenario=>{{
  const card = document.createElement('div');
  card.className='scenario-card';
  const rows = scenario.waterfall.map(item=>`
    <div class="stack-row">
      <div>${{item.layer}}</div>
      <div>${{item.layer==='Equity residual'?money(item.recovered):`${{money(item.recovered)}} · ${{pct(item.recovery_pct)}}`}}</div>
    </div>`).join('');
  card.innerHTML=`<h3>${{scenario.label}}</h3><div class="label">${{money(scenario.enterprise_value)}} enterprise value</div><div class="stack">${{rows}}</div>`;
  scenarioGrid.appendChild(card);
}});

// Heatmap
const heatmap = document.getElementById('heatmap');
heatmap.innerHTML='<div></div>'+report.sensitivity.multiple_values.map(v=>`<div class="heatmap-header">${{v.toFixed(2)}}x</div>`).join('');
const maxEquity = report.sensitivity.max_equity||1;
report.sensitivity.cells.forEach((row,ri)=>{{
  const label = document.createElement('div');
  label.className='heatmap-label';
  label.textContent=money(report.sensitivity.ebitda_values[ri])+' EBITDA';
  heatmap.appendChild(label);
  row.forEach(cell=>{{
    const tile = document.createElement('div');
    tile.className='cell';
    const intensity = clamp(cell.equity_value/maxEquity,0,1);
    const ink = intensity>0.56?'#ffffff':'#1f1c17';
    tile.style.background=`linear-gradient(180deg,rgba(32,38,45,${{0.08+intensity*0.86}}),rgba(232,236,239,.88))`;
    tile.style.color=ink;
    tile.innerHTML=`<div><strong>${{money(cell.enterprise_value)}}</strong></div>
      <div class="cell-note" style="color:${{ink==='#ffffff'?'rgba(255,255,255,.84)':'#5c5348'}}">
        2L recovery: ${{pct(cell.second_lien_recovery)}}<br>Equity: ${{money(cell.equity_value)}}
      </div>`;
    heatmap.appendChild(tile);
  }});
}});

// Instrument table
const instrumentTable = document.getElementById('instrument-table');
report.instrument_stats.forEach(item=>{{
  const row = document.createElement('tr');
  const pikLabel = item.is_pik?`<span class="badge badge-fulcrum">PIK ${{fix1(item.pik_rate_pct)}}%</span>`:'—';
  row.innerHTML=`
    <td><strong>${{item.instrument}}</strong></td>
    <td>${{item.layer}}</td>
    <td>${{money(item.principal)}}</td>
    <td>${{item.maturity}}</td>
    <td>${{item.interest_type}}</td>
    <td>${{pikLabel}}</td>
    <td>${{money(item.expected_recovered)}} · ${{pct(item.expected_recovery_pct)}}</td>
    <td>${{pct(item.money_good_probability)}}</td>`;
  instrumentTable.appendChild(row);
}});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    workbook_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    model = load_cap_table(workbook_path)
    assumptions = SimulationAssumptions(
        runs=args.runs,
        seed=args.seed,
        ebitda_cv=args.ebitda_cv,
        debt_volatility=args.debt_volatility,
        ebitda_distressed=args.ebitda_distressed,
        ebitda_clean=args.ebitda_clean,
        ebitda_recovery=args.ebitda_recovery,
        market_price_1l=args.market_price_1l,
        discount_rate=args.discount_rate,
        resolution_months=args.resolution_months,
        ch11_costs=args.ch11_costs,
    )
    report = simulate(model, assumptions)

    json_path            = output_dir / "tse_cap_table_simulation.json"
    csv_layers_path      = output_dir / "tse_cap_table_layer_stats.csv"
    csv_instruments_path = output_dir / "tse_cap_table_instrument_stats.csv"
    csv_scenarios_path   = output_dir / "tse_cap_table_scenarios.csv"
    csv_implied_path     = output_dir / "tse_cap_table_implied_price.csv"
    csv_pik_path         = output_dir / "tse_cap_table_pik_accretion.csv"
    html_path            = output_dir / "tse_cap_table_report.html"

    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    write_csv(
        csv_layers_path,
        [
            {
                "layer": r["layer"],
                "current_claim_m": f"{r['current_claim']:.4f}",
                "expected_claim_at_exit_m": f"{r['expected_claim_at_exit']:.4f}",
                "expected_recovered_m": f"{r['expected_recovered']:.4f}",
                "expected_recovery_pct": f"{r['expected_recovery_pct']:.6f}",
                "money_good_probability": f"{r['money_good_probability']:.6f}",
                "p10_recovery_pct": f"{r['recovery_distribution']['p10']:.6f}",
                "p50_recovery_pct": f"{r['recovery_distribution']['p50']:.6f}",
                "p90_recovery_pct": f"{r['recovery_distribution']['p90']:.6f}",
            }
            for r in report["layer_stats"]
        ],
        ["layer","current_claim_m","expected_claim_at_exit_m","expected_recovered_m",
         "expected_recovery_pct","money_good_probability","p10_recovery_pct","p50_recovery_pct","p90_recovery_pct"],
    )

    write_csv(
        csv_instruments_path,
        [
            {
                "layer": r["layer"],
                "instrument": r["instrument"],
                "principal_m": f"{r['principal']:.4f}",
                "maturity": r["maturity"],
                "interest_type": r["interest_type"],
                "is_pik": r["is_pik"],
                "pik_rate_pct": f"{r['pik_rate_pct']:.4f}",
                "expected_claim_at_exit_m": f"{r['expected_claim_at_exit']:.4f}",
                "expected_recovered_m": f"{r['expected_recovered']:.4f}",
                "expected_recovery_pct": f"{r['expected_recovery_pct']:.6f}",
                "money_good_probability": f"{r['money_good_probability']:.6f}",
            }
            for r in report["instrument_stats"]
        ],
        ["layer","instrument","principal_m","maturity","interest_type","is_pik","pik_rate_pct",
         "expected_claim_at_exit_m","expected_recovered_m","expected_recovery_pct","money_good_probability"],
    )

    write_csv(
        csv_scenarios_path,
        [
            {
                "scenario": sc["label"],
                "enterprise_value_m": f"{sc['enterprise_value']:.4f}",
                "layer": r["layer"],
                "claim_m": f"{r['claim']:.4f}",
                "recovered_m": f"{r['recovered']:.4f}",
                "recovery_pct": f"{r['recovery_pct']:.6f}",
            }
            for sc in report["scenarios"]
            for r in sc["waterfall"]
        ],
        ["scenario","enterprise_value_m","layer","claim_m","recovered_m","recovery_pct"],
    )

    write_csv(
        csv_implied_path,
        [
            {
                "scenario": r["scenario"],
                "ebitda_m": f"{r['ebitda']:.4f}",
                "ev_net_m": f"{r['ev_net']:.4f}",
                "recovery_pct": f"{r['recovery_pct']:.6f}",
                "recovery_cents": f"{r['recovery_cents']:.4f}",
                "implied_fair_value_cents": f"{r['implied_fair_value']:.4f}",
                "market_price_cents": f"{r['market_price']:.4f}",
                "gap_cents": f"{r['gap_cents']:.4f}",
                "implied_gross_return_pct": f"{r['implied_gross_return_pct']:.4f}",
            }
            for r in report["implied_price"]["scenarios"]
        ],
        ["scenario","ebitda_m","ev_net_m","recovery_pct","recovery_cents",
         "implied_fair_value_cents","market_price_cents","gap_cents","implied_gross_return_pct"],
    )

    if report["pik_accretion"]:
        write_csv(
            csv_pik_path,
            [
                {
                    "instrument": r["instrument"],
                    "layer": r["layer"],
                    "original_balance_m": f"{r['original_balance']:.4f}",
                    "pik_rate_pct": f"{r['pik_rate_pct']:.4f}",
                    "months": r["months"],
                    "accreted_balance_m": f"{r['accreted_balance']:.4f}",
                    "additional_debt_m": f"{r['additional_debt']:.4f}",
                }
                for r in report["pik_accretion"]
            ],
            ["instrument","layer","original_balance_m","pik_rate_pct","months","accreted_balance_m","additional_debt_m"],
        )

    html_path.write_text(render_report(report), encoding="utf-8")

    print(f"Wrote {{json_path}}")
    print(f"Wrote {{csv_layers_path}}")
    print(f"Wrote {{csv_instruments_path}}")
    print(f"Wrote {{csv_scenarios_path}}")
    print(f"Wrote {{csv_implied_path}}")
    if report["pik_accretion"]:
        print(f"Wrote {{csv_pik_path}}")
    print(f"Wrote {{html_path}}")


if __name__ == "__main__":
    main()
