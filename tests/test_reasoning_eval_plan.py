"""The B2 dry-run spend planner makes no calls and reports worst-case bounds."""

from __future__ import annotations

from scripts import reasoning_eval_plan as planner


def test_plan_covers_both_runs_and_every_configuration() -> None:
    report = planner.plan(4096)
    calibration, pilot = report["runs"]["calibration"], report["runs"]["pilot"]
    assert calibration["cases"] == 20 and calibration["repeats"] == 1
    assert pilot["cases"] == 40 and pilot["repeats"] == 3
    for run in (calibration, pilot):
        labels = [row["configuration"] for row in run["configurations"]]
        assert labels == [c.label for c in planner.CONFIGURATIONS]
        assert run["worst_case_total_usd"] > 0


def test_bound_grows_with_the_output_limit() -> None:
    small = planner.plan(1024)["runs"]["pilot"]["worst_case_total_usd"]
    large = planner.plan(4096)["runs"]["pilot"]["worst_case_total_usd"]
    assert large > small


def test_premium_configurations_dominate_the_bound() -> None:
    rows = {
        row["configuration"]: row["worst_case_usd"]
        for row in planner.plan(4096)["runs"]["pilot"]["configurations"]
    }
    assert rows["sol-6.1-high"] > rows["sonnet-5-high"] > rows["luna-low"]
