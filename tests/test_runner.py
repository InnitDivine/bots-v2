import asyncio

import pytest

from runner import run_bot_supervisor


@pytest.mark.asyncio
async def test_supervisor_recreates_bot_after_failure():
    stop_event = asyncio.Event()
    created = []

    class FakeBot:
        def __init__(self, should_fail: bool):
            self.should_fail = should_fail
            self.closed = False

        async def start(self):
            if self.should_fail:
                raise RuntimeError("boom")
            stop_event.set()

        async def close(self):
            self.closed = True

    def factory():
        bot = FakeBot(should_fail=not created)
        created.append(bot)
        return bot

    async def no_sleep(_delay):
        return None

    await run_bot_supervisor(stop_event, factory, sleep_fn=no_sleep)

    assert len(created) == 2
    assert all(bot.closed for bot in created)
