"""Interface + implementation dispatch patterns.

Used by the recall regression harness to test:
  - interface-override synthesis (A2 channel) via seam_impact on the interface method
  - seam_context callers include implementing classes
"""


class PaymentProcessor:
    """Abstract payment processor interface."""

    def process_payment(self, amount: float) -> bool:
        """Process a payment and return success/failure."""
        raise NotImplementedError

    def refund(self, transaction_id: str) -> bool:
        """Refund a previous transaction."""
        raise NotImplementedError


class StripeProcessor(PaymentProcessor):
    """Stripe implementation of PaymentProcessor."""

    def process_payment(self, amount: float) -> bool:
        """Charge via Stripe API."""
        return True

    def refund(self, transaction_id: str) -> bool:
        """Issue Stripe refund."""
        return True


class PayPalProcessor(PaymentProcessor):
    """PayPal implementation of PaymentProcessor."""

    def process_payment(self, amount: float) -> bool:
        """Charge via PayPal API."""
        return True

    def refund(self, transaction_id: str) -> bool:
        """Issue PayPal refund."""
        return True


class OrderService:
    """Service that uses a PaymentProcessor via dependency injection."""

    def __init__(self, processor: PaymentProcessor) -> None:
        self.processor = processor

    def checkout(self, amount: float) -> bool:
        """Run checkout — delegates to the injected processor."""
        return self.processor.process_payment(amount)

    def cancel_order(self, transaction_id: str) -> bool:
        """Cancel an order — delegates refund to the injected processor."""
        return self.processor.refund(transaction_id)
