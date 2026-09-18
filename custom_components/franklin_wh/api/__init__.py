"""FranklinWH cloud client, vendored from richo/franklinwh-python.

Base: upstream main @ 77504d8 (2026-08-15), MIT licensed. Local changes, all
marked in client.py: a None guard in get_stats(), set_mode_reserve() from
upstream PR #37, and the installer-only /manage/ endpoint removed. To sync,
diff against that upstream commit.
"""

from .constants import DEFAULT_URL_BASE
from .client import (
    AccessoryType,
    AccountLockedException,
    Client,
    Current,
    DeviceTimeoutException,
    ExportMode,
    ExportSettings,
    GatewayOfflineException,
    GridStatus,
    HttpClientFactory,
    InvalidCredentialsException,
    InvalidDataException,
    MODE_EMERGENCY_BACKUP,
    MODE_MAP,
    MODE_SELF_CONSUMPTION,
    MODE_TIME_OF_USE,
    Mode,
    PermissionDeniedException,
    Stats,
    SwitchState,
    TokenExpiredException,
    TokenFetcher,
    Totals,
    UnknownMethodsClient,
    WORK_MODE_TO_NAME,
)

__all__ = [
    "DEFAULT_URL_BASE",
    "AccessoryType",
    "AccountLockedException",
    "Client",
    "Current",
    "DeviceTimeoutException",
    "ExportMode",
    "ExportSettings",
    "GatewayOfflineException",
    "GridStatus",
    "HttpClientFactory",
    "InvalidCredentialsException",
    "InvalidDataException",
    "MODE_EMERGENCY_BACKUP",
    "MODE_MAP",
    "MODE_SELF_CONSUMPTION",
    "MODE_TIME_OF_USE",
    "Mode",
    "PermissionDeniedException",
    "Stats",
    "SwitchState",
    "TokenExpiredException",
    "TokenFetcher",
    "Totals",
    "UnknownMethodsClient",
    "WORK_MODE_TO_NAME",
]
