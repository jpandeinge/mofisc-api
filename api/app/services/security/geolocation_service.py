# api/app/services/security/geolocation_service.py

"""
IP Geolocation service using ip-api.com (free tier).

Features:
- In-memory cache with 24-hour TTL
- Rate limiting awareness (45 requests/minute)
- Graceful fallback if service unavailable
- Configurable enable/disable via environment

Note: ip-api.com free tier uses HTTP only. For production with sensitive
requirements, consider upgrading to their pro tier (HTTPS) or using
a different provider like ipinfo.io or MaxMind.
"""

import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

import httpx

from ...utils.logging_config import get_logger

logger = get_logger(__name__)

# Allow disabling geolocation in development/testing
GEOLOCATION_ENABLED = os.getenv("GEOLOCATION_ENABLED", "true").lower() == "true"


@dataclass
class GeoLocation:
    """Geolocation result for an IP address."""

    city: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    region: Optional[str] = None
    success: bool = False


class GeoLocationCache:
    """Simple in-memory cache with TTL."""

    def __init__(self, ttl_seconds: int = 86400):  # 24 hours
        self._cache: Dict[str, tuple[GeoLocation, float]] = {}
        self._ttl = ttl_seconds

    def get(self, ip: str) -> Optional[GeoLocation]:
        """Get cached location if not expired."""
        if ip in self._cache:
            location, timestamp = self._cache[ip]
            if time.time() - timestamp < self._ttl:
                return location
            # Expired, remove from cache
            del self._cache[ip]
        return None

    def set(self, ip: str, location: GeoLocation) -> None:
        """Cache a location result."""
        self._cache[ip] = (location, time.time())

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()


# Global cache instance
_geo_cache = GeoLocationCache()


class GeoLocationService:
    """Service for IP geolocation lookups."""

    # ip-api.com free tier - no API key needed
    BASE_URL = "http://ip-api.com/json"

    @classmethod
    async def lookup(cls, ip_address: Optional[str]) -> GeoLocation:
        """
        Look up geolocation for an IP address.

        Returns GeoLocation with success=False if lookup fails.
        Uses caching to minimize API calls.
        """
        # Check if geolocation is disabled
        if not GEOLOCATION_ENABLED:
            logger.debug("Geolocation disabled via environment")
            return GeoLocation(success=False)

        if not ip_address:
            return GeoLocation(success=False)

        # Skip private/local IPs
        if cls._is_private_ip(ip_address):
            return GeoLocation(success=False)

        # Check cache first
        cached = _geo_cache.get(ip_address)
        if cached:
            logger.debug(f"GeoLocation cache hit for {ip_address[:10]}...")
            return cached

        # Make API request
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{cls.BASE_URL}/{ip_address}",
                    params={"fields": "status,city,country,countryCode,regionName"},
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        location = GeoLocation(
                            city=data.get("city"),
                            country=data.get("country"),
                            country_code=data.get("countryCode"),
                            region=data.get("regionName"),
                            success=True,
                        )
                        _geo_cache.set(ip_address, location)
                        return location

                logger.warning(
                    f"GeoLocation API returned non-success for {ip_address[:10]}"
                )

        except httpx.TimeoutException:
            logger.warning(f"GeoLocation lookup timed out for {ip_address[:10]}")
        except httpx.RequestError as e:
            logger.warning(f"GeoLocation request failed: {str(e)[:50]}")
        except Exception as e:
            logger.error(f"GeoLocation unexpected error: {str(e)[:50]}")

        # Return failure result (don't cache failures)
        return GeoLocation(success=False)

    @staticmethod
    def _is_private_ip(ip: str) -> bool:
        """Check if IP is private/local (not routable on internet)."""
        if not ip:
            return True

        # Common private/local ranges
        private_prefixes = (
            "10.",
            "172.16.",
            "172.17.",
            "172.18.",
            "172.19.",
            "172.20.",
            "172.21.",
            "172.22.",
            "172.23.",
            "172.24.",
            "172.25.",
            "172.26.",
            "172.27.",
            "172.28.",
            "172.29.",
            "172.30.",
            "172.31.",
            "192.168.",
            "127.",
            "0.",
            "::1",
            "fe80:",
            "fc00:",
            "fd00:",
        )

        return ip.startswith(private_prefixes) or ip == "localhost"

    @classmethod
    def format_location(cls, location: GeoLocation) -> Optional[str]:
        """Format location as 'City, Country' or None if unavailable."""
        if not location.success:
            return None

        parts = []
        if location.city:
            parts.append(location.city)
        if location.country:
            parts.append(location.country)

        return ", ".join(parts) if parts else None

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the geolocation cache."""
        _geo_cache.clear()
