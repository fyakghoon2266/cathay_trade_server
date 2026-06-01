"""End-to-end smoke test: simulate three execs (Alice = seller, Bob/Carol = buyers).

Identity is by display_name; everyone shares the same entry-ticket token.
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "http://127.0.0.1:8787/mcp/"
TOKEN = "arena-2026"


async def call(session: ClientSession, name: str, **kwargs) -> dict:
    res = await session.call_tool(name, {"args": kwargs} if kwargs else {})
    if res.isError:
        raise RuntimeError(f"{name} failed: {res.content}")
    text = res.content[0].text  # type: ignore[attr-defined]
    return json.loads(text)


def auth(name: str) -> dict:
    return {"api_key": TOKEN, "display_name": name}


async def main() -> int:
    fake_files = {
        "SKILL.md": "---\nname: risk-frameworks\ndescription: 壓力測試\n---\n\n# 風險評估\n",
        "examples/case1.md": "客戶 X 在 2008 倒閉的故事...",
    }

    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as alice:
            await alice.initialize()
            r = await call(alice, "register_agent", **auth("Alice"), persona_blurb="風險控管達人")
            print("alice register:", r)
            assert r["created"] is True
            r = await call(alice, "publish_skill",
                **auth("Alice"),
                skill_id="risk-frameworks",
                title="風險評估框架",
                description="壓力測試 + 情境分析心法",
                detail="# 風險評估\n適用於投資組合健診...",
                files=fake_files,
                list_price=40,
                tags=["風控", "壓力測試"],
            )
            print("alice publish:", r)
            assert r["list_price"] == 40
            published_id = r["skill_id"]

    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as bob:
            await bob.initialize()
            r = await call(bob, "register_agent",
                           **auth("Bob"), persona_blurb="想學風險的 PM",
                           interests_tags=["風控", "投資"], wants_blurb="我想學壓力測試")
            print("bob register:", r)
            assert r["created"] is True
            listing = await call(bob, "list_skills", **auth("Bob"))
            assert listing["skills"], "Bob should see Alice's listing"
            assert listing["skills"][0]["list_price"] == 40

            matches = await call(bob, "find_matches", **auth("Bob"))
            print("bob find_matches:", matches)
            assert matches["matches"]
            top = matches["matches"][0]
            assert top["skill_id"] == published_id
            print("  → suggested bid:", top["suggested_bid"])

            offer = await call(bob, "propose_trade", **auth("Bob"), skill_id=published_id, price=30, note="這個我很需要")
            assert offer["status"] == "pending" and not offer["auto_accepted"]
            offer_id = offer["offer_id"]

    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as alice:
            await alice.initialize()
            inbox = await call(alice, "check_inbox", **auth("Alice"))
            assert inbox["items"] and inbox["items"][0]["offer_id"] == offer_id
            await call(alice, "respond_to_offer", **auth("Alice"), offer_id=offer_id, action="counter", counter_price=45, note="再加一點")

    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as bob:
            await bob.initialize()
            inbox = await call(bob, "check_inbox", **auth("Bob"))
            assert inbox["items"][0]["status"] == "countered"
            await call(bob, "respond_to_offer", **auth("Bob"), offer_id=offer_id, action="counter", counter_price=45, note="OK")

    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as alice:
            await alice.initialize()
            await call(alice, "check_inbox", **auth("Alice"))
            await call(alice, "respond_to_offer", **auth("Alice"), offer_id=offer_id, action="accept")

    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as bob:
            await bob.initialize()
            inbox = await call(bob, "check_inbox", **auth("Bob"))
            assert inbox["items"][0]["status"] == "accepted"
            done = await call(bob, "purchase_skill", **auth("Bob"), offer_id=offer_id)
            assert "SKILL.md" in done["files"]
            assert done["remaining_budget"] == 55
            status = await call(bob, "my_status", **auth("Bob"))
            assert status["registered"] and status["budget"] == 55 and status["purchases_count"] == 1

    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as alice:
            await alice.initialize()
            status = await call(alice, "my_status", **auth("Alice"))
            assert status["budget"] == 100 and status["revenue"] == 45

    # Auto-buy at list price (no inbox needed)
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as carol:
            await carol.initialize()
            await call(carol, "register_agent", **auth("Carol"), persona_blurb="新手 PM",
                       interests_tags=["風控"], wants_blurb="想找壓力測試")
            r = await call(carol, "propose_trade", **auth("Carol"), skill_id=published_id, price=42, note="直接買")
            assert r["auto_accepted"] and r["status"] == "completed"
            assert r["price_paid"] == 42 and r["remaining_budget"] == 58

    # Same display_name returning → updates not creates
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as alice:
            await alice.initialize()
            r = await call(alice, "register_agent", **auth("Alice"), persona_blurb="新版人設")
            assert r["created"] is False, "second register with same name must NOT recreate"
            assert r["revenue"] == 87, "wallet must be preserved across re-registers"

    # my_status of unregistered name returns registered:false (not error)
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as anon:
            await anon.initialize()
            r = await call(anon, "my_status", **auth("Nobody"))
            assert r["registered"] is False

    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
