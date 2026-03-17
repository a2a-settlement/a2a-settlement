"""A2A-SE Federation Protocol — core exchange implementation.

Provides federation endpoints, peer management, attestation import,
health telemetry, capability manifest generation, and Designated Escrow
coordination for cross-exchange settlement.
"""

from exchange.federation.escrow_coordination import FederatedEscrowCoordinator

__all__ = ["FederatedEscrowCoordinator"]
