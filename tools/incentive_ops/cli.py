"""cli.py — entry for `python -m tools.incentive_ops <command>`

All commands use typed objects. Structured via logger; human output via click.echo.
"""

from __future__ import annotations

import sys
from typing import Any

import click

from src.utils.logger import configure_logger, get_logger

from .accounting import load_ledger, realized_report, validate_caps
from .actionability import main_actionability
from .capture import capture_all
from .classify import check_classification
from .deadlines import main_deadlines
from .eligibility import fetch_eligibility_str
from .ev import base_and_upside, compute_ev
from .registry import load_registry, validate_registry
from .types import (
    EVScenarioInputs,
    PilotCaps,
    RewardType,
    SuspectedSecretError,
)

logger = get_logger(__name__)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--log-level", default="INFO", help="Logging level")
@click.pass_context
def cli(ctx: click.Context, log_level: str) -> None:
    """A1 Phase-0 incentive-ops tooling (read-only research, NO capital/keys)."""
    configure_logger(log_level)
    ctx.ensure_object(dict)


@cli.command("validate")
@click.option("--path", default="research/a1-incentive-farming/starter-registry-v0.yaml")
def cmd_validate(path: str) -> None:
    """Validate registry (hard errors fail; warnings expected for PENDING/UNVERIFIED)."""
    try:
        warns = validate_registry(path)
        click.echo(f"Registry at {path} loaded OK ({len(warns)} warnings)")
        for w in warns[:20]:
            click.echo(f"  WARN: {w}")
        if len(warns) > 20:
            click.echo(f"  ... +{len(warns) - 20} more")
        sys.exit(0)
    except Exception as e:
        click.echo(f"VALIDATE FAIL: {e}", err=True)
        sys.exit(1)


@cli.command("capture")
@click.option("--id", "only_id", default=None, help="Capture only this program id")
@click.option("--force", is_flag=True, help="Re-fetch even if sidecar exists")
def cmd_capture(only_id: str | None, force: bool) -> None:
    """Fetch allowlisted official_source_url, compute sha over raw, write sidecar."""
    records, _ = load_registry(warn=False)
    caps = capture_all(records, force=force, only_id=only_id)
    click.echo(f"Captured {len(caps)} program(s)")
    for c in caps:
        click.echo(f"  {c.id}: sha={c.snapshot_sha256[:12]}... captured_at={c.captured_at}")


@cli.command("classify")
@click.option(
    "--check", is_flag=True, help="Reproduce recorded labels and exit non-zero on mismatch"
)
@click.option("--path", default=None)
def cmd_classify(check: bool, path: str | None) -> None:
    """Derive classification tiers (independent of actionability)."""
    if check:
        ok, results, mismatches, counts = check_classification()
        click.echo("=== classify --check ===")
        click.echo(f"Total records: {len(results)}")
        click.echo(f"Derived counts: {counts}")
        for r in results:
            click.echo(f"  {r.recorded_label} <- {r.rule_fired}  [match={r.matches_recorded}]")
        if not ok:
            click.echo("MISMATCHES:")
            for m in mismatches:
                click.echo(f"  {m}")
        sys.exit(0 if ok else 1)
    # otherwise just show for the fixture
    records, _ = load_registry(
        path or "research/a1-incentive-farming/starter-registry-v0.yaml", warn=False
    )
    from .classify import classify_all  # local

    res = classify_all(records)
    for r in res:
        click.echo(
            f"{r.recorded_label} {r.derived_label} {r.rule_fired} match={r.matches_recorded}"
        )
    sys.exit(0 if all(r.matches_recorded for r in res) else 1)


@cli.command("actionability")
@click.option(
    "--ledger",
    type=click.Path(exists=False),
    default=None,
    help="Path to ledger YAML/JSON for caps checks",
)
def cmd_actionability(ledger: str | None) -> None:
    """Run the SEPARATE actionability gate (default-deny). Expect all non-ACTIONABLE on starter."""
    led = load_ledger(ledger) if ledger else None
    summary = main_actionability(ledger=led)
    for pid, status in summary.items():
        click.echo(f"{pid}: {status}")
    non = all("ACTIONABLE" not in s for s in summary.values())
    click.echo(f"\nAll non-ACTIONABLE: {non}")
    sys.exit(0 if non else 2)


@cli.command("ev")
@click.option("--scenario", type=click.Choice(["base", "upside"]), default="base")
@click.option("--p-eligibility", type=float, default=0.8)
@click.option("--p-distribution", type=float, default=0.7)
@click.option("--reward-qty", type=float, default=100.0)
@click.option("--realizable-price", type=float, default=0.5)
@click.option("--haircut", type=float, default=0.6)
@click.option("--base-yield", type=float, default=5.0)
@click.option("--gas", type=float, default=3.0)
@click.option("--capital", type=float, default=250.0)
@click.option("--days", type=float, default=14.0)
@click.option("--benchmark-apy", type=float, default=0.05)
@click.option("--loss-reserve", type=float, default=10.0)
@click.option("--hours", type=float, default=3.0)
@click.option("--hourly-rate", type=float, default=50.0)
@click.option("--announced/--unannounced", "announced", default=False)
@click.option(
    "--reward-type", type=click.Choice([e.value for e in RewardType]), default="speculative_points"
)
def cmd_ev(scenario: str, **kwargs: Any) -> None:
    """Compute EV with typed inputs. Base forces unannounced speculative_points -> 0."""
    try:
        inp = EVScenarioInputs(
            p_eligibility=kwargs["p_eligibility"],
            p_distribution=kwargs["p_distribution"],
            reward_qty=kwargs["reward_qty"],
            realizable_price=kwargs["realizable_price"],
            liquidity_vesting_haircut=kwargs["haircut"],
            base_yield=kwargs["base_yield"],
            gas_bridge_fees=kwargs["gas"],
            capital=kwargs["capital"],
            days=kwargs["days"],
            benchmark_apy=kwargs["benchmark_apy"],
            expected_loss_reserve=kwargs["loss_reserve"],
            manual_hours=kwargs["hours"],
            hourly_rate=kwargs["hourly_rate"],
            reward_announced=kwargs["announced"],
        )
        rt = RewardType(kwargs["reward_type"])
        res = compute_ev(inp, reward_type=rt)
        if scenario == "upside":
            up_res = base_and_upside(inp, reward_type=rt)["upside"]
            res = up_res
        click.echo(f"scenario={scenario} reward_type={rt}")
        for k, v in res.items():
            click.echo(f"  {k}: {v:.6f}")
        # Golden-ish note for test: base unannounced speculative -> 0 spec part
    except Exception as e:
        click.echo(f"EV error: {e}", err=True)
        sys.exit(1)


@cli.command("deadlines")
@click.option("--within-days", type=int, default=7)
def cmd_deadlines(within_days: int) -> None:
    """Offline deadline alerts from registry (review_expiry etc)."""
    alerts = main_deadlines(within_days=within_days)
    if not alerts:
        click.echo("No upcoming/expired deadlines in window.")
    for a in alerts:
        click.echo(f"{a['program_id']}: {a['kind']} {a['date']} {a['status']} ({a['days']}d)")
    sys.exit(0)


@cli.command("eligibility")
@click.option(
    "--address", required=True, help="Public wallet address (EVM 0x..); rejects keys/seeds"
)
@click.option("--program", default="layer3-quests")
def cmd_eligibility(address: str, program: str) -> None:
    """Read-only lookup (address only; allowlisted endpoints)."""
    try:
        snap = fetch_eligibility_str(program, address)
        click.echo(f"program={snap.program_id} addr={snap.address}")
        click.echo(f"  eligible={snap.eligible} points={snap.points_or_allocation}")
        click.echo(f"  source={snap.source}")
    except SuspectedSecretError:
        click.echo("REJECTED: address input looks like secret (key/seed)", err=True)
        sys.exit(3)
    except Exception as e:
        click.echo(f"eligibility error: {e}", err=True)
        sys.exit(1)


@cli.command("report")
@click.option("--ledger", type=click.Path(exists=False), default=None)
def cmd_report(ledger: str | None) -> None:
    """Accounting report using real validated ledger parser (no capital movement)."""
    led = load_ledger(ledger) if ledger else []
    rpt = realized_report(led)
    click.echo(f"report: {rpt}")
    if ledger:
        click.echo(f"  loaded from {ledger}")


@cli.command("caps-check")
@click.option("--total", type=float, default=1200.0)
@click.option("--per", type=float, default=100.0)
@click.option("--conc", type=int, default=4)
def cmd_caps_check(total: float, per: float, conc: int) -> None:
    """Demo that validate_caps blocks breaches (unit logic). No operational 100 placeholder."""
    caps = PilotCaps()
    bad = {"id": "demo-breach", "usd": 300.0}
    # build a ledger that will breach
    ledger = [{"id": "p1", "usd": 900.0}, {"id": "p2", "usd": 200.0}]
    ck = validate_caps(ledger, bad, caps)
    click.echo(f"breach_demo ok={ck.ok} reason={ck.reason}")
    sys.exit(0 if not ck.ok else 1)


if __name__ == "__main__":
    cli()
