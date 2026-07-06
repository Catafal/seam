# Order Processing PRD

> Status: ready-for-agent.

## Implementation

Order checkout behavior is implemented by `OrderService.checkout`.

The implementation lives in [interfaces.py](../interfaces.py), and the payment
processor interface is `PaymentProcessor`.
