"""IBKR gateway registry: hands out the one :class:`IbkrGateway`, or None.

None means IBKR is not configured (``IBKR_SVC_URL`` empty), and every IBKR
path checks for it rather than for the setting. The instance is built on first
use, so importing this package does no I/O and needs no startup hook.
"""

from app.config import settings
from app.services.ibkr.base import ContractInfo, IbkrGateway
from app.services.ibkr.client import HttpIbkrGateway

__all__ = ["ContractInfo", "IbkrGateway", "get_gateway"]

_gateway: IbkrGateway | None = None


def get_gateway() -> IbkrGateway | None:
    """The configured gateway, or None when IBKR is off."""
    global _gateway
    if _gateway is None and settings.ibkr_svc_url:
        _gateway = HttpIbkrGateway()
    return _gateway
