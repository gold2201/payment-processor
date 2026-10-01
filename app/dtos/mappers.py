from app.dtos.payment import PaymentDTO
from app.models.payment import Payment


def payment_to_dto(payment: Payment) -> PaymentDTO:
    return PaymentDTO(
        payment_id=payment.payment_id,
        amount=payment.amount,
        currency=payment.currency,
        description=payment.description,
        metadata=payment.metadata_,
        status=payment.status,
        webhook_url=payment.webhook_url,
        created_at=payment.created_at,
        processed_at=payment.processed_at,
    )
