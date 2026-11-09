import json
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Booking, Locker, LockerEvent, Payment
from ..payments import verify_signature
from ..state_machine import State, apply

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
log = logging.getLogger("app.webhooks")


@router.post("/razorpay")
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(""),
                           x_razorpay_event_id: str = Header(""),
                           session: AsyncSession = Depends(get_session)):
    raw = await request.body()
    if not verify_signature(raw, x_razorpay_signature):  # REQ-PAY-002
        raise HTTPException(400, "BAD_SIGNATURE")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(400, "INVALID_JSON") from error
    if not isinstance(data, dict):
        raise HTTPException(400, "INVALID_PAYMENT_EVENT")
    event_type = data.get("event")
    if (
        not isinstance(event_type, str)
        or event_type not in {"payment.captured", "payment.failed"}
        or not x_razorpay_event_id
    ):
        return {"ok": True}  # ignore other events
    try:
        ent = data["payload"]["payment"]["entity"]
        order_id = ent["order_id"]
        payment_id = ent["id"]
        amount = ent["amount"]
        currency = ent["currency"]
    except (KeyError, TypeError) as error:
        raise HTTPException(400, "INVALID_PAYMENT_EVENT") from error

    try:
        async with session.begin():
            booking = (await session.execute(
                select(Booking).where(Booking.razorpay_order_id == order_id).with_for_update()
            )).scalar_one_or_none()
            if booking is None:
                log.warning("Captured payment webhook references an unknown order")
                return {"ok": True}
            now = (await session.execute(select(func.utc_timestamp(3)))).scalar_one()
            status = "captured" if event_type == "payment.captured" else "failed"
            if event_type == "payment.failed":
                status = "failed_attempt"
                log.info("Payment attempt failed for booking %s; hold remains retryable until cancellation or expiry",
                         booking.public_id)
            elif amount != booking.amount_paise or currency != "INR":
                status = "amount_mismatch"
                log.error("Captured payment amount/currency mismatch for booking %s", booking.public_id)
            elif booking.state == State.CREATED and booking.expires_at <= now:
                booking.state = apply(booking.state, "expire")
                status = "late_captured"
                locker = (
                    await session.execute(
                        select(Locker).where(Locker.id == booking.locker_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if locker is not None and locker.status == "held":
                    other_active = (
                        await session.execute(
                            select(Booking.id)
                            .where(
                                Booking.locker_id == locker.id,
                                Booking.id != booking.id,
                                Booking.state.in_(
                                    (State.CREATED, State.PAID, State.STORED, State.OVERSTAY, State.DISPUTED)
                                ),
                            )
                            .limit(1)
                        )
                    ).first()
                    if other_active is None:
                        locker.status = "free"
                session.add(
                    LockerEvent(
                        locker_id=booking.locker_id,
                        booking_id=booking.id,
                        type="late_payment_reconciliation_required",
                        payload={"public_id": booking.public_id, "payment_id": payment_id},
                    )
                )
                log.error("Captured payment arrived after booking hold expired: %s", booking.public_id)
            elif booking.state == State.CREATED:
                booking.state = apply(booking.state, "payment_captured")  # REQ-PAY-001
                booking.paid_at = now
                booking.ends_at = now + timedelta(hours=booking.duration_hours)
            else:
                status = "captured_unapplied"
                log.error("Captured payment cannot confirm booking %s in state %s",
                          booking.public_id, booking.state.value)
                session.add(
                    LockerEvent(
                        locker_id=booking.locker_id,
                        booking_id=booking.id,
                        type="captured_payment_reconciliation_required",
                        payload={"public_id": booking.public_id, "payment_id": payment_id},
                    )
                )

            if status == "amount_mismatch":
                session.add(
                    LockerEvent(
                        locker_id=booking.locker_id,
                        booking_id=booking.id,
                        type="captured_payment_reconciliation_required",
                        payload={"public_id": booking.public_id, "payment_id": payment_id},
                    )
                )

            session.add(
                Payment(
                    booking_id=booking.id,
                    gateway_payment_id=payment_id,
                    gateway_event_id=x_razorpay_event_id,
                    amount_paise=amount,
                    status=status,
                )
            )
    except IntegrityError:
        return {"ok": True, "duplicate": True}
    if status in {"late_captured", "amount_mismatch", "captured_unapplied"}:
        return {"ok": True, "reconciliation_required": True}
    return {"ok": True}
