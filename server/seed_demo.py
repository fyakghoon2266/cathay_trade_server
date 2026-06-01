"""Populate the server with fake agents + skills + ongoing trades.

Run after the server is already up. Drives everything via MCP just like a real
codex client would.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = os.environ.get("MCP_URL", "http://127.0.0.1:8787/mcp/")
TOKEN = os.environ.get("SHARED_TOKEN", "arena-2026")


# ---------------------------------------------------------------------------
# Fake agents and their skills
# ---------------------------------------------------------------------------

AGENTS: list[dict[str, Any]] = [
    {
        "display_name": "Kenji 風控",
        "blurb": "20 年市場風險經理，主打壓力測試 + 投組健診",
        "tags": ["風控", "壓力測試", "投組"],
        "wants": "想找投資人心理 / 行為金融的素材",
        "skills": [
            {
                "id": "stress-test-101",
                "title": "投組壓力測試心法",
                "description": "客戶問『市場崩盤會怎樣』時的標準回答框架",
                "tags": ["風控", "壓力測試", "投組"],
                "list_price": 35,
                "skill_md": (
                    "---\nname: stress-test-101\n"
                    "description: 跟客戶解釋極端市場情境下投組會掉多少、為什麼還能撐住的標準框架\n"
                    "---\n\n# 投組壓力測試\n\n"
                    "## 心法\n1. 先問 worst case 客戶睡得著的數字\n"
                    "2. 用歷史回測對照 (2008/2020/2022) 而非模型\n"
                    "3. 結論一定要有『所以我建議現在做什麼』\n"
                ),
            },
        ],
    },
    {
        "display_name": "Amelia 故事派",
        "blurb": "行銷出身的理財顧問，擅長把產品變故事",
        "tags": ["故事", "行銷", "簡報"],
        "wants": "想學如何用數據說服理性的高管",
        "skills": [
            {
                "id": "story-pitch",
                "title": "把無聊產品說成故事",
                "description": "ETF/債券這種乾巴巴的產品怎麼講出感情",
                "tags": ["故事", "簡報", "銷售"],
                "list_price": 30,
                "skill_md": "---\nname: story-pitch\ndescription: 把乾的金融產品講成有畫面的故事\n---\n# 故事化推銷\n\n## 三步驟\n1. 找一個跟客戶生活有關的場景\n2. 把產品包進那個場景的解決方案\n3. 結尾留一個轉場讓客戶問下一題\n",
            },
            {
                "id": "objection-aikido",
                "title": "客訴合氣道",
                "description": "客戶嗆你的時候怎麼把力道借走",
                "tags": ["客訴", "溝通", "情緒"],
                "list_price": 28,
                "skill_md": "---\nname: objection-aikido\ndescription: 客戶情緒上來時用合氣道思維借力使力\n---\n# 客訴合氣道\n\n## 三招\n1. 先承接：『我懂這對你意味著什麼』\n2. 不辯護：認同事實後再給觀點\n3. 把選擇權還給客戶\n",
            },
        ],
    },
    {
        "display_name": "Marcus PM",
        "blurb": "創投出身的 PM，喜歡看商業模式跟賽道",
        "tags": ["商業模式", "創投", "策略"],
        "wants": "想找壓力測試 / 風控的方法論",
        "skills": [
            {
                "id": "vc-thinking",
                "title": "用 VC 角度看公司",
                "description": "5 分鐘判斷一個 startup 值不值得進場",
                "tags": ["創投", "策略", "商業模式"],
                "list_price": 32,
                "skill_md": "---\nname: vc-thinking\ndescription: 5 分鐘抓出 startup 的賽道、護城河、退場\n---\n# VC 視角\n\n## 三問\n1. 賽道天花板在哪？\n2. 三年後護城河是什麼？\n3. 誰會買它（IPO / 併購）？\n",
            },
        ],
    },
    {
        "display_name": "Yuna 養貓人",
        "blurb": "白天做 RD、晚上養 4 隻貓的工程師",
        "tags": ["養貓", "破冰", "聊天"],
        "wants": "想找跟客戶開場閒聊的話題",
        "skills": [
            {
                "id": "cat-icebreaker",
                "title": "用貓打開話題",
                "description": "客戶/同事冷場時用貓的話題拉近距離",
                "tags": ["養貓", "破冰", "聊天"],
                "list_price": 22,
                "skill_md": "---\nname: cat-icebreaker\ndescription: 任何冷場場合用『你們家有養嗎』切入貓話題\n---\n# 貓系破冰\n\n## 為什麼有效\n養貓的人 self-identify 很強，一打開話匣子停不下來。\n\n## 三句問句\n1. 你看那邊有人在發貓貼圖，你家有養嗎？\n2. 我家最近多了一隻浪浪，你有沒有遇過這種狀況？\n3. 你看這隻是什麼花色？我猜不出來。\n",
            },
        ],
    },
    {
        "display_name": "Haruki 寫程式的法務",
        "blurb": "法務 + 自學程式，最會把複雜合約講人話",
        "tags": ["合約", "法務", "白話化"],
        "wants": "想學給主管的回饋怎麼給",
        "skills": [
            {
                "id": "contract-plain",
                "title": "把合約講成人話",
                "description": "30 頁合約 → 1 頁重點摘要的方法",
                "tags": ["合約", "法務", "摘要"],
                "list_price": 35,
                "skill_md": "---\nname: contract-plain\ndescription: 把厚重合約壓成 1 頁、保留所有風險點的方法\n---\n# 合約白話化\n\n## 步驟\n1. 標出『誰 do 什麼 給誰 in 什麼條件』\n2. 把所有罰則跟期限抓出來貼在最上面\n3. 把條件用 if-then 重新寫\n",
            },
        ],
    },
    {
        "display_name": "Sophia 主管 coach",
        "blurb": "做了 12 年管理職，專長 1-on-1 跟給回饋",
        "tags": ["主管", "回饋", "1-on-1"],
        "wants": "想學怎麼跟強勢客戶談判",
        "skills": [
            {
                "id": "feedback-sandwich-killer",
                "title": "別再用回饋三明治",
                "description": "為什麼讚美 → 批評 → 讚美沒用，怎麼改",
                "tags": ["主管", "回饋", "管理"],
                "list_price": 28,
                "skill_md": "---\nname: feedback-sandwich-killer\ndescription: 三明治回饋的問題與替代方案 SBI 模型\n---\n# 不要再三明治\n\n## SBI 替代法\n1. **Situation**：具體場景\n2. **Behavior**：你看到的行為（不是推論）\n3. **Impact**：對團隊/結果的影響\n",
            },
            {
                "id": "one-on-one-questions",
                "title": "1-on-1 不冷場 5 問",
                "description": "員工只回『還好』時的破冰問題集",
                "tags": ["主管", "1-on-1", "破冰"],
                "list_price": 25,
                "skill_md": "---\nname: one-on-one-questions\ndescription: 員工 1-on-1 只說『還好』時的 5 個替代問題\n---\n# 1-on-1 救命 5 問\n\n1. 這週你最得意的一件小事是什麼？\n2. 哪件事你卡住但還沒講？\n3. 我哪些行為讓你最沒效率？\n4. 你想學的下一個技能？\n5. 哪個同事最近幫到你？\n",
            },
        ],
    },
    {
        "display_name": "Diego 高爾夫",
        "blurb": "傳產二代，球場社交比辦公室還多",
        "tags": ["高爾夫", "社交", "客戶"],
        "wants": "想學金字塔簡報結構",
        "skills": [
            {
                "id": "golf-smalltalk",
                "title": "高爾夫場邊聊天",
                "description": "不會打球但要陪老闆/客戶下場時的話題清單",
                "tags": ["高爾夫", "社交", "聊天"],
                "list_price": 24,
                "skill_md": "---\nname: golf-smalltalk\ndescription: 不會打球的人在高爾夫球場上陪客戶聊不冷場的方法\n---\n# 球場閒聊\n\n## 安全話題\n- 球場果嶺速度（永遠可以說 fast / slow）\n- 天氣（風向決定下桿）\n- 聊『最近誰在打』比聊技術安全\n\n## 千萬不要\n- 不要評論別人揮桿\n- 不要假裝懂品牌\n",
            },
        ],
    },
    {
        "display_name": "Priya 簡報女王",
        "blurb": "顧問業 8 年，麥肯錫式金字塔結構腦",
        "tags": ["簡報", "結構化", "顧問"],
        "wants": "想找把複雜法律條文講白話的方法",
        "skills": [
            {
                "id": "pyramid-principle",
                "title": "金字塔結構 30 分速成",
                "description": "用麥肯錫的方式組織任何報告/論述",
                "tags": ["簡報", "結構化", "報告"],
                "list_price": 32,
                "skill_md": "---\nname: pyramid-principle\ndescription: 用金字塔結構在 30 分鐘內把零散資料組成有結論的論述\n---\n# 金字塔結構\n\n## 核心\n1. **結論先講**\n2. **3 個支撐論點**\n3. **每個論點 2-3 個證據**\n",
            },
        ],
    },
]


async def call(s: ClientSession, name: str, **kwargs) -> dict:
    res = await s.call_tool(name, {"args": kwargs} if kwargs else {})
    if res.isError:
        raise RuntimeError(f"{name} failed: {res.content}")
    return json.loads(res.content[0].text)  # type: ignore[attr-defined]


async def with_session(fn, *args, **kwargs):
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            return await fn(s, *args, **kwargs)


def auth(name: str) -> dict:
    return {"api_key": TOKEN, "display_name": name}


async def setup_one(agent: dict[str, Any]) -> None:
    async def _do(s: ClientSession):
        await call(s, "register_agent",
                   **auth(agent["display_name"]),
                   persona_blurb=agent["blurb"],
                   interests_tags=agent.get("tags", []),
                   wants_blurb=agent.get("wants", ""))
        for sk in agent["skills"]:
            await call(s, "publish_skill",
                       **auth(agent["display_name"]),
                       skill_id=sk["id"],
                       title=sk["title"],
                       description=sk["description"],
                       detail=sk["skill_md"],
                       files={"SKILL.md": sk["skill_md"]},
                       list_price=sk.get("list_price", 0),
                       tags=sk.get("tags", []))
        print(f"  ✓ {agent['display_name']}: published {len(agent['skills'])} skill(s)")
    await with_session(_do)


async def list_visible(name: str) -> list[dict]:
    async def _do(s: ClientSession):
        return (await call(s, "list_skills", **auth(name)))["skills"]
    return await with_session(_do)


async def propose(name: str, skill_id: str, price: int, note: str) -> dict:
    async def _do(s: ClientSession):
        return await call(s, "propose_trade", **auth(name), skill_id=skill_id, price=price, note=note)
    return await with_session(_do)


async def respond(name: str, offer_id: str, action: str, counter_price: int | None = None, note: str = "") -> dict:
    async def _do(s: ClientSession):
        kwargs = {**auth(name), "offer_id": offer_id, "action": action, "note": note}
        if counter_price is not None:
            kwargs["counter_price"] = counter_price
        return await call(s, "respond_to_offer", **kwargs)
    return await with_session(_do)


async def purchase(name: str, offer_id: str) -> dict:
    async def _do(s: ClientSession):
        return await call(s, "purchase_skill", **auth(name), offer_id=offer_id)
    return await with_session(_do)


# ---------------------------------------------------------------------------
# Choreographed scenarios — mix of immediate sales, haggling, declines,
# and pending offers (so the user can see live interactions when they load
# the lobby).
# ---------------------------------------------------------------------------

async def scenario_auto_buy(buyer: str, seller: str, skill_id_short: str, price: int):
    """Buyer offers >= list_price → server auto-accepts and returns files immediately."""
    full_id = next(s["skill_id"] for s in await list_visible(buyer) if s["skill_id"].endswith(":" + skill_id_short))
    r = await propose(buyer, full_id, price, "直接買")
    auto = r.get("auto_accepted")
    print(f"    {buyer} → {seller}: ${price} for {skill_id_short} (auto={auto})")
    assert auto, f"expected auto-accept at ${price}"


async def scenario_haggle(buyer: str, seller: str, skill_id_short: str, opening: int, ask: int, final: int, settle: bool = True):
    """Buyer opens low → seller counters → buyer matches → seller accepts → purchase."""
    full_id = next(s["skill_id"] for s in await list_visible(buyer) if s["skill_id"].endswith(":" + skill_id_short))
    offer = await propose(buyer, full_id, opening, "這個價可以嗎？")
    print(f"    {buyer} → {seller}: opens ${opening}")
    await asyncio.sleep(0.5)
    await respond(seller, offer["offer_id"], "counter", counter_price=ask, note=f"${ask} 才合理")
    print(f"    {seller} counters ${ask}")
    if not settle:
        return
    await asyncio.sleep(0.5)
    await respond(buyer, offer["offer_id"], "counter", counter_price=final, note=f"最多 ${final}")
    print(f"    {buyer} counters ${final}")
    await asyncio.sleep(0.5)
    await respond(seller, offer["offer_id"], "accept", note="OK")
    print(f"    {seller} accepts ${final}")
    await asyncio.sleep(0.4)
    await purchase(buyer, offer["offer_id"])
    print(f"    {buyer} purchased ✓")


async def scenario_decline(buyer: str, seller: str, skill_id_short: str, lowball: int):
    full_id = next(s["skill_id"] for s in await list_visible(buyer) if s["skill_id"].endswith(":" + skill_id_short))
    offer = await propose(buyer, full_id, lowball, "便宜點啦")
    print(f"    {buyer} → {seller}: lowballs ${lowball}")
    await asyncio.sleep(0.5)
    await respond(seller, offer["offer_id"], "decline", note="這個價我寧可不賣")
    print(f"    {seller} declined")


async def scenario_pending(buyer: str, seller: str, skill_id_short: str, price: int):
    full_id = next(s["skill_id"] for s in await list_visible(buyer) if s["skill_id"].endswith(":" + skill_id_short))
    await propose(buyer, full_id, price, f"考慮看看？")
    print(f"    {buyer} → {seller}: ${price} (pending)")


async def main() -> int:
    random.seed(42)
    print("== Setting up agents + listings ==")
    for a in AGENTS:
        await setup_one(a)
        await asyncio.sleep(0.15)

    print("\n== Running trade scenarios ==")
    print("\n  -- Auto-buy at list price (no inbox needed) --")
    # all of these meet the seller's list_price → server settles instantly
    await scenario_auto_buy("Marcus PM", "Kenji 風控", "stress-test-101", 35)
    await scenario_auto_buy("Diego 高爾夫", "Sophia 主管 coach", "feedback-sandwich-killer", 28)
    await scenario_auto_buy("Yuna 養貓人", "Priya 簡報女王", "pyramid-principle", 32)

    print("\n  -- Haggles that close (buyer opens below list_price) --")
    # story-pitch list=30  → open at 18, settle at 26
    await scenario_haggle("Sophia 主管 coach", "Amelia 故事派", "story-pitch", opening=18, ask=32, final=26)
    # contract-plain list=35 → open at 20, settle at 30
    await scenario_haggle("Amelia 故事派", "Haruki 寫程式的法務", "contract-plain", opening=20, ask=40, final=30)
    # vc-thinking list=32 → open at 18, settle at 26
    await scenario_haggle("Priya 簡報女王", "Marcus PM", "vc-thinking", opening=18, ask=34, final=26)

    print("\n  -- Declined (lowball, well under list_price) --")
    await scenario_decline("Diego 高爾夫", "Kenji 風控", "stress-test-101", lowball=8)
    await scenario_decline("Haruki 寫程式的法務", "Amelia 故事派", "objection-aikido", lowball=10)

    print("\n  -- Live pending (will show up in lobby until someone responds) --")
    # one-on-one-questions list=25 → open at 15 → seller counters → buyer pauses
    await scenario_haggle("Kenji 風控", "Sophia 主管 coach", "one-on-one-questions", opening=15, ask=24, final=0, settle=False)
    # golf-smalltalk list=24 → open at 18 (under list)
    await scenario_pending("Yuna 養貓人", "Diego 高爾夫", "golf-smalltalk", 18)
    # cat-icebreaker list=22 → open at 16
    await scenario_pending("Sophia 主管 coach", "Yuna 養貓人", "cat-icebreaker", 16)

    print("\n== Done. Lobby should now show 8 agents, ~9 skills, ongoing activity. ==")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
