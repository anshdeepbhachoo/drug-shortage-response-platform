"""Workflow runner logic. No database (engine=None skips the run record)."""

from __future__ import annotations

import pytest

from ingestion.workflow import Task, order_tasks, run_workflow


def ok(stats=None):
    return lambda engine, params: stats or {"rows": 1}


def boom(message="down"):
    def run(engine, params):
        raise RuntimeError(message)
    return run


def run(tasks, **kwargs):
    sleeps = []
    results = run_workflow("test", tasks, engine=None, sleep=sleeps.append, log=lambda _: None, **kwargs)
    return {r.name: r for r in results}, sleeps


def test_tasks_run_in_dependency_order():
    calls = []
    track = lambda name: (lambda engine, params: calls.append(name) or {})
    tasks = [Task("c", track("c"), depends_on=("b",)), Task("a", track("a")), Task("b", track("b"), depends_on=("a",))]

    results, _ = run(tasks)

    assert calls == ["a", "b", "c"]
    assert all(r.state == "succeeded" for r in results.values())


def test_failed_task_is_retried_with_backoff_then_downstream_skipped():
    tasks = [Task("shortages", boom(), retries=2, retry_delay=10), Task("dpd", ok(), depends_on=("shortages",))]

    results, sleeps = run(tasks)

    assert results["shortages"].state == "failed"
    assert results["shortages"].attempts == 3
    assert "RuntimeError: down" in results["shortages"].error
    assert sleeps == [10, 20]
    assert results["dpd"].state == "skipped"
    assert results["dpd"].attempts == 0


def test_task_that_recovers_on_retry_succeeds():
    attempts = []

    def flaky(engine, params):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("blip")
        return {"rows": 5}

    results, sleeps = run([Task("t", flaky, retries=1, retry_delay=1)])

    assert results["t"].state == "succeeded"
    assert results["t"].attempts == 2
    assert results["t"].stats == {"rows": 5}
    assert sleeps == [1]


def test_independent_task_still_runs_when_another_fails():
    results, _ = run([Task("a", boom(), retries=0), Task("b", ok())])
    assert results["a"].state == "failed"
    assert results["b"].state == "succeeded"


def test_only_reruns_selected_task():
    results, _ = run([Task("shortages", boom(), retries=0), Task("dpd", ok(), depends_on=("shortages",))], only={"dpd"})
    assert list(results) == ["dpd"]
    assert results["dpd"].state == "succeeded"


def test_params_reach_tasks():
    seen = {}
    run([Task("t", lambda engine, params: seen.update(params) or {})], params={"batch_size": 7})
    assert seen == {"batch_size": 7}


@pytest.mark.parametrize("tasks, message", [
    ([Task("a", ok(), depends_on=("b",)), Task("b", ok(), depends_on=("a",))], "cycle"),
    ([Task("a", ok(), depends_on=("missing",))], "unknown"),
    ([Task("a", ok()), Task("a", ok())], "unique"),
])
def test_bad_workflows_are_rejected(tasks, message):
    with pytest.raises(ValueError, match=message):
        order_tasks(tasks)


def test_unknown_only_task_is_rejected():
    with pytest.raises(ValueError, match="Unknown task"):
        run([Task("a", ok())], only={"nope"})
