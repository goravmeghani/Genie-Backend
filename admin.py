from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from db_utils import get_db_connection
from agent import checkpointer

PlanType = Literal["free", "premium"]
RoleType = Literal["user", "admin"]

router = APIRouter(prefix="/admin", tags=["admin"])
pricing_router = APIRouter(tags=["pricing"])


class UserProfileOut(BaseModel):
    id: str
    email: str
    plan: PlanType
    role: RoleType
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None


class PlanUpdateRequest(BaseModel):
    plan: PlanType


class MetricsResponse(BaseModel):
    total_users: int
    premium_users: int
    free_users: int
    admin_users: int
    # monthly_active_users: int
    total_threads: int
    # total_messages: int


class PricingPlanOut(BaseModel):
    id: str
    name: str
    stripe_price_id: Optional[str] = None
    monthly_price_label: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PricingPlanUpdate(BaseModel):
    monthly_price_label: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    is_active: Optional[bool] = Field(default=None)
    stripe_price_id: Optional[str] = Field(default=None)
    
    def has_updates(self) -> bool:
        return any(
            value is not None
            for value in (
                self.monthly_price_label,
                self.description,
                self.is_active,
                self.stripe_price_id,
            )
        )


class PublicPricingPlan(BaseModel):
    name: str
    monthly_price_label: Optional[str] = None
    description: Optional[str] = None


def _normalize_role(raw: Optional[str]) -> RoleType:
    if (raw or "").lower() == "admin":
        return "admin"
    return "user"


def require_admin_user_id(
    admin_user_id: Optional[str] = Query(
        default=None,
        description="Admin user ID (UUID). Can also be provided via x-admin-user-id header.",
    ),
    x_admin_user_id: Optional[str] = Header(
        default=None,
        alias="x-admin-user-id",
        convert_underscores=False,
    ),
) -> str:
    candidate = admin_user_id or x_admin_user_id
    if not candidate:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="admin_user_id or x-admin-user-id header is required.",
        )

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select role from public.user_profiles where id = %s",
                (candidate,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    role = _normalize_role(row[0])
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return candidate

# def _compute_thread_and_message_stats() -> tuple[int, int]:
#     """
#     Walk all LangGraph checkpoints and estimate:
#     - total_threads: distinct thread_id values
#     - total_messages: human + AI messages in the latest checkpoint per thread
#     """
#     thread_latest_messages: dict[str, list] = {}

#     # Each checkpoint corresponds to a particular (thread_id, user_id, etc.) state snapshot.
#     for cp in checkpointer.list(None):
#         cfg = (cp.config or {}).get("configurable", {}) or {}
#         tid = cfg.get("thread_id")
#         if not tid:
#             continue
#         tid = str(tid)

#         # LangGraph PostgresSaver stores state in cp.checkpoint["values"]
#         cp_data = cp.checkpoint or {}
#         values = cp_data.get("values", {}) or {}
#         msgs = values.get("messages", []) or []

#         # Keep the most recent snapshot for each thread_id
#         thread_latest_messages[tid] = msgs

#     total_threads = len(thread_latest_messages)

#     total_messages = 0
#     for msgs in thread_latest_messages.values():
#         for m in msgs:
#             # Case 1: real LangChain message objects
#             if isinstance(m, (HumanMessage, AIMessage)):
#                 total_messages += 1
#             # Case 2: serialized dicts from the DB
#             elif isinstance(m, dict) and m.get("type") in ("human", "ai"):
#                 total_messages += 1

#     return total_threads, total_messages

def _compute_thread_and_message_stats() -> tuple[int, int]:
    """
    Walk all LangGraph checkpoints and estimate:
    - total_threads: distinct thread_id values
    - total_messages: human + AI messages in the latest checkpoint per thread
    """
    thread_latest_messages: dict[str, list] = {}

    # Each checkpoint corresponds to a particular (thread_id, user_id, etc.) state snapshot.
    for cp in checkpointer.list(None):
        cfg = (cp.config or {}).get("configurable", {}) or {}
        tid = cfg.get("thread_id")
        if not tid:
            continue
        tid = str(tid)

        # LangGraph PostgresSaver stores state in cp.checkpoint["values"]
        cp_data = cp.checkpoint or {}
        values = cp_data.get("values", {}) or {}
        msgs = values.get("messages", []) or []

        # Keep the most recent snapshot for each thread_id
        thread_latest_messages[tid] = msgs

    total_threads = len(thread_latest_messages)

    return total_threads

def _row_to_user_profile(row: tuple) -> UserProfileOut:
    return UserProfileOut(
        id=str(row[0]),
        email=row[1] or "",
        plan="premium" if row[2] == "premium" else "free",
        role=_normalize_role(row[3]),
        created_at=row[4],
        last_login_at=row[5],
    )



@router.get("/users", response_model=list[UserProfileOut])
def list_users(_: str = Depends(require_admin_user_id)):
    query = """
        select id, coalesce(email, ''), coalesce(plan, 'free'), coalesce(role, 'user'),
               created_at, last_login_at
        from public.user_profiles
        order by created_at desc nulls last
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall() or []

    return [_row_to_user_profile(row) for row in rows]


@router.patch("/users/{user_id}/plan", response_model=UserProfileOut)
def update_user_plan(
    user_id: str,
    body: PlanUpdateRequest,
    _: str = Depends(require_admin_user_id),
):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update public.user_profiles
                set plan = %s,
                    updated_at = now()
                where id = %s
                returning id, coalesce(email, ''), coalesce(plan, 'free'),
                          coalesce(role, 'user'), created_at, last_login_at
                """,
                (body.plan, user_id),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return _row_to_user_profile(row)


@router.get("/metrics", response_model=MetricsResponse)
def get_admin_metrics(_: str = Depends(require_admin_user_id)):
    aggregates_query = """
        select
            count(*) as total_users,
            count(*) filter (where coalesce(plan, 'free') = 'premium') as premium_users,
            count(*) filter (where coalesce(plan, 'free') = 'free') as free_users,
            count(*) filter (where coalesce(role, 'user') = 'admin') as admin_users,
            count(*) filter (where last_login_at >= (now() - interval '30 days')) as mau
        from public.user_profiles
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(aggregates_query)
            totals = cur.fetchone() or (0, 0, 0, 0, 0)

            # threads count (optional)
            try:
                cur.execute("select count(*) from public.threads")
                threads_row = cur.fetchone()
                threads_count = (threads_row[0] if threads_row else 0) or 0
            except Exception:
                threads_count = 0
            
            # messages count (optional)
            try:
                cur.execute("select count(*) from public.messages")
                messages_row = cur.fetchone()
                messages_count = (messages_row[0] if messages_row else 0) or 0
            except Exception:
                messages_count = 0
    total_threads=0
    # total_threads = _compute_thread_and_message_stats()
    print("METRICS DEBUG →", {
    "threads": total_threads,
    "totals": totals,
    })
    return MetricsResponse(
        total_users=totals[0] or 0,
        premium_users=totals[1] or 0,
        free_users=totals[2] or 0,
        admin_users=totals[3] or 0,
        # monthly_active_users=totals[4] or 0,
        total_threads=total_threads or 0,
    )


@router.get("/pricing", response_model=list[PricingPlanOut])
def list_pricing_plans(_: str = Depends(require_admin_user_id)):
    query = """
        select id, name, stripe_price_id, monthly_price_label, description,
               coalesce(is_active, true) as is_active,
               created_at, updated_at
        from public.pricing_plans
        order by created_at asc nulls last
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall() or []

    return [
        PricingPlanOut(
            id=str(row[0]),
            name=row[1],
            stripe_price_id=row[2],
            monthly_price_label=row[3],
            description=row[4],
            is_active=bool(row[5]),
            created_at=row[6],
            updated_at=row[7],
        )
        for row in rows
    ]


@router.patch("/pricing/{plan_id}", response_model=PricingPlanOut)
def update_pricing_plan(
    plan_id: str,
    body: PricingPlanUpdate,
    _: str = Depends(require_admin_user_id),
):
    if not body.has_updates():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided to update.",
        )

    updates: list[str] = []
    values: list[object] = []
    if body.monthly_price_label is not None:
        updates.append("monthly_price_label = %s")
        values.append(body.monthly_price_label)
    if body.description is not None:
        updates.append("description = %s")
        values.append(body.description)
    if body.is_active is not None:
        updates.append("is_active = %s")
        values.append(body.is_active)
    if body.stripe_price_id is not None:
        updates.append("stripe_price_id = %s")
        values.append(body.stripe_price_id)
    updates.append("updated_at = now()")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                update public.pricing_plans
                set {', '.join(updates)}
                where id = %s
                returning id, name, stripe_price_id, monthly_price_label,
                          description, coalesce(is_active, true),
                          created_at, updated_at
                """,
                (*values, plan_id),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pricing plan not found",
        )

    return PricingPlanOut(
        id=str(row[0]),
        name=row[1],
        stripe_price_id=row[2],
        monthly_price_label=row[3],
        description=row[4],
        is_active=bool(row[5]),
        created_at=row[6],
        updated_at=row[7],
    )


@pricing_router.get("/pricing", response_model=Optional[PublicPricingPlan])
def get_active_pricing_plan():
    query = """
        select name, monthly_price_label, description
        from public.pricing_plans
        where name = 'premium' and coalesce(is_active, false) = true
        order by updated_at desc nulls last
        limit 1
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()

    if not row:
        return None

    return PublicPricingPlan(
        name=row[0],
        monthly_price_label=row[1],
        description=row[2],
    )

