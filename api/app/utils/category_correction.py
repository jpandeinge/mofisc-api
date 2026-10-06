# app/utils/category_correction.py

import re

import spacy

# Load the spaCy model (make sure to install it: python -m spacy download en_core_web_lg)
nlp = spacy.load("en_core_web_lg")


def correct_predictions(row):
    """
    Apply rule-based corrections to ML model predictions for transaction categorization.
    This function should be called AFTER the ML model makes its initial prediction
    to refine and correct categories based on transaction particulars and patterns.

    Args:
        row: Dictionary containing transaction data with Category, TransactionParticulars, etc.

    Returns:
        str: Corrected category name
    """
    # Check for different possible column names and use empty string as default
    particulars = row.get(
        "TransactionParticulars", row.get("TransactionDescription", "")
    ).lower()
    debit_credit = row.get("TransactionTypeDebitCredit", "").lower()
    channel = row.get("Channel", "").lower()

    # Rule for 'tbms' in 'Channel'
    if "tbms" in channel:
        return "Bank Fees"
    # Rule for 'bluevoucher'
    if "bluevoucher" in particulars:
        return (
            "Bluevoucher Transfer" if debit_credit == "debit" else "Bluevoucher Income"
        )

    # Rule for 'mtc' and 'tn mobile' in 'TransactionParticulars'
    if any(x in particulars for x in ["mtc", "tn mobile", "prepaid airtime"]):
        return "Airtime"

    if re.search(
        r"ibpmt/tv dstv/box office|mbpmt/tv dstv/box office|dstv|multichoice|tv gotv eb|gotv|fnxxmchoic",
        particulars,
    ):
        return "DSTV"

    if re.search(r"xx([a-zA-Z]+)+khomas|bwxxkhomas", particulars):
        return "Medical Aid"

    if re.match(r"bol payment|fnb", particulars):
        return "Payments" if debit_credit == "debit" else "Income"

    if (
        re.search(r"lti premium|fnxxlegals|holifes|bonbenbon|holsib", particulars)
        or channel == "insurance mobility channel"
    ):
        return "Life Insurance"

    if re.search(
        r"atm instant money redemption|atm cash withdrawal sb",
        particulars,
        re.IGNORECASE,
    ):
        return "Cash Withdrawal" if debit_credit == "debit" else "Deposit"

    if re.search(r"pos purchase", particulars, re.IGNORECASE) and re.search(
        r"investment", particulars, re.IGNORECASE
    ):
        return "General Expense" if debit_credit == "debit" else "Unknown"

    if (
        re.search(r"atm rt cash deposit |deposit| cash dep", particulars)
        or re.search(r"atm rt cash deposit", particulars)
        or re.search(r"cash deposit ", particulars)
        or re.search(r"cash deposit", particulars)
    ):
        return "Deposit"

    if re.search(
        r"fnxxokfurnok|xx([a-zA-Z]+)+furnna|xx([a-zA-Z]+)+okfurn", particulars
    ) or re.search(r"sbxxnictus", particulars, re.IGNORECASE):
        return "Furniture Account"

    if re.search(r"furniture mart|home corp|ok furniture|sleepmasters", particulars):
        return "Furniture Shopping"

    if channel == "vehicle and asset finance (hpnl)":
        return "Vehicle Finance"

    if re.match(
        r"history statement|fee- atm cash withdrawal|a/c. transfer reversal confirmation letter fees",
        particulars,
    ):
        return "Bank Fees"

    if re.search(r"the biltong shop|biltong|catering and contracts", particulars):
        return "Food & Beverages"

    if re.search(r"pos purchase ", particulars, re.IGNORECASE) and re.search(
        r"gregs|greg s|novel motor company|motor|garage|panel beating|motors|tiger wheel andtyre|tyre|wheel|toyota|indongo toyota|auto",
        particulars,
        re.IGNORECASE,
    ):
        return "Automotive"

    if re.search(r"pos purchase ", particulars, re.IGNORECASE) and re.search(
        r"debonairs|king pie", particulars, re.IGNORECASE
    ):
        return "Fast Food"

    if re.search(r"pos purchase ", particulars, re.IGNORECASE) and re.search(
        r"spur|mugg bean|joe s beerhouse", particulars, re.IGNORECASE
    ):
        return "Eating Out"

    if re.search(r"pos purchase ", particulars, re.IGNORECASE) and re.search(
        r"slabbert fuels puma", particulars, re.IGNORECASE
    ):
        return "Service Station"

    if re.search(r"int.coll", particulars):
        return "Interest Collection"

    if re.search(r"pos purchase ", particulars, re.IGNORECASE) and re.match(
        r"bet|betmoney|betway|pstbet|casino|gambling|lotto", particulars
    ):
        return "Gambling"

    if re.search(r"pos purchase ", particulars, re.IGNORECASE) and re.match(
        r"paytoday|paytoday business", particulars
    ):
        return "Paytoday"

    if re.search(r"a/c. transfer", particulars):
        return "Account Transfer"

    if "pos purchase" in particulars:
        if "investment" in particulars:
            return "General Expense"
        elif any(
            x in particulars
            for x in [
                "fruit and veg",
                "ok minimark",
                "supermarket",
                "superma",
                "market",
                "cash and carry",
                "supermarket",
                "market",
                "marke",
                "megasave",
                "namica",
                "cash andcarry",
                "cash and carry",
                "spot on",
                "spar",
            ]
        ):
            return "Groceries"
        elif any(
            x in particulars
            for x in [
                "pub",
                "bottle stor",
                "booze",
                "lounge",
                "club",
                "liquor",
                "bar",
                "lounge",
                "club",
                "bottle store",
                "brewers market",
                "liquorshop",
                "liquor shop",
                "liquor",
                "tops",
            ]
        ):
            return "Entertainment"
        elif any(
            x in particulars
            for x in [
                "buco",
                "bargain",
                "build it",
                "mega build",
                "megabuild",
                "ark trading",
                "kambwa trading",
                "cashbuild",
                "megabuild",
                "shivute building suppl",
                "build",
                "hardap building suppli",
                "neo paints",
                " hemco paints",
                "paint",
            ]
        ):
            return "Building Supplies"
        elif any(
            x in particulars
            for x in [
                "nust",
                "natis",
                "unam",
                "home affairs",
                "nampost",
                "clean kicks",
                "cleankicks",
            ]
        ):
            return "Service"
        elif any(
            x in particulars
            for x in [
                "game disocunt world",
                "crazystore",
                "game discount",
                "west pack",
                "home",
            ]
        ):
            return "Shopping"
        elif any(
            x in particulars
            for x in [
                "style",
                "big daddy",
                "legit",
                "sportscenekatutur",
                "edg wernhil",
                "beaver canoe",
                "shoe city",
                "studio 88",
                "markham",
                "totalsports",
                "total sports",
                "otb sport",
                "jb sports",
                "sports scene",
                "action sports",
                "sportscene",
                "sportsmans warehouse",
                "sports scene",
                "mrp sport",
            ]
        ):
            return "Clothing"
        elif any(
            x in particulars
            for x in [
                "airlink",
                "guesthouse",
                "halali toursit",
                "tourist",
                "namutoni etosha",
                "etosha",
                "gondwana",
                "gondwa",
                "hotel",
                "hote",
                "lodge",
                "game reserve",
                "game lodge",
                "ranch",
                "safari",
                "safari logde",
                "camp",
                "camp site",
                "camping",
                "camping site",
                "national part",
            ]
        ):
            return "Travel"
        elif any(
            x in particulars for x in ["agra", " agra safari", "kaap agri", "fedco"]
        ):
            return "Agricultural Supplies"

    if any(
        x in particulars
        for x in [
            "buco",
            "bargain",
            "build it",
            "mega build",
            "megabuild",
            "ark trading",
            "kambwa trading",
            "cashbuild",
            "megabuild",
            "shivute building suppl",
            "build",
            "hardap building suppli",
            "neo paints",
            " hemco paints",
            "paint",
        ]
    ):
        return "Building Supplies"

    if any(
        x in particulars
        for x in [
            "fruit and veg",
            "ok minimark",
            "supermarket",
            "superma",
            "market",
            "cash and carry",
            "supermarket",
            "market",
            "marke",
            "megasave",
            "namica",
            "cash andcarry",
            "cash and carry",
            "spot on",
            "spar",
            "wb walvisbay",
        ]
    ):
        return "Groceries"

    if any(
        x in particulars
        for x in [
            "bottle stor",
            "booze",
            "lounge",
            "club",
            "liquor",
            "bar",
            "lounge",
            "club",
            "bottle store",
            "brewers market",
            "liquorshop",
            "liquor shop",
            "liquor",
            "tops",
            "pub",
        ]
    ):
        return "Entertainment"
    if any(
        x in particulars
        for x in [
            "airlink",
            "guesthouse",
            "halali toursit",
            "tourist",
            "namutoni etosha",
            "etosha",
            "gondwana",
            "gondwa",
            "hotel",
            "hote",
            "lodge",
            "game reserve",
            "game lodge",
            "ranch",
            "safari",
            "safari logde",
            "camp",
            "camp site",
            "camping",
            "camping site",
            "national part",
        ]
    ):
        return "Travel"

    if any(
        x in particulars
        for x in [
            "style",
            "big daddy",
            "legit",
            "sportscenekatutur",
            "edg wernhil",
            "beaver canoe",
            "shoe city",
            "studio 88",
            "markham",
            "totalsports",
            "total sports",
            "otb sport",
            "jb sports",
            "sports scene",
            "action sports",
            "sportscene",
            "sportsmans warehouse",
            "sports scene",
            "mrp sport",
        ]
    ):
        return "Clothing"

    if re.search(r"scribd|spotify|google storage|google", particulars):
        return "Subscriptions"

    if re.search(r"healing touch spa", particulars):
        return "Lifestyle"

    if re.search(r"\w\d+/\d+/\d+\w+", particulars):
        return "Salary" if debit_credit == "credit" else "Transfer"

    if re.search(r"[a-z]+/[a-z]+/[a-z]+/[a-z]+/\d{12}|s&t", particulars):
        return "Travel Allowance"

    if re.search(r"atm rev of cash withdrawal", particulars, re.IGNORECASE):
        return "Reversal"

    if re.search(r"grneft/itxref/", particulars, re.IGNORECASE) or re.search(
        r"grneft/vatref/", particulars, re.IGNORECASE
    ):
        return "Tax Return"

    if re.search(
        r"e(&|&amp;)water.*eb:\s+\d+|e&water eb|xx([a-zA-Z]+)+erored|xx([a-zA-Z]+)+hbaymu|ibpmt/electriciity|water elec|ibpmt/water elec|mtc contract|ibpmt/mtc contract|e&amp;water city of windhoek prp|nored",
        particulars,
    ):
        return "Utilities"

    if re.search(
        r"xx([a-zA-Z]+)+foursg4s|sbxxgfoursg4s|g4s|foursg4s|namibia security",
        particulars,
    ):
        return "Security Service"

    if re.search(
        r"sbxxrcsjet|ibpmt/amercian swiss account|ibpmt/ackermans",
        particulars,
        re.IGNORECASE,
    ):
        return "Clothing Account"

    if re.search(
        r"homeln|homeloan|homeloa|sbxxhomelo\d+|bwxxpaymnt-mlpayment|mlpayment",
        particulars,
        re.IGNORECASE,
    ):
        return "Home Loan"

    if re.search(r"saving|savings|ibpmt/savings", particulars, re.IGNORECASE):
        return "Savings"

    if re.search(
        r"omlac|outsursupon|mommet|momretsl|omsicn|fnbinsn|outsursu|holins|^[a-z]*(xx)+holis|xx([a-zA-Z]+)+hol|^[a-z]*(xx)+sanlam|bonlife|sbxxomlacn|sanlam|legals|xx([a-zA-Z]+)+legals|santam|lwnamin|fnxxlwnamin\d+|^[a-z]*(xx)+kpnamp|kpnamp|fnxxfnbinsn",
        particulars,
        re.IGNORECASE,
    ):
        return "Insurance"

    if re.search(
        r"endo collection|nbloan|ndloan|omfinn|rcp account|sbxxsbnrcp|nrcp:nbxxnbloan\d+|fnrpexpres|fnrpomfp|fncxfocus|xx([a-zA-Z]+)+persln|xx([a-zA-Z]+)+revoln|ibpmt/vericred|vericred|letsego|loan|loan payment|nrcplo|sbxxnrcplo",
        particulars,
        re.IGNORECASE,
    ):
        return "Loan"

    if re.search(r"disbursement credit|disbursement ", particulars, re.IGNORECASE):
        return "Loan Disbursement"

    if re.search(r"xx([a-zA-Z]+)+nsfafs", particulars, re.IGNORECASE):
        return "Student Loan"

    if re.search(
        r"call acc|algray|allangray|pointbreak|ijg|pointb|point break|ominvb|sbxxomutnm|mergence|eos capital|nam asset|namibia asset ma|ashburton|ninety one|simonis|money market|dividend|letsego div|cgp div|easy equities|stanlib",
        particulars,
        re.IGNORECASE,
    ):
        return "Investment"

    if re.search(r"sbxxtelepatelepassport|fnrpcapri", particulars):
        return "Debit Order"

    if re.search(r"nbcard|card|sbxxsbncrc", particulars, re.IGNORECASE) or re.search(
        r"credit card (base 2000)", channel, re.IGNORECASE
    ):
        return "Credit Card"

    if re.search(r"fitcit|ironho-iron house gym", particulars, re.IGNORECASE):
        return "Health & Fitness"

    if re.search(r"dis chem", particulars, re.IGNORECASE):
        return "Health & Beauty"

    if re.search(r"ibpmt/rent", particulars):
        return "Housing"

    if re.search(r"pos purchase ", particulars, re.IGNORECASE) and re.search(
        r"core consumer electron", particulars
    ):
        return "Electronics"

    if re.search(
        r"ibpmt/berg op-school fees|school fees|ibpmt/school fees|educational centre|educational|school|educational|school|whkgym|bwxxwhkgym|bwxxproeda-school fees|proeda|christ s love christia|waltons|waltons mega",
        particulars,
    ):
        return "Education"

    if re.search(
        r"khomas medical|biokinetics|magg biokinetics|torga optical|ibpmt/motjari pharmacy|pharmacy|pharma|pharmaceuticals|medi|medipharma|medical|clinic|mediclinic|medi clinic|hospital|diago lab|pathcare| path care|bwxxxkhomas-|bwxxxprlife|prlife|nhp|bwxxnamihp-nhp|dr |healthrite phamaceutic|paramount healthcare|dental practice",
        particulars,
    ):
        return "Healthcare"

    if re.search(r"fnxxxxnmc", particulars):
        return "Medical Aid"

    if re.match(r"ibpmt/|send moneey dr", particulars):
        return "Transfers" if debit_credit == "debit" else "Income"

    # Named Entity Recognition to detect names
    doc = nlp(particulars)  # Use original case for NER accuracy
    person_names = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
    if person_names and not any(
        x in particulars for x in ["pos purchase", "atm cash withdrawal"]
    ):
        return "Personal Transfer"

    # Ensure consistent naming conventions for categories
    if row["Category"] in ["Personal / Business Loan", "Loan"]:
        return "Loan"
    if row["Category"] in ["Shopping"]:
        return "Clothing"

    if row["Category"] in ["Transfer"]:
        return "Transfers"

    if row["Category"] in ["Medical Aid"]:
        return "Unknown"

    if row["Category"] in ["Fuel/Retail", "Service Station"]:
        return "Service Station"

    if row["Category"] in ["Debit Orders", "Debit Order"]:
        return "Debit Order"

    if row["Category"] in ["General Expenses", "General Expense"]:
        return "General Expense"

    if row["Category"] in ["ATM Cash Windrawal", "ATM Cash Withdrawal"]:
        return "ATM Cash Withdrawal"

    return row["Category"]
