import asyncio

import pytest

from src.main import _cancel_background_tasks


@pytest.mark.asyncio
async def test_cancel_background_tasks_skips_none() -> None:
    async def never_finishes() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(never_finishes())

    await _cancel_background_tasks(None, task, None)

    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancel_background_tasks_handles_completed_task() -> None:
    async def finishes_immediately() -> str:
        return "done"

    task = asyncio.create_task(finishes_immediately())
    await task

    await _cancel_background_tasks(task)

    assert task.done()
    assert task.result() == "done"
