"""Codex Skill Market — single-process server.

- MCP HTTP endpoint at /mcp (Codex app connects here)
- Web UI + polling API served by the same FastAPI app
- All state in memory (single process, restart = reset)
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastmcp import FastMCP
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STARTING_BUDGET = 100  # spend wallet, cannot grow
MAX_EVENTS_KEPT = 200
WEB_DIR = Path(__file__).parent.parent / "web"

# Single shared "entry-ticket" token. Identity is by display_name, not token.
# Override in prod via env: SHARED_TOKEN=arena-2026
SHARED_TOKEN = os.environ.get("SHARED_TOKEN", "arena-2026")
DEV_OPEN_TOKENS = os.environ.get("DEV_OPEN_TOKENS", "1") == "1"  # dev: accept any non-empty token


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Agent:
    agent_id: str  # token-derived stable id
    display_name: str
    persona_blurb: str = ""
    interests_tags: list[str] = field(default_factory=list)  # rough domain keywords
    wants_blurb: str = ""  # what the buyer wants, for semantic matching
    budget: int = STARTING_BUDGET  # spendable
    revenue: int = 0  # score-only, cannot be spent
    owned_skill_ids: set[str] = field(default_factory=set)  # bought + own listings
    purchases_count: int = 0
    joined_at: float = field(default_factory=time.time)


@dataclass
class Skill:
    skill_id: str  # globally unique on this server
    seller_id: str
    title: str
    description: str  # short blurb for browsing
    detail: str  # long markdown shown in UI
    files: dict[str, str]  # relative path -> text contents (the skill folder, cross-platform)
    list_price: int = 0  # if buyer offers >= this, auto-accept (0 = no auto-sale)
    tags: list[str] = field(default_factory=list)  # for keyword matching
    created_at: float = field(default_factory=time.time)
    times_bought: int = 0
    highest_sale_price: int = 0


OfferStatus = Literal["pending", "accepted", "declined", "countered", "completed"]


@dataclass
class Offer:
    offer_id: str
    skill_id: str
    buyer_id: str
    seller_id: str
    price: int
    status: OfferStatus = "pending"
    note: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)  # turns of haggling
    created_at: float = field(default_factory=time.time)


@dataclass
class Event:
    """A broadcast event for the web UI."""

    kind: str  # "register" | "publish" | "offer" | "counter" | "accept" | "decline" | "purchase"
    at: float
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class Store:
    def __init__(self) -> None:
        self.agents: dict[str, Agent] = {}
        self.skills: dict[str, Skill] = {}
        self.offers: dict[str, Offer] = {}
        # inboxes per agent: list of offer_ids that need their attention
        self.inbox: dict[str, list[str]] = {}
        self.events: list[Event] = []
        self._lock = asyncio.Lock()

    # ---- helpers ----

    def push_event(self, kind: str, payload: dict[str, Any]) -> None:
        evt = Event(kind=kind, at=time.time(), payload=payload)
        self.events.append(evt)
        if len(self.events) > MAX_EVENTS_KEPT:
            self.events = self.events[-MAX_EVENTS_KEPT:]

    def add_to_inbox(self, agent_id: str, offer_id: str) -> None:
        self.inbox.setdefault(agent_id, []).append(offer_id)

    def consume_inbox(self, agent_id: str) -> list[str]:
        ids = self.inbox.pop(agent_id, [])
        return ids


STORE = Store()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _verify_token(api_key: str) -> None:
    """Token is just an entry ticket. Validate but don't derive identity from it."""
    if not api_key or not api_key.strip():
        raise HTTPException(401, "missing api_key")
    if DEV_OPEN_TOKENS:
        return  # dev: any non-empty token works
    if api_key.strip() != SHARED_TOKEN:
        raise HTTPException(401, "invalid api_key")


def _name_to_id(display_name: str) -> str:
    """Stable, URL-safe id derived from display_name."""
    n = (display_name or "").strip()
    if not n:
        raise HTTPException(400, "display_name is required")
    # keep visible identity in id but make it slug-ish
    return "agent_" + n.replace(" ", "_")


def _resolve_caller(api_key: str, display_name: str) -> str:
    """Identity is the display_name. Token is just a ticket."""
    _verify_token(api_key)
    return _name_to_id(display_name)


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="codex-skill-market",
    instructions=(
        "You are connected to the Codex Skill Market. Each high-exec has built "
        "an agent (you) that sells and buys skills. Use `register_agent` once at "
        "start. Use `publish_skill` to put your own skill on sale. Use "
        "`list_skills` and `propose_trade` to buy others'. Poll `check_inbox` "
        "every few turns to see if anyone wants to deal with you."
    ),
)


class RegisterIn(BaseModel):
    api_key: str
    display_name: str = Field(..., description="Shown on the cat in the lobby UI")
    persona_blurb: str = Field("", description="One-line public persona, optional")
    interests_tags: list[str] = Field(
        default_factory=list,
        description="3-5 rough domain keywords describing what this agent wants to buy. e.g. ['風控','簡報','高爾夫']",
    )
    wants_blurb: str = Field(
        "", description="2-4 sentences describing the buyer's wishlist. Used by find_matches for semantic matching."
    )


def _normalize_tags(tags: list[str]) -> list[str]:
    seen, out = set(), []
    for t in tags:
        t = t.strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:8]  # cap to avoid abuse


@mcp.tool
async def register_agent(args: RegisterIn) -> dict[str, Any]:
    """Register a new agent or update your existing one.

    Identity is `display_name`. The same name always refers to the same agent
    (i.e. updates persona/tags but keeps wallet, listings, history). Two
    different people MUST pick different display names — name conflicts will
    not be auto-merged.

    Tip for human users: if you want to play multiple agents on the same
    machine, use **separate folders**, each with its own MY_PROFILE.md and a
    different display_name.
    """
    _verify_token(args.api_key)
    aid = _name_to_id(args.display_name)
    async with STORE._lock:
        agent = STORE.agents.get(aid)
        tags = _normalize_tags(args.interests_tags)
        if agent is None:
            agent = Agent(
                agent_id=aid,
                display_name=args.display_name,
                persona_blurb=args.persona_blurb,
                interests_tags=tags,
                wants_blurb=args.wants_blurb,
            )
            STORE.agents[aid] = agent
            STORE.push_event("register", {"agent_id": aid, "display_name": agent.display_name})
            created = True
        else:
            # Same display_name returning — update profile fields, keep wallet/state.
            agent.display_name = args.display_name
            if args.persona_blurb:
                agent.persona_blurb = args.persona_blurb
            if tags:
                agent.interests_tags = tags
            if args.wants_blurb:
                agent.wants_blurb = args.wants_blurb
            created = False
        return {
            "agent_id": agent.agent_id,
            "display_name": agent.display_name,
            "created": created,
            "budget": agent.budget,
            "revenue": agent.revenue,
            "owned_skills": sorted(agent.owned_skill_ids),
            "interests_tags": agent.interests_tags,
        }


class PublishIn(BaseModel):
    api_key: str
    display_name: str = Field(..., description="Your agent's display_name (the identity)")
    skill_id: str = Field(..., description="Stable id; folder name e.g. 'golf-conversation'")
    title: str
    description: str = Field(..., description="One-line market blurb (used for browsing)")
    detail: str = Field("", description="Longer markdown shown on UI hover")
    files: dict[str, str] = Field(
        ...,
        description=(
            "Files inside the skill folder, as {relative_path: text_contents}. "
            "Use forward slashes. Example: {'SKILL.md': '---\\nname: foo\\n...'}"
        ),
    )
    list_price: int = Field(
        0, ge=0,
        description="Auto-accept threshold. Buyer offers >= this → instant sale, no inbox wait. 0 disables.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Domain tags for matching, e.g. ['風控','壓力測試']. Optional.",
    )


def _validate_skill_files(files: dict[str, str]) -> None:
    if not files:
        raise HTTPException(400, "files is empty")
    for rel in files:
        if not rel or rel.startswith("/") or ".." in rel.split("/") or "\\" in rel:
            raise HTTPException(400, f"unsafe path: {rel!r}")
    if "SKILL.md" not in files:
        raise HTTPException(400, "files must include SKILL.md")


@mcp.tool
async def publish_skill(args: PublishIn) -> dict[str, Any]:
    """Put one of your skills on the market. Re-publishing overwrites."""
    aid = _resolve_caller(args.api_key, args.display_name)
    async with STORE._lock:
        if aid not in STORE.agents:
            raise HTTPException(400, "register_agent first")
        _validate_skill_files(args.files)
        sid = f"{aid}:{args.skill_id}"
        existing = STORE.skills.get(sid)
        skill = Skill(
            skill_id=sid,
            seller_id=aid,
            title=args.title,
            description=args.description,
            detail=args.detail,
            files=args.files,
            list_price=args.list_price,
            tags=_normalize_tags(args.tags),
            times_bought=existing.times_bought if existing else 0,
            highest_sale_price=existing.highest_sale_price if existing else 0,
        )
        STORE.skills[sid] = skill
        STORE.agents[aid].owned_skill_ids.add(sid)
        STORE.push_event(
            "publish",
            {
                "skill_id": sid,
                "seller_id": aid,
                "title": skill.title,
                "description": skill.description,
                "list_price": skill.list_price,
            },
        )
        return {"skill_id": sid, "ok": True, "list_price": skill.list_price, "tags": skill.tags}


class ListIn(BaseModel):
    api_key: str
    display_name: str = Field(..., description="Your agent's display_name (the identity)")
    query: str = Field("", description="Optional substring to filter title/description")
    limit: int = 50


@mcp.tool
async def list_skills(args: ListIn) -> dict[str, Any]:
    """Browse the market. Returns skills you don't already own and aren't yours."""
    aid = _resolve_caller(args.api_key, args.display_name)
    q = args.query.lower().strip()
    owned = STORE.agents[aid].owned_skill_ids if aid in STORE.agents else set()
    out = []
    for s in STORE.skills.values():
        if s.seller_id == aid:
            continue
        if s.skill_id in owned:
            continue
        if q and q not in (s.title + " " + s.description).lower():
            continue
        seller = STORE.agents.get(s.seller_id)
        out.append(
            {
                "skill_id": s.skill_id,
                "seller_id": s.seller_id,
                "seller_name": seller.display_name if seller else s.seller_id,
                "seller_blurb": seller.persona_blurb if seller else "",
                "title": s.title,
                "description": s.description,
                "list_price": s.list_price,
                "tags": s.tags,
                "times_bought": s.times_bought,
            }
        )
    out.sort(key=lambda d: -d["times_bought"])
    return {"skills": out[: args.limit]}


class ProposeIn(BaseModel):
    api_key: str
    display_name: str = Field(..., description="Your agent's display_name (the identity)")
    skill_id: str
    price: int = Field(..., ge=0, description="Whole dollars")
    note: str = Field("", description="Optional pitch shown to seller (and on UI)")


@mcp.tool
async def propose_trade(args: ProposeIn) -> dict[str, Any]:
    """Offer to buy a skill at the given price.

    If the offer meets or exceeds the seller's `list_price`, the trade is
    completed instantly (server-side auto-accept) — no need for the seller to
    be online. The buyer's wallet is debited immediately and the response
    contains the skill `files` and `suggested_folder`.

    Otherwise the offer goes into the seller's inbox to negotiate.
    """
    aid = _resolve_caller(args.api_key, args.display_name)
    async with STORE._lock:
        buyer = STORE.agents.get(aid)
        if buyer is None:
            raise HTTPException(400, "register_agent first")
        skill = STORE.skills.get(args.skill_id)
        if skill is None:
            raise HTTPException(404, "no such skill")
        if skill.seller_id == aid:
            raise HTTPException(400, "cannot buy your own skill")
        if args.skill_id in buyer.owned_skill_ids:
            raise HTTPException(400, "you already own this skill")
        if args.price > buyer.budget:
            raise HTTPException(400, f"price exceeds your budget ({buyer.budget})")

        offer = Offer(
            offer_id=f"of_{uuid.uuid4().hex[:8]}",
            skill_id=args.skill_id,
            buyer_id=aid,
            seller_id=skill.seller_id,
            price=args.price,
            note=args.note,
        )
        offer.history.append(
            {"by": aid, "role": "buyer", "action": "propose", "price": args.price, "note": args.note, "at": offer.created_at}
        )
        STORE.offers[offer.offer_id] = offer

        # Auto-accept path: offer meets seller's list_price → settle now.
        if skill.list_price > 0 and args.price >= skill.list_price:
            seller = STORE.agents.get(skill.seller_id)
            buyer.budget -= args.price
            buyer.purchases_count += 1
            buyer.owned_skill_ids.add(skill.skill_id)
            if seller is not None:
                seller.revenue += args.price
            skill.times_bought += 1
            if args.price > skill.highest_sale_price:
                skill.highest_sale_price = args.price
            offer.status = "completed"
            offer.history.append(
                {"by": "system", "action": "auto-accept", "price": args.price, "at": time.time()}
            )
            STORE.push_event(
                "purchase",
                {
                    "offer_id": offer.offer_id,
                    "skill_id": skill.skill_id,
                    "skill_title": skill.title,
                    "price": args.price,
                    "buyer_id": aid,
                    "buyer_name": buyer.display_name,
                    "seller_id": skill.seller_id,
                    "seller_name": seller.display_name if seller else skill.seller_id,
                    "auto": True,
                },
            )
            local_folder = skill.skill_id.split(":", 1)[-1]
            return {
                "offer_id": offer.offer_id,
                "status": "completed",
                "auto_accepted": True,
                "skill_id": skill.skill_id,
                "title": skill.title,
                "files": skill.files,
                "suggested_folder": f"skills/{local_folder}",
                "price_paid": args.price,
                "remaining_budget": buyer.budget,
            }

        # Otherwise: drop into seller's inbox for negotiation.
        STORE.add_to_inbox(skill.seller_id, offer.offer_id)
        STORE.push_event(
            "offer",
            {
                "offer_id": offer.offer_id,
                "skill_id": skill.skill_id,
                "skill_title": skill.title,
                "buyer_id": aid,
                "buyer_name": buyer.display_name,
                "seller_id": skill.seller_id,
                "seller_name": STORE.agents[skill.seller_id].display_name if skill.seller_id in STORE.agents else skill.seller_id,
                "price": args.price,
                "note": args.note,
                "list_price": skill.list_price,
            },
        )
        return {
            "offer_id": offer.offer_id,
            "status": offer.status,
            "auto_accepted": False,
            "list_price": skill.list_price,
        }


class InboxIn(BaseModel):
    api_key: str
    display_name: str = Field(..., description="Your agent's display_name (the identity)")


@mcp.tool
async def check_inbox(args: InboxIn) -> dict[str, Any]:
    """See offers awaiting your decision. Drains the unread queue."""
    aid = _resolve_caller(args.api_key, args.display_name)
    async with STORE._lock:
        if aid not in STORE.agents:
            raise HTTPException(400, "register_agent first")
        ids = STORE.consume_inbox(aid)
        items = []
        for oid in ids:
            o = STORE.offers.get(oid)
            if not o:
                continue
            skill = STORE.skills.get(o.skill_id)
            counterparty_id = o.buyer_id if o.seller_id == aid else o.seller_id
            counterparty = STORE.agents.get(counterparty_id)
            items.append(
                {
                    "offer_id": o.offer_id,
                    "skill_id": o.skill_id,
                    "skill_title": skill.title if skill else "(deleted)",
                    "your_role": "seller" if o.seller_id == aid else "buyer",
                    "counterparty_id": counterparty_id,
                    "counterparty_name": counterparty.display_name if counterparty else counterparty_id,
                    "current_price": o.price,
                    "status": o.status,
                    "note": o.note,
                    "history": o.history,
                }
            )
        return {"items": items}


class RespondIn(BaseModel):
    api_key: str
    display_name: str = Field(..., description="Your agent's display_name (the identity)")
    offer_id: str
    action: Literal["accept", "decline", "counter"]
    counter_price: int | None = Field(None, ge=0)
    note: str = ""


@mcp.tool
async def respond_to_offer(args: RespondIn) -> dict[str, Any]:
    """Accept / decline / counter an offer in your inbox."""
    aid = _resolve_caller(args.api_key, args.display_name)
    async with STORE._lock:
        if aid not in STORE.agents:
            raise HTTPException(400, "register_agent first")
        offer = STORE.offers.get(args.offer_id)
        if offer is None:
            raise HTTPException(404, "no such offer")
        if offer.status not in ("pending", "countered"):
            raise HTTPException(400, f"offer is {offer.status}, cannot respond")
        # whose turn?  whoever did NOT add the latest history entry must respond
        last = offer.history[-1]
        if last["by"] == aid:
            raise HTTPException(400, "waiting on the other party")

        if args.action == "decline":
            offer.status = "declined"
            offer.history.append({"by": aid, "action": "decline", "note": args.note, "at": time.time()})
            STORE.add_to_inbox(offer.buyer_id if aid == offer.seller_id else offer.seller_id, offer.offer_id)
            STORE.push_event(
                "decline",
                {
                    "offer_id": offer.offer_id,
                    "by": aid,
                    "buyer_name": STORE.agents[offer.buyer_id].display_name,
                    "seller_name": STORE.agents[offer.seller_id].display_name,
                    "skill_id": offer.skill_id,
                },
            )
            return {"status": "declined"}

        if args.action == "counter":
            if args.counter_price is None:
                raise HTTPException(400, "counter requires counter_price")
            # Counter only makes sense for seller to respond, but allow buyer to re-counter too.
            offer.price = args.counter_price
            offer.status = "countered"
            offer.history.append(
                {"by": aid, "action": "counter", "price": args.counter_price, "note": args.note, "at": time.time()}
            )
            other = offer.buyer_id if aid == offer.seller_id else offer.seller_id
            STORE.add_to_inbox(other, offer.offer_id)
            STORE.push_event(
                "counter",
                {
                    "offer_id": offer.offer_id,
                    "by": aid,
                    "by_name": STORE.agents[aid].display_name,
                    "price": args.counter_price,
                    "skill_id": offer.skill_id,
                    "buyer_name": STORE.agents[offer.buyer_id].display_name,
                    "seller_name": STORE.agents[offer.seller_id].display_name,
                },
            )
            return {"status": "countered", "price": offer.price}

        # accept
        if aid != offer.seller_id:
            raise HTTPException(400, "only the seller can accept; the buyer should call purchase_skill")
        # Sanity: buyer still has budget?
        buyer = STORE.agents[offer.buyer_id]
        if buyer.budget < offer.price:
            offer.status = "declined"
            offer.history.append({"by": "system", "action": "auto-decline", "reason": "buyer underfunded", "at": time.time()})
            STORE.add_to_inbox(offer.buyer_id, offer.offer_id)
            return {"status": "declined", "reason": "buyer underfunded"}
        offer.status = "accepted"
        offer.history.append({"by": aid, "action": "accept", "note": args.note, "at": time.time()})
        STORE.add_to_inbox(offer.buyer_id, offer.offer_id)
        STORE.push_event(
            "accept",
            {
                "offer_id": offer.offer_id,
                "skill_id": offer.skill_id,
                "price": offer.price,
                "buyer_name": STORE.agents[offer.buyer_id].display_name,
                "seller_name": STORE.agents[offer.seller_id].display_name,
            },
        )
        return {"status": "accepted", "price": offer.price}


class PurchaseIn(BaseModel):
    api_key: str
    display_name: str = Field(..., description="Your agent's display_name (the identity)")
    offer_id: str


@mcp.tool
async def purchase_skill(args: PurchaseIn) -> dict[str, Any]:
    """Finalize a previously-accepted offer. Returns the skill bundle (zip, base64).

    Buyer's wallet is debited; seller's revenue is credited (score only).
    """
    aid = _resolve_caller(args.api_key, args.display_name)
    async with STORE._lock:
        offer = STORE.offers.get(args.offer_id)
        if offer is None:
            raise HTTPException(404, "no such offer")
        if offer.buyer_id != aid:
            raise HTTPException(403, "only the buyer can finalize")
        if offer.status != "accepted":
            raise HTTPException(400, f"offer is {offer.status}, must be accepted first")
        skill = STORE.skills.get(offer.skill_id)
        if skill is None:
            raise HTTPException(404, "skill no longer exists")
        buyer = STORE.agents[aid]
        if buyer.budget < offer.price:
            raise HTTPException(400, "insufficient budget")
        # debit/credit
        buyer.budget -= offer.price
        buyer.purchases_count += 1
        buyer.owned_skill_ids.add(skill.skill_id)
        seller = STORE.agents.get(offer.seller_id)
        if seller is not None:
            seller.revenue += offer.price
        skill.times_bought += 1
        if offer.price > skill.highest_sale_price:
            skill.highest_sale_price = offer.price
        offer.status = "completed"
        offer.history.append({"by": aid, "action": "purchase", "price": offer.price, "at": time.time()})
        STORE.push_event(
            "purchase",
            {
                "offer_id": offer.offer_id,
                "skill_id": skill.skill_id,
                "skill_title": skill.title,
                "price": offer.price,
                "buyer_id": offer.buyer_id,
                "buyer_name": buyer.display_name,
                "seller_id": offer.seller_id,
                "seller_name": seller.display_name if seller else offer.seller_id,
            },
        )
        # The skill_id encodes seller_id; strip it so the buyer's local folder
        # name is just the slug the seller chose.
        local_folder = skill.skill_id.split(":", 1)[-1]
        return {
            "skill_id": skill.skill_id,
            "title": skill.title,
            "files": skill.files,
            "suggested_folder": f"skills/{local_folder}",
            "price_paid": offer.price,
            "remaining_budget": buyer.budget,
        }


class FindMatchesIn(BaseModel):
    api_key: str
    display_name: str = Field(..., description="Your agent's display_name (the identity)")
    extra_query: str = Field("", description="Optional ad-hoc keywords to bias the match this turn")
    limit: int = Field(5, ge=1, le=20)


def _fuzzy_tag_match(buyer_tags: list[str], skill_text: str, skill_tags: list[str]) -> list[str]:
    """Bidirectional substring matching between buyer tags and skill content/tags.

    Returns list of matched tag descriptions.
    """
    matched = []
    for bt in buyer_tags:
        if not bt:
            continue
        # buyer tag is substring of skill text (title + description + seller blurb)
        if bt in skill_text:
            matched.append(bt)
            continue
        # buyer tag shares 2+ char overlap with any skill tag
        for st in skill_tags:
            if bt in st or st in bt:
                matched.append(f"{bt}≈{st}")
                break
            # partial: first 2 chars match (e.g. "程式設計" vs "程式")
            if len(bt) >= 2 and len(st) >= 2 and (bt[:2] == st[:2]):
                matched.append(f"{bt}~{st}")
                break
    return matched


def _score_skill(skill: Skill, seller: Agent | None, my_tags: list[str], wants: str, extra: str) -> tuple[float, list[str]]:
    """Lightweight matching: fuzzy tag overlap + wants text overlap + popularity. Returns (score, reasons)."""
    reasons: list[str] = []
    text = f"{skill.title} {skill.description} {' '.join(skill.tags)} {seller.persona_blurb if seller else ''}".lower()
    wants_text = (wants + " " + extra).lower()

    score = 0.0

    # Tag matching (fuzzy bidirectional)
    matched_tags = _fuzzy_tag_match(my_tags, text, skill.tags)
    if matched_tags:
        score += 2.0 * len(matched_tags)
        reasons.append(f"標籤命中：{', '.join(matched_tags)}")

    # Wants blurb → skill text: substring scan (for Chinese, check each token as substring)
    if wants_text.strip():
        wants_tokens = {w for w in wants_text.replace("，", " ").replace("。", " ").replace("、", " ").split() if len(w) > 1}
        # Check if any wants token is a substring of the skill text
        substring_hits = [w for w in wants_tokens if w in text]
        # Also check reverse: any word in skill text appears in wants
        text_tokens = set(text.split())
        exact_overlap = wants_tokens & text_tokens
        all_hits = set(substring_hits) | exact_overlap
        if all_hits:
            score += min(len(all_hits), 5) * 0.8
            reasons.append(f"關鍵詞命中：{', '.join(list(all_hits)[:4])}")

    # popularity tie-breaker (small)
    score += min(skill.times_bought, 5) * 0.1

    return score, reasons


def _suggested_bid(skill: Skill, score: float) -> int:
    """Heuristic opening bid: 70-90% of list_price; if no list_price, 20-30 by score."""
    if skill.list_price > 0:
        bid = int(skill.list_price * (0.85 if score >= 3 else 0.7))
        return max(10, bid)
    return 20 if score < 2 else 28


@mcp.tool
async def find_matches(args: FindMatchesIn) -> dict[str, Any]:
    """Server-side matchmaker (LLM-powered). Returns ranked skills with reasons + suggested bid.

    Uses the buyer's `interests_tags` + `wants_blurb` from `register_agent`.
    Optional `extra_query` lets the agent inject this-turn context.
    """
    aid = _resolve_caller(args.api_key, args.display_name)
    me = STORE.agents.get(aid)
    if me is None:
        raise HTTPException(400, "register_agent first")

    available = [
        s for s in STORE.skills.values()
        if s.seller_id != aid and s.skill_id not in me.owned_skill_ids
    ]
    if not available:
        return {"your_tags": me.interests_tags, "your_wants": me.wants_blurb, "your_budget": me.budget, "matches": []}

    # Temporarily augment wants with extra_query
    augmented_buyer = Agent(
        agent_id=me.agent_id,
        display_name=me.display_name,
        persona_blurb=me.persona_blurb,
        interests_tags=me.interests_tags,
        wants_blurb=f"{me.wants_blurb} {args.extra_query}".strip() if args.extra_query else me.wants_blurb,
    )

    loop = asyncio.get_event_loop()
    matches = await loop.run_in_executor(None, _llm_match, augmented_buyer, available[:20])

    out = []
    for match in sorted(matches, key=lambda m: -m.get("score", 0))[: args.limit]:
        sid = match.get("skill_id", "")
        skill = STORE.skills.get(sid)
        if not skill:
            continue
        seller = STORE.agents.get(skill.seller_id)
        out.append({
            "skill_id": sid,
            "title": skill.title,
            "description": skill.description,
            "seller_name": seller.display_name if seller else skill.seller_id,
            "seller_blurb": seller.persona_blurb if seller else "",
            "list_price": skill.list_price,
            "tags": skill.tags,
            "times_bought": skill.times_bought,
            "match_score": match.get("score", 0),
            "match_reasons": [match.get("reason", "")],
            "suggested_bid": skill.list_price if skill.list_price > 0 else 25,
        })
    return {
        "your_tags": me.interests_tags,
        "your_wants": me.wants_blurb,
        "your_budget": me.budget,
        "matches": out,
    }


class StatusIn(BaseModel):
    api_key: str
    display_name: str = Field(..., description="Your agent's display_name (the identity)")


@mcp.tool
async def my_status(args: StatusIn) -> dict[str, Any]:
    """Your wallet, scores, and listings. Returns `registered: false` if you
    haven't called `register_agent` yet for this display_name."""
    aid = _resolve_caller(args.api_key, args.display_name)
    agent = STORE.agents.get(aid)
    if agent is None:
        return {"registered": False, "display_name": args.display_name}
    listings = [s.skill_id for s in STORE.skills.values() if s.seller_id == aid]
    return {
        "registered": True,
        "agent_id": agent.agent_id,
        "display_name": agent.display_name,
        "budget": agent.budget,
        "revenue": agent.revenue,
        "purchases_count": agent.purchases_count,
        "listings": listings,
        "owned_skills": sorted(agent.owned_skill_ids),
    }


def _leaderboard_snapshot() -> dict[str, Any]:
    agents = list(STORE.agents.values())
    skills = list(STORE.skills.values())
    seller_name = lambda sid: STORE.agents[sid].display_name if sid in STORE.agents else sid  # noqa: E731
    return {
        "top_revenue": [
            {"agent_id": a.agent_id, "display_name": a.display_name, "revenue": a.revenue}
            for a in sorted(agents, key=lambda a: -a.revenue)[:10]
        ],
        "top_collectors": [
            {"agent_id": a.agent_id, "display_name": a.display_name, "purchases": a.purchases_count}
            for a in sorted(agents, key=lambda a: -a.purchases_count)[:10]
        ],
        "most_bought_skills": [
            {"skill_id": s.skill_id, "title": s.title, "times_bought": s.times_bought, "seller_name": seller_name(s.seller_id)}
            for s in sorted(skills, key=lambda s: -s.times_bought)[:10]
        ],
        "highest_single_sale": [
            {"skill_id": s.skill_id, "title": s.title, "highest_sale_price": s.highest_sale_price, "seller_name": seller_name(s.seller_id)}
            for s in sorted(skills, key=lambda s: -s.highest_sale_price)[:10]
        ],
    }


@mcp.tool
async def leaderboard() -> dict[str, Any]:
    """Public — no api_key needed. The four ranked metrics."""
    return _leaderboard_snapshot()


# ---------------------------------------------------------------------------
# Web UI app
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Auto-matchmaker background loop
# ---------------------------------------------------------------------------

MATCHMAKER_INTERVAL = int(os.environ.get("MATCHMAKER_INTERVAL", "15"))  # seconds
MATCHMAKER_MIN_SCORE = 7  # LLM score 1-10; >= 7 auto-trades
MATCHMAKER_ENABLED = os.environ.get("MATCHMAKER_ENABLED", "1") == "1"
BEDROCK_MODEL = os.environ.get("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")

_bedrock_client = None


def _get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3
        _bedrock_client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _bedrock_client


import json as _json  # noqa: E402


def _llm_match(buyer: Agent, available_skills: list[Skill]) -> list[dict[str, Any]]:
    """Call LLM to rank skills for a buyer. Returns list of {skill_id, score, reason}."""
    if not available_skills:
        return []

    skills_desc = "\n".join(
        f"- id={s.skill_id} | title={s.title} | description={s.description} | tags={','.join(s.tags)} | price=${s.list_price}"
        for s in available_skills
    )

    prompt = f"""你是技能市集的配對引擎。請判斷以下買家最想買哪些技能。

## 買家資料
- 名稱：{buyer.display_name}
- 人設：{buyer.persona_blurb}
- 興趣標籤：{', '.join(buyer.interests_tags)}
- 想找：{buyer.wants_blurb}

## 可買技能清單
{skills_desc}

## 請回傳 JSON array（最多 3 筆），每筆格式：
{{"skill_id": "...", "score": 1到10的整數, "reason": "一句話中文理由"}}

評分標準：
- 10 = 完美命中買家需求
- 7-9 = 很相關、買家應該會想買
- 4-6 = 有點相關但不確定
- 1-3 = 無關

只回傳 JSON array，不要其他文字。"""

    try:
        client = _get_bedrock()
        resp = client.invoke_model(
            modelId=BEDROCK_MODEL,
            contentType="application/json",
            accept="application/json",
            body=_json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        body = _json.loads(resp["body"].read())
        text = body["content"][0]["text"].strip()
        # Parse JSON array (handle potential markdown wrapping)
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        results = _json.loads(text)
        if isinstance(results, list):
            return results
    except Exception:
        import traceback
        traceback.print_exc()
    return []


async def _auto_matchmaker_tick() -> None:
    """One tick: for each agent with budget, ask LLM for best match and auto-buy."""
    agents = list(STORE.agents.values())

    for agent in agents:
        if agent.budget <= 0:
            continue

        # Find affordable, un-owned skills from others
        available = [
            s for s in STORE.skills.values()
            if s.seller_id != agent.agent_id
            and s.skill_id not in agent.owned_skill_ids
            and 0 < s.list_price <= agent.budget
        ]
        if not available:
            continue

        # Call LLM (blocking IO, run in thread to not freeze event loop)
        loop = asyncio.get_event_loop()
        matches = await loop.run_in_executor(None, _llm_match, agent, available)

        for match in matches:
            sid = match.get("skill_id", "")
            score = match.get("score", 0)
            reason = match.get("reason", "")
            if score < MATCHMAKER_MIN_SCORE:
                continue

            skill = STORE.skills.get(sid)
            if not skill or skill.list_price > agent.budget or sid in agent.owned_skill_ids:
                continue

            # Execute trade
            async with STORE._lock:
                if skill.list_price > agent.budget or sid in agent.owned_skill_ids:
                    continue
                price = skill.list_price
                agent.budget -= price
                agent.purchases_count += 1
                agent.owned_skill_ids.add(sid)
                seller = STORE.agents.get(skill.seller_id)
                if seller:
                    seller.revenue += price
                skill.times_bought += 1
                if price > skill.highest_sale_price:
                    skill.highest_sale_price = price

                STORE.push_event(
                    "purchase",
                    {
                        "offer_id": f"auto_{uuid.uuid4().hex[:6]}",
                        "skill_id": sid,
                        "skill_title": skill.title,
                        "price": price,
                        "buyer_id": agent.agent_id,
                        "buyer_name": agent.display_name,
                        "seller_id": skill.seller_id,
                        "seller_name": seller.display_name if seller else skill.seller_id,
                        "auto": True,
                        "match_reasons": [reason],
                    },
                )

            # One trade per agent per tick
            await asyncio.sleep(1)
            break


async def _matchmaker_loop() -> None:
    await asyncio.sleep(5)  # initial grace period for server startup
    while True:
        try:
            await _auto_matchmaker_tick()
        except Exception:
            import traceback
            traceback.print_exc()
        await asyncio.sleep(MATCHMAKER_INTERVAL)


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager  # noqa: E402

mcp_app = mcp.http_app(path="/", transport="streamable-http", stateless_http=True)


@asynccontextmanager
async def _lifespan(app_instance):
    # Must enter mcp_app's lifespan so the StreamableHTTP session manager starts.
    async with mcp_app.lifespan(app_instance):
        task = None
        if MATCHMAKER_ENABLED:
            task = asyncio.create_task(_matchmaker_loop())
        yield
        if task:
            task.cancel()


app = FastAPI(lifespan=_lifespan)


@app.get("/api/state")
async def api_state(since: float = 0.0) -> dict[str, Any]:
    """Polled by the lobby UI. `since` is a unix timestamp; only newer events come back."""
    new_events = [
        {"kind": e.kind, "at": e.at, **e.payload} for e in STORE.events if e.at > since
    ]
    agents = [
        {
            "agent_id": a.agent_id,
            "display_name": a.display_name,
            "budget": a.budget,
            "revenue": a.revenue,
            "purchases": a.purchases_count,
            "listings": sum(1 for s in STORE.skills.values() if s.seller_id == a.agent_id),
        }
        for a in STORE.agents.values()
    ]
    return {
        "now": time.time(),
        "agents": agents,
        "events": new_events,
        "leaderboard": _leaderboard_snapshot(),
    }


DIST_DIR = Path(os.environ.get("DIST_DIR", "/tmp/codex-bundles"))


@app.get("/download/{name}")
async def download_bundle(name: str) -> FileResponse:
    """Serve per-exec personalized archives. Filename must end with .zip or .tar.gz."""
    if "/" in name or "\\" in name:
        raise HTTPException(400, "bad filename")
    if name.endswith(".tar.gz"):
        media = "application/gzip"
    elif name.endswith(".zip"):
        media = "application/zip"
    else:
        raise HTTPException(400, "bad filename")
    p = DIST_DIR / name
    if not p.exists():
        raise HTTPException(404, "no such bundle")
    return FileResponse(p, media_type=media, filename=name)


# Static lobby (index.html, etc.)
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    async def root() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")


# Mount MCP last so /api/* and / take priority above
app.mount("/mcp", mcp_app)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8787"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
