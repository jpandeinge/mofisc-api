# api/app/services/security/device_naming_service.py

"""
Device naming service for generating user-friendly device names.

Converts raw browser/OS strings into clean, readable names like:
- "Chrome on Mac"
- "Safari on iPhone"
- "Firefox on Windows"
"""

from typing import Optional


class DeviceNamingService:
    """Service for generating friendly device names."""

    # Browser name mappings (strip version numbers, normalize names)
    BROWSER_MAPPINGS = {
        "chrome": "Chrome",
        "safari": "Safari",
        "firefox": "Firefox",
        "edge": "Edge",
        "opera": "Opera",
        "brave": "Brave",
        "vivaldi": "Vivaldi",
        "samsung": "Samsung Browser",
        "duckduckgo": "DuckDuckGo",
        "tor": "Tor Browser",
    }

    # OS name mappings (user-friendly names)
    OS_MAPPINGS = {
        "macos": "Mac",
        "mac os x": "Mac",
        "mac os": "Mac",
        "windows": "Windows",
        "ios": "iPhone",
        "ipados": "iPad",
        "android": "Android",
        "linux": "Linux",
        "ubuntu": "Linux",
        "fedora": "Linux",
        "chromeos": "Chromebook",
        "chrome os": "Chromebook",
    }

    @classmethod
    def generate_friendly_name(cls, browser: Optional[str], os: Optional[str]) -> str:
        """
        Generate a friendly device name from browser and OS strings.

        Args:
            browser: Browser string (e.g., "Chrome 144.0.0.0")
            os: OS string (e.g., "macOS 14.2.1")

        Returns:
            Friendly name like "Chrome on Mac" or "Unknown Device"
        """
        clean_browser = cls._clean_browser(browser)
        clean_os = cls._clean_os(os)

        if clean_browser and clean_os:
            return f"{clean_browser} on {clean_os}"
        elif clean_browser:
            return clean_browser
        elif clean_os:
            return f"Browser on {clean_os}"
        else:
            return "Unknown Device"

    @classmethod
    def _clean_browser(cls, browser: Optional[str]) -> Optional[str]:
        """Clean browser string by removing version numbers and normalizing."""
        if not browser:
            return None

        # Convert to lowercase for matching
        browser_lower = browser.lower().strip()

        # Skip "Unknown Browser"
        if "unknown" in browser_lower:
            return None

        # Find matching browser name
        for key, friendly_name in cls.BROWSER_MAPPINGS.items():
            if key in browser_lower:
                return friendly_name

        # If no match but has a name, extract first word (before version)
        parts = browser.split()
        if parts:
            first_word = parts[0].strip()
            # Only return if it's not a version number
            if first_word and not first_word[0].isdigit():
                return first_word.capitalize()

        return None

    @classmethod
    def _clean_os(cls, os: Optional[str]) -> Optional[str]:
        """Clean OS string by removing version numbers and normalizing."""
        if not os:
            return None

        # Convert to lowercase for matching
        os_lower = os.lower().strip()

        # Skip "Unknown OS"
        if "unknown" in os_lower:
            return None

        # Find matching OS name
        for key, friendly_name in cls.OS_MAPPINGS.items():
            if key in os_lower:
                return friendly_name

        # If no match, extract first word
        parts = os.split()
        if parts:
            first_word = parts[0].strip()
            if first_word and not first_word[0].isdigit():
                return first_word.capitalize()

        return None

    @classmethod
    def clean_browser_name(cls, browser: Optional[str]) -> Optional[str]:
        """Return clean browser name without version."""
        return cls._clean_browser(browser)

    @classmethod
    def clean_os_name(cls, os: Optional[str]) -> Optional[str]:
        """Return clean OS name without version."""
        return cls._clean_os(os)
