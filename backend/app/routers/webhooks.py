import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Booking, Payment, utcnow
from ..payments import verify_signature
from ..state_machine import State, apply
from datetime import timedelta

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.post("/razorpay")
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(""),
                           x_razorpay_event_id: str = Header(""),
                           session: AsyncSession = Depends(get_session)):
    raw = await request.body()
    if not verify_signature(raw, x_razorpay_signature):  # REQ-PAY-002
        raise HTTPException(400, "BAD_SIGNATURE")
    data = json.loads(raw)
    if data.get("event") != "payment.captured" or not x_razorpay_event_id:
        return {"ok": True}  # ignore other events
    ent = data["payload"]["payment"]["entity"]
    try:
        async with session.begin():
            booking = (await session.execute(
                select(Booking).where(Booking.razorpay_order_id == ent["order_id"]).with_for_update()
            )).scalar_one_or_none()
            if booking is None:
                return {"ok": True}
            # REQ-PAY-003: unique event id; duplicate insert = already processed
            session.add(Payment(booking_id=booking.id, gateway_payment_id=ent["id"],
                                gateway_event_id=x_razorpay_event_id, amount_paise=ent["amount"],
                                status="captured"))
            await session.flush()
            if booking.state == State.CREATED and ent["amount"] == booking.amount_paise:
                booking.state = apply(booking.state, "payment_captured")  # REQ-PAY-001
                booking.paid_at = utcnow()
                booking.ends_at = booking.paid_at + timedelta(hours=booking.duration_hours)
    except IntegrityError:
        pass  # duplicate delivery, safe
    return {"ok": True}
