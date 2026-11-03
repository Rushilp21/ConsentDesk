"""Boundary for a later broker phase. Nothing in the research app executes orders."""
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    quantity: int
    limit_price: float
    research_id: str

class BrokerExecution(Protocol):
    def preview(self, intent: OrderIntent) -> dict: ...
    def submit_confirmed(self, intent: OrderIntent, confirmation_id: str) -> dict: ...

class DisabledExecution:
    def preview(self, intent: OrderIntent) -> dict:
        raise RuntimeError('Broker execution is not implemented. Research only.')

    def submit_confirmed(self, intent: OrderIntent, confirmation_id: str) -> dict:
        raise RuntimeError('Broker execution is not implemented. Research only.')
