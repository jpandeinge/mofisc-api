"""
Text formatting utilities for transaction data.
Provides consistent string formatting across the application.
"""

import re
import unicodedata

# Known acronyms that should be uppercase
KNOWN_ACRONYMS = {
    "atm",
    "pos",
    "llc",
    "ltd",
    "inc",
    "co",
    "corp",
    "dba",
    "usa",
    "uk",
    "eu",
    "api",
    "url",
    "http",
    "https",
    "id",
    "vat",
    "gst",
    "ptyltd",
    "pty",
    "asx",
    "nsw",
    "qld",
    "vic",
    "wa",
    "sa",
    "tas",
    "nt",
    "act",
}

# Common merchant name variations for normalization (International + African focus)
MERCHANT_NORMALIZATIONS = {
    # International Fast Food and Chains
    "mcdonalds": "mcdonald's",
    " Macdonald ": " McDonald's ",
    "macdonalds": "mcdonald's",
    "burger king": "burger king",
    "kfc": "kfc",
    "subway": "subway",
    "dominos": "domino's pizza",
    "pizza hut": "pizza hut",
    "starbucks coffee": "starbucks",
    "starbucks store": "starbucks",
    "papa johns": "papa john's",
    "dunkin donuts": "dunkin'",
    "tim hortons": "tim hortons",
    "hungry lion": "hungry lion",
    "debonairs": "debonairs",
    "king pie": "king pie",
    # International Retailers
    "woolworths": "woolworths",
    "coles": "coles",
    "aldi": "aldi",
    "lidl": "lidl",
    "iga": "iga",
    "bunnings": "bunnings",
    "target": "target",
    "kmart": "kmart",
    "big w": "big w",
    "tesco": "tesco",
    "sainsburys": "sainsbury's",
    "asda": "asda",
    "morrisons": "morrisons",
    "carrefour": "carrefour",
    "auchan": "auchan",
    # African/Namibian Retailers and Chains
    "shoprite": "shoprite",
    "pick n pay": "pick n pay",
    "model": "model",
    "checkers": "checkers",
    "spar": "spar",
    "woermann brok": "woermann brok",
    "bokomo": "bokomo",
    "nep": "nep",
    "okatjaindu": "okatjaindu",
    "okatji": "okatji",
    "woerman": "woermann",
    "woermann and brok": "woermann brok",
    # Banks and Financial (International + African)
    "commonwealth bank": "cba",
    "nab": "national australia bank",
    "anz": "anz",
    "westpac": "westpac",
    "barclays": "barclays",
    "hsbc": "hsbc",
    "lloyds": "lloyds",
    "natwest": "natwest",
    "rbs": "royal bank of scotland",
    "santander": "santander",
    "bank of america": "boa",
    "chase": "jpmorgan chase",
    "wells fargo": "wells fargo",
    "citibank": "citibank",
    "deutsche bank": "deutsche bank",
    # African Banks
    "first national bank": "fnb",
    "nedbank": "nedbank",
    "absa": "absa",
    "standard bank": "standard bank",
    "sbn": "standard bank",
    "capitec": "capitec",
    "bidvest": "bidvest",
    "bank windhoek": "bank windhoek",
    "fnb namibia": "fnb",
    "standard bank namibia": "standard bank",
    "commercial bank namibia": "commercial bank",
    "trustco": "trustco",
    # Mobile Money and Digital Wallets (African focus)
    "m-pesa": "m-pesa",
    "airtel money": "airtel money",
    "orange money": "orange money",
    "tigo cash": "tigo cash",
    "vodafone cash": "vodafone cash",
    "ecocash": "ecocash",
    "onemoney": "onemoney",
    "moov money": "moov money",
    "mobile money": "mobile money",
    "cash transfer": "cash transfer",
    "mobile transfer": "mobile transfer",
    "ewallet" "ewallet transfer": "ewallet transfer",
    "ewallet payment": "ewallet payment",
    "ewallet withdrawal": "ewallet withdrawal",
    "ewallet deposit": "ewallet deposit",
    "ewallet balance": "ewallet balance",
    "ewallet transaction": "ewallet transaction",
    "ewallet transfer": "ewallet transfer",
    "ewallet payment": "ewallet payment",
    "pay 2 cell": "pay 2 cell",
    "blue voucher": "blue voucher",
    "easy wallet": "easy wallet",
    # Utilities and Government (African focus)
    "namibia power": "nampower",
    "namibia water": "namwater",
    "municipality": "municipality",
    "city of windhoek": "windhoek municipality",
    "namibia broadcasting": "nbc",
    "telecom namibia": "tnam",
    "leo": "leo",
    "paratus": "paratus",
    # Transportation and Travel
    "air namibia": "air namibia",
    "south african airways": "saa",
    "british airways": "ba",
    "lufthansa": "lufthansa",
    "klm": "klm",
    "emirates": "emirates",
    "qatar airways": "qatar",
    "etihad": "etihad",
    "shell": "shell",
    "total": "total",
    "engen": "engen",
    "bp": "bp",
    "caltex": "caltex",
    "petrobras": "petrobras",
    # Generic terms to normalize (International + African)
    "coffee shop": "cafe",
    "restaurant": "diner",
    "take away": "takeaway",
    "take-out": "takeaway",
    "gas station": "petrol station",
    "service station": "petrol station",
    "filling station": "petrol station",
    "shopping centre": "shopping center",
    "shopping center": "shopping center",
    "mall": "shopping center",
    "supermarket": "supermarket",
    "department store": "dept store",
    "convenience store": "convenience store",
    "mini market": "convenience store",
    "tuck shop": "convenience store",
    "spaza shop": "convenience store",
    "shebeen": "bar",
    "pub": "bar",
    "tavern": "bar",
    # Healthcare and Pharmacy
    "pharmacy": "pharmacy",
    "chemist": "pharmacy",
    "hospital": "hospital",
    "clinic": "clinic",
    "medical center": "medical center",
    "dental": "dental",
    "optometrist": "optometrist",
    # Education
    "university": "university",
    "college": "college",
    "school": "school",
    "academy": "academy",
    # Technology and Services
    "vodafone": "vodafone",
    "mtn": "mtn",
    "airtel": "airtel",
    "orange": "orange",
    "tigo": "tigo",
    "africell": "africell",
    "internet cafe": "cyber cafe",
    "phone shop": "phone shop",
    "electronics": "electronics",
    # Food and Beverage
    "fast food": "fast food",
    "quick service": "fast food",
    "bakery": "bakery",
    "butchery": "butchery",
    "delicatessen": "deli",
}

# Noise words to filter out during similarity matching (International + African)
SIMILARITY_NOISE_WORDS = {
    # Business structures (International)
    "ltd",
    "limited",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "llc",
    "llp",
    "ptyltd",
    "pty",
    "plc",
    "ltda",
    "sa",
    "sas",
    "sarl",
    "bv",
    "nv",
    "ag",
    "gmbh",
    "kg",
    "ohg",
    # Common words
    "and",
    "the",
    "of",
    "at",
    "in",
    "on",
    "by",
    "for",
    "with",
    "from",
    "to",
    "per",
    "via",
    # Business types and locations
    "store",
    "shop",
    "branch",
    "location",
    "outlet",
    "centre",
    "center",
    "plaza",
    "mall",
    "market",
    "supermarket",
    "hyper",
    "mini",
    "super",
    # Address components (International)
    "st",
    "street",
    "rd",
    "road",
    "ave",
    "avenue",
    "blvd",
    "boulevard",
    "ln",
    "lane",
    "dr",
    "drive",
    "ct",
    "court",
    "place",
    "pde",
    "parade",
    "square",
    "close",
    "crescent",
    "way",
    "path",
    "grove",
    "heath",
    "hill",
    "park",
    "green",
    "meadow",
    "field",
    "valley",
    "ridge",
    "brook",
    "stream",
    # Transportation
    "hwy",
    "highway",
    "fwy",
    "freeway",
    "expressway",
    "motorway",
    "autobahn",
    "autoroute",
    "autostrada",
    "ringroad",
    # African specific terms
    "spaza",
    "tuck",
    "shebeen",
    "kiosque",
    "duka",
    "dukana",
    "maghreb",
    "souk",
    "bazaar",
    "emporium",
    # Numbers and ordinals
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "main",
    "central",
    "north",
    "south",
    "east",
    "west",
    "upper",
    "lower",
    "new",
    "old",
    # Services
    "services",
    "service",
    "solutions",
    "group",
    "holdings",
    "enterprises",
    "trading",
    "wholesale",
    "retail",
    "direct",
    "online",
    "digital",
    "mobile",
    "cash",
    "pay",
    "payment",
    "transfer",
    "transaction",
}


def to_title_case(text: str) -> str:
    """
    Converts a string to Title Case with smart handling of edge cases.
    Handles: apostrophes, hyphens, multiple spaces, acronyms, and special characters.

    Examples:
        >>> to_title_case("hello world")
        "Hello World"
        >>> to_title_case("mcdonald's")
        "McDonald's"
        >>> to_title_case("7-eleven")
        "7-Eleven"
        >>> to_title_case("hello  world")
        "Hello World"
        >>> to_title_case("atm withdrawal")
        "ATM Withdrawal"
        >>> to_title_case("AT&T")
        "AT&T"

    Args:
        text: The string to convert to Title Case

    Returns:
        The string formatted in Title Case
    """
    if not text or not isinstance(text, str):
        return ""

    # Trim and normalize multiple spaces to single space
    normalized = " ".join(text.split())

    if not normalized:
        return ""

    words = normalized.split(" ")
    result = []

    for word in words:
        if not word:
            continue

        # Check if word is already an acronym with uppercase letters (e.g., "AT&T", "USA")
        # If it has uppercase letters and is relatively short, preserve it
        if len(word) <= 5 and any(c.isupper() for c in word) and word == word.upper():
            result.append(word)
            continue

        # Handle hyphenated words (e.g., "7-eleven" → "7-Eleven")
        if "-" in word:
            hyphenated_parts = [_title_case_word(part) for part in word.split("-")]
            result.append("-".join(hyphenated_parts))
            continue

        # Handle words with apostrophes (e.g., "mcdonald's" → "McDonald's")
        if "'" in word:
            parts = word.split("'")
            # First part gets title cased, subsequent parts get lowercase
            apostrophe_parts = [_title_case_word(parts[0])] + [
                p.lower() for p in parts[1:]
            ]
            result.append("'".join(apostrophe_parts))
            continue

        result.append(_title_case_word(word))

    return " ".join(result)


def _title_case_word(word: str) -> str:
    """
    Helper function to title case a single word.
    Checks for known acronyms and handles capitalization.

    Args:
        word: A single word to title case

    Returns:
        The word in title case
    """
    if not word:
        return word

    lower_word = word.lower()

    # Check if it's a known acronym
    if lower_word in KNOWN_ACRONYMS:
        return word.upper()

    # Standard title case: capitalize first letter, lowercase the rest
    return word[0].upper() + word[1:].lower()


def normalize_for_similarity(text: str) -> str:
    """
    Normalize text for better similarity matching by removing common noise words
    and standardizing common merchant name variations.

    This is specifically designed for fuzzy matching of merchant names and categories
    in transaction similarity search, with support for international and African contexts.

    Args:
        text: The text to normalize

    Returns:
        Normalized text optimized for similarity comparison
    """
    if not text or not isinstance(text, str):
        return ""

    # Normalize Unicode characters (handle accents, diacritics, etc.)
    # This converts characters like é, ñ, ü to e, n, u for better matching
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))

    # Convert to lowercase and remove extra whitespace
    normalized = " ".join(normalized.split()).lower()

    if not normalized:
        return ""

    # Apply merchant name normalizations
    for old, new in MERCHANT_NORMALIZATIONS.items():
        normalized = normalized.replace(old, new)

    # Handle common international variations
    # Remove currency symbols and numbers that might interfere
    normalized = re.sub(
        r"[\d\$\€\£\¥\₹\₦\₨\₩\₪\₫\₭\₮\₯\₱\₲\₳\₴\₵\₶\₷\₸\₹\₺\₻\₼\₽\₾\₿]", " ", normalized
    )

    # Remove punctuation and special characters (but keep spaces and common separators)
    normalized = re.sub(r"[^\w\s\-\.]", " ", normalized)

    # Handle phone numbers (common in African mobile money transactions)
    normalized = re.sub(
        r"\b\d{3,4}[\-\.\s]?\d{3,4}[\-\.\s]?\d{3,4}\b", " phone ", normalized
    )

    # Split into words and filter out noise words
    words = normalized.split()
    filtered_words = []

    for word in words:
        # Skip noise words
        if word in SIMILARITY_NOISE_WORDS:
            continue

        # Skip very short words (likely abbreviations or noise)
        if len(word) < 2:
            continue

        # Skip extremely long words (likely errors or concatenated text)
        if len(word) > 50:
            continue

        # Clean the word of remaining punctuation
        clean_word = re.sub(r"[^\w]", "", word)

        # Only add if cleaning didn't remove everything
        if clean_word and len(clean_word) >= 2:
            filtered_words.append(clean_word)

    # If filtering removed too many words, fall back to a more conservative approach
    if len(filtered_words) < 2 and len(words) >= 2:
        # Keep words that are at least 3 characters long and not noise
        conservative_words = []
        for word in words:
            clean_word = re.sub(r"[^\w]", "", word)
            if (
                len(clean_word) >= 3
                and clean_word not in SIMILARITY_NOISE_WORDS
                and len(clean_word) <= 30
            ):  # Reasonable length limit
                conservative_words.append(clean_word)

        if conservative_words:
            filtered_words = conservative_words

    # Ensure we have at least some content
    if not filtered_words:
        # Return original cleaned text if filtering was too aggressive
        # But still apply Unicode normalization and basic cleaning
        fallback = unicodedata.normalize("NFKD", text)
        fallback = "".join(c for c in fallback if not unicodedata.combining(c))
        fallback = re.sub(r"[^\w\s]", " ", fallback.lower()).strip()
        return fallback if fallback else "unknown"

    # Sort words to handle different orderings (important for merchant names)
    # This helps match "KFC Windhoek" with "Windhoek KFC"
    if len(filtered_words) > 1:
        # Only sort if we have multiple meaningful words
        filtered_words.sort()

    return " ".join(filtered_words)


def normalize_african_mobile_money(text: str) -> str:
    """
    Special normalization for African mobile money and banking transactions.

    Handles common patterns in African mobile money services like M-Pesa, Airtel Money,
    and various banking transaction formats.

    Args:
        text: The transaction text to normalize

    Returns:
        Normalized text optimized for African mobile money pattern matching
    """
    if not text or not isinstance(text, str):
        return ""

    # Normalize Unicode characters first
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.lower().strip()

    # African mobile money patterns
    mobile_money_patterns = {
        # M-Pesa variations (Kenya, Tanzania)
        r"m[-_\s]?pesa": "m-pesa",
        r"mpesa": "m-pesa",
        r"m\s?pesa": "m-pesa",
        # Airtel Money variations
        r"airtel\s?money": "airtel money",
        r"airtel\s?cash": "airtel money",
        # MTN Mobile Money variations
        r"mtn\s?money": "mtn money",
        r"mtn\s?cash": "mtn money",
        r"mtn\s?mobile\s?money": "mtn money",
        # Orange Money variations
        r"orange\s?money": "orange money",
        r"orange\s?cash": "orange money",
        # Tigo Cash/Pesa variations
        r"tigo\s?cash": "tigo cash",
        r"tigo\s?pesa": "tigo cash",
        # Vodafone Cash variations
        r"vodafone\s?cash": "vodafone cash",
        r"vodafone\s?money": "vodafone cash",
        # Ecocash variations (Zimbabwe)
        r"eco\s?cash": "ecocash",
        r"ecocash": "ecocash",
        # Generic mobile money
        r"mobile\s?money": "mobile money",
        r"money\s?transfer": "mobile money",
        r"cash\s?transfer": "mobile money",
    }

    # Apply mobile money pattern normalizations
    for pattern, replacement in mobile_money_patterns.items():
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)

    # Handle common African banking transaction patterns
    banking_patterns = {
        # ATM withdrawals
        r"atm\s?withdraw": "atm withdrawal",
        r"cash\s?withdraw": "atm withdrawal",
        r"withdrawal": "atm withdrawal",
        # Bank transfers
        r"bank\s?transfer": "bank transfer",
        r"electronic\s?transfer": "bank transfer",
        r"wire\s?transfer": "bank transfer",
        # POS payments
        r"pos\s?payment": "pos payment",
        r"point\s?of\s?sale": "pos payment",
        r"card\s?payment": "pos payment",
        # Bill payments
        r"bill\s?pay": "bill payment",
        r"utilit": "utility payment",  # Matches utility, utilities
        r"electric": "electricity payment",
        r"water": "water payment",
        r"phone": "phone payment",
        r"airtime": "airtime purchase",
    }

    # Apply banking pattern normalizations
    for pattern, replacement in banking_patterns.items():
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)

    # Handle phone numbers (very common in African transactions)
    # Format: +264XXXXXXXXX, 264XXXXXXXXX, 0XXXXXXXXX
    phone_patterns = [
        r"\+264\d{9}",  # Namibian format
        r"264\d{9}",  # Namibian without +
        r"0\d{9}",  # Local format (various countries)
        r"\d{3}[\s\-\.]?\d{3}[\s\-\.]?\d{4}",  # International format
    ]

    for pattern in phone_patterns:
        if re.search(pattern, normalized):
            normalized = re.sub(pattern, " [phone] ", normalized)
            break

    # Handle account numbers (common in banking)
    account_patterns = [
        r"\b\d{8,16}\b",  # 8-16 digit account numbers
    ]

    for pattern in account_patterns:
        matches = re.findall(pattern, normalized)
        for match in matches:
            if len(match) >= 8:  # Only replace longer numbers
                normalized = normalized.replace(match, " [account] ")

    # Clean up extra spaces and normalize
    normalized = " ".join(normalized.split())

    return normalized


def is_african_mobile_money_transaction(text: str) -> bool:
    """
    Determine if a transaction text represents an African mobile money transaction.

    Args:
        text: The transaction text to check

    Returns:
        True if it appears to be a mobile money transaction
    """
    if not text:
        return False

    normalized = normalize_african_mobile_money(text.lower())

    mobile_money_indicators = [
        "m-pesa",
        "airtel money",
        "mtn money",
        "orange money",
        "tigo cash",
        "vodafone cash",
        "ecocash",
        "mobile money",
        "cash transfer",
        "money transfer",
        "[phone]",
    ]

    return any(indicator in normalized for indicator in mobile_money_indicators)
