from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from enovapower.async_client import AsyncEnovaClient
from enovapower.client import EnovaClient
from enovapower.exceptions import (
    EnovaAuthError,
    EnovaError,
    EnovaNetworkError,
    EnovaSessionExpiredError,
)
from enovapower.models import (
    BillingPeriod,
    GreenButtonInterval,
    TariffRate,
    UsageReading,
)
from enovapower.parsers import (
    parse_billing_periods,
    parse_csv,
    parse_current_plan,
    parse_green_button_xml,
    parse_tariff_html,
)
from enovapower.storage import UsageStore

try:
    __version__ = _pkg_version("enovapower")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0+unknown"

__all__ = [
    "__version__",
    "AsyncEnovaClient",
    "EnovaClient",
    "EnovaAuthError",
    "EnovaError",
    "EnovaNetworkError",
    "EnovaSessionExpiredError",
    "BillingPeriod",
    "GreenButtonInterval",
    "TariffRate",
    "UsageReading",
    "UsageStore",
    "parse_billing_periods",
    "parse_csv",
    "parse_current_plan",
    "parse_green_button_xml",
    "parse_tariff_html",
]
