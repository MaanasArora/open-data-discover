"""Download City of Toronto Open Data (CKAN) packages with file-based completeness tracking."""

from .core import (
    BASE_URL,
    CKANError,
    Client,
    Downloader,
    LocalPackage,
    PackageResult,
    PackageStatus,
    ResourceResult,
    __version__,
    package_ref,
    resource_filename,
)

__all__ = [
    "BASE_URL",
    "CKANError",
    "Client",
    "Downloader",
    "LocalPackage",
    "PackageResult",
    "PackageStatus",
    "ResourceResult",
    "__version__",
    "package_ref",
    "resource_filename",
]
