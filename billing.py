from __future__ import annotations

import os
from typing import Optional

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db_utils import set_user_premium, get_db_connection

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY_TEST")
STRIPE_PRICE_ID = os.getenv("STRIPE_TEST_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET_TEST")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutSessionRequest(BaseModel):
    user_id: str


@router.post("/create-checkout-session")
def create_checkout_session(body: CheckoutSessionRequest):
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        raise HTTPException(
            status_code=500,
            detail="Stripe test keys are not configured on the server.",
        )
    db_price_id: Optional[str] = None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select stripe_price_id
                    from public.pricing_plans
                    where name = 'premium'
                      and coalesce(is_active, false) = true
                    order by updated_at desc nulls last
                    limit 1
                    """
                )
                row = cur.fetchone()
                if row and row[0]:
                    db_price_id = str(row[0])
    except Exception as exc:
        # Don't crash the whole flow if DB lookup fails – just log and fallback
        print(f"[billing] Failed to load premium stripe_price_id from pricing_plans: {exc}")

    # 2) Decide final price id: DB first, then env fallback
    price_id = db_price_id or STRIPE_PRICE_ID

    if not price_id:
        raise HTTPException(
            status_code=500,
            detail=(
                "Stripe price is not configured. "
                "Set stripe_price_id for the 'premium' plan in pricing_plans "
                "or configure STRIPE_TEST_PRICE_ID."
            ),
        )

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=body.user_id,
            success_url=f"{FRONTEND_BASE_URL}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_BASE_URL}/billing/cancel",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"url": session.url}

@router.post("/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Stripe webhook secret is not configured.",
        )

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    event = None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid payload") from exc
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    if event["type"] == "checkout.session.completed":
        data = event["data"]["object"]
        user_id: Optional[str] = data.get("client_reference_id")
        customer_id: Optional[str] = data.get("customer")
        subscription_id: Optional[str] = data.get("subscription")

        if user_id:
            set_user_premium(
                user_id,
                customer_id=customer_id,
                subscription_id=subscription_id,
            )

    return {"received": True}
