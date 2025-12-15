import os
from contextlib import contextmanager
from typing import Optional

import psycopg


DB_URI = (
    os.getenv("SUPABASE_DB_URI")
)

if not DB_URI:
    raise RuntimeError(
        "Database URI not configured. Set SUPABASE_DB_URI, DATABASE_URL, or DB_URI."
    )


@contextmanager
def get_db_connection():
    conn = psycopg.connect(DB_URI, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def get_user_plan(user_id: str) -> str:
    """
    Return the user's plan from public.user_profiles. Defaults to 'free'.
    """
    if not user_id:
        return "free"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select plan from public.user_profiles where id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                return "free"
            return row[0]


def set_user_plan(
    user_id: str,
    plan: str,
    *,
    stripe_customer_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None,
) -> None:
    """
    Upsert the user's plan and optionally store Stripe identifiers.
    """
    if not user_id:
        return

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into public.user_profiles (id, email, plan, stripe_customer_id, stripe_subscription_id)
                values (
                    %(user_id)s,
                    coalesce((select email from auth.users where id = %(user_id)s), ''),
                    %(plan)s,
                    %(customer)s,
                    %(subscription)s
                )
                on conflict (id) do update set
                    plan = excluded.plan,
                    stripe_customer_id = excluded.stripe_customer_id,
                    stripe_subscription_id = excluded.stripe_subscription_id,
                    updated_at = now();
                """,
                {
                    "user_id": user_id,
                    "plan": plan,
                    "customer": stripe_customer_id,
                    "subscription": stripe_subscription_id,
                },
            )


def set_user_premium(
    user_id: str,
    *,
    customer_id: Optional[str] = None,
    subscription_id: Optional[str] = None,
) -> None:
    set_user_plan(
        user_id,
        "premium",
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
    )
