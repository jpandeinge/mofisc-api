# api/app/utils/statement_ocr.py

import re
from datetime import datetime

import pandas as pd
import pdfplumber

from ..utils.logging_config import get_logger

logger = get_logger(__name__)

BANK_CONFIGS = {
    "standard_bank": {
        "drop_first_column": True,
        "column_names": [
            "Date",
            "TransactionDescription",
            "Debit",
            "Credit",
            "Balance",
        ],
        "rows_to_drop": 2,
    },
    "fnb": {},  # Add any specific configurations for FNB if needed
}


def extract_standard_bank_account_statement(pdf_path, bank_config):
    """
    Extract account statement from a given PDF path according to the bank's configuration.
    Args:
        pdf_path (str): Path to the PDF file.
        bank_config (dict): Configuration dict specifying how to process the statement for the bank.
    Returns:
        pd.DataFrame: Processed data extracted from the PDF.
    """
    combined_table = pd.DataFrame()
    first_page = True

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_tables = page.extract_tables()
            for table in page_tables:
                if first_page:
                    # Process the first page normally, including the header
                    df = pd.DataFrame(table[0:], columns=table[0])
                    first_page = False
                else:
                    # For subsequent pages, exclude rows that are likely to be headers
                    df = pd.DataFrame(
                        [row for row in table[1:] if row[0]],
                        columns=combined_table.columns,
                    )
                combined_table = pd.concat([combined_table, df], ignore_index=True)

    # Post-process the DataFrame as specified in bank_config
    if bank_config.get("drop_first_column", False):
        combined_table = combined_table.drop(combined_table.columns[0], axis=1)
    if "column_names" in bank_config:
        combined_table.columns = bank_config["column_names"]
    if "rows_to_drop" in bank_config:
        combined_table = combined_table.iloc[bank_config["rows_to_drop"] :]

    # combine debit and credit columns to a single amount column named 'TransactionAmount'  and create a new column 'TransactionTypeDebitCredit'
    combined_table["TransactionAmount"] = combined_table.apply(
        lambda x: x["Debit"] if x["Debit"] else x["Credit"], axis=1
    )
    combined_table["TransactionTypeDebitCredit"] = combined_table.apply(
        lambda x: "Debit" if x["Debit"] else "Credit", axis=1
    )
    combined_table = combined_table.drop(["Debit", "Credit"], axis=1)
    combined_table["Balance"] = (
        combined_table["Balance"].str.replace(",", "").astype(float)
    )  # remove commas and convert to float
    combined_table["TransactionAmount"] = (
        combined_table["TransactionAmount"].str.replace(",", "").astype(float)
    )
    combined_table["Date"] = pd.to_datetime(
        combined_table["Date"], format="%d/%m/%Y", errors="coerce"
    )
    combined_table["FormattedDate"] = combined_table["Date"].dt.strftime("%Y-%m-%d")
    combined_table = combined_table.drop(["Date"], axis=1)
    combined_table = combined_table.rename(
        columns={"FormattedDate": "TransactionDate", "Balance": "BookBalance"}
    )
    combined_table = combined_table[
        [
            "TransactionDate",
            "TransactionDescription",
            "TransactionAmount",
            "TransactionTypeDebitCredit",
            "BookBalance",
        ]
    ]  # rearrange columns
    combined_table = combined_table.reset_index(drop=True)

    combined_table = combined_table.rename(
        columns={
            "TransactionDate": "transaction_date",
            "TransactionDescription": "transaction_description",
            "TransactionAmount": "transaction_amount",
            "TransactionTypeDebitCredit": "transaction_type",
            "BookBalance": "balance",
        }
    )

    return combined_table


def extract_fnb_account_statement(file_path):
    all_transactions = []
    statement_period = None

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_transactions.append(text)
                if not statement_period:
                    # Try multiple statement period formats
                    patterns = [
                        r"Statement Period :(\d+ \w+ \d{4}) to(\d+ \w+ \d{4})",
                        r"Statement Period:(\d+ \w+ \d{4}) to (\d+ \w+ \d{4})",
                        r"Statement Period\s*:\s*(\d+ \w+ \d{4})\s*to\s*(\d+ \w+ \d{4})",
                        r"Period\s*:\s*(\d+ \w+ \d{4})\s*-\s*(\d+ \w+ \d{4})",
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, text)
                        if match:
                            try:
                                statement_period = {
                                    "start": datetime.strptime(
                                        match.group(1).strip(), "%d %B %Y"
                                    ),
                                    "end": datetime.strptime(
                                        match.group(2).strip(), "%d %B %Y"
                                    ),
                                }
                                break
                            except ValueError:
                                continue

    full_text = "\n".join(all_transactions)

    transaction_pattern = re.compile(
        r"\n(\d{2} \w{3}) (.*?)(\d+,\d+\.\d{2}|\d+\.\d{2})(Cr|Dr)?\s+(\d+,\d+\.\d{2}|\d+\.\d{2})(Cr|Dr)?(\s+\d+\.\d{2})?"
    )
    transactions = transaction_pattern.findall(full_text)

    if not transactions:
        logger.warning("No transactions found in FNB statement")
        return pd.DataFrame(
            columns=[
                "transaction_date",
                "transaction_description",
                "transaction_amount",
                "transaction_type",
                "balance",
            ]
        )

    transaction_data = pd.DataFrame(
        transactions,
        columns=[
            "Date",
            "Description",
            "Amount",
            "Credit/Debit",
            "Balance",
            "Balance Type",
            "Accrued Bank Charges",
        ],
    )

    # Determine year for date parsing
    if statement_period:
        start_year = statement_period["start"].year
        end_year = statement_period["end"].year
        start_month = statement_period["start"].month
    else:
        # Fallback: use current year
        current_date = datetime.now()
        start_year = current_date.year
        end_year = current_date.year
        start_month = 1
        logger.warning("Statement period not found, using current year: %d", start_year)

    def parse_date_with_year(date_str):
        """Parse date string and append appropriate year."""
        try:
            parsed_date = datetime.strptime(f"{date_str} {start_year}", "%d %b %Y")
            if statement_period and parsed_date.month < start_month:
                parsed_date = parsed_date.replace(year=end_year)
            return parsed_date
        except ValueError:
            logger.warning("Failed to parse date: %s", date_str)
            return None

    transaction_data["Date"] = transaction_data["Date"].apply(parse_date_with_year)

    # Remove rows with unparseable dates
    transaction_data = transaction_data.dropna(subset=["Date"])

    if transaction_data.empty:
        logger.warning("All dates failed to parse in FNB statement")
        return pd.DataFrame(
            columns=[
                "transaction_date",
                "transaction_description",
                "transaction_amount",
                "transaction_type",
                "balance",
            ]
        )

    transaction_data.sort_values(by="Date", ascending=False, inplace=True)

    transaction_data["Description"] = transaction_data["Description"].replace(
        "", "Bank Fees"
    )

    transaction_data["Amount"] = (
        transaction_data["Amount"].str.replace(",", "").astype(float)
    )
    transaction_data["Balance"] = (
        transaction_data["Balance"].str.replace(",", "").astype(float)
    )
    transaction_data["TransactionTypeDebitCredit"] = transaction_data[
        "Credit/Debit"
    ].apply(lambda x: "credit" if x == "Cr" else "debit")
    transaction_data["Amount"] = transaction_data.apply(
        lambda x: (
            x["Amount"] if x["TransactionTypeDebitCredit"] == "debit" else x["Amount"]
        ),
        axis=1,
    )

    # Update how 'Accrued Bank Charges' are processed
    transaction_data["Accrued Bank Charges"] = transaction_data[
        "Accrued Bank Charges"
    ].apply(
        lambda x: (
            x
            if pd.notna(x) and x.strip().replace(",", "").replace(".", "").isdigit()
            else "0.00"
        )
    )

    transaction_data.drop(columns=["Credit/Debit", "Balance Type"], inplace=True)
    # rename columns
    transaction_data.rename(
        columns={
            "Date": "TransactionDate",
            "Description": "TransactionDescription",
            "Amount": "TransactionAmount",
            "TransactionTypeDebitCredit": "TransactionTypeDebitCredit",
            "Balance": "BookBalance",
        },
        inplace=True,
    )
    transaction_data = transaction_data[
        [
            "TransactionDate",
            "TransactionDescription",
            "TransactionAmount",
            "TransactionTypeDebitCredit",
            "BookBalance",
            "Accrued Bank Charges",
        ]
    ]

    # Safely convert datetime to string format
    transaction_data["TransactionDate"] = pd.to_datetime(
        transaction_data["TransactionDate"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")

    transaction_data.reset_index(drop=True, inplace=True)

    transaction_data = transaction_data.rename(
        columns={
            "TransactionDate": "transaction_date",
            "TransactionDescription": "transaction_description",
            "TransactionAmount": "transaction_amount",
            "TransactionTypeDebitCredit": "transaction_type",
            "BookBalance": "balance",
        }
    )

    return transaction_data


def extract_nedbank_account_statement(pdf_path):
    transactions = []
    previous_balance = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            lines = text.split("\n")

            for i, line in enumerate(lines):
                match = re.match(
                    r"^(\d{2}-[A-Z]{3}-\d{2})\s+(\d{2}-[A-Z]{3}-\d{2})\s+(\S+)\s+(.*)",
                    line,
                )
                if match:
                    trans_date, value_date, ref_no, description = match.groups()

                    # Combine multi-line descriptions
                    next_line = lines[i + 1] if i + 1 < len(lines) else ""
                    while (
                        not re.match(r"^\d{2}-[A-Z]{3}-\d{2}", next_line)
                        and i + 1 < len(lines)
                        and not next_line.startswith("* = inclusive of 15% VAT")
                    ):
                        description += " " + next_line.strip()
                        i += 1
                        next_line = lines[i + 1] if i + 1 < len(lines) else ""

                    # Extract amounts and balance
                    amounts = re.findall(r"([\d,]+\.\d{2})(?:\*)?", description)
                    if len(amounts) >= 2:
                        amount = float(amounts[-2].replace(",", ""))
                        balance = float(amounts[-1].replace(",", ""))

                        # Determine transaction type
                        if previous_balance is None:
                            transaction_type = (
                                "Debit" if "STAMP DUTY" in description else "Credit"
                            )
                        else:
                            transaction_type = (
                                "Credit" if balance > previous_balance else "Debit"
                            )

                        # Remove the amounts from the description
                        description = re.sub(
                            r"\s+([\d,]+\.\d{2})(?:\*)?", "", description
                        ).strip()

                        transactions.append(
                            {
                                "TransactionDate": datetime.strptime(
                                    trans_date, "%d-%b-%y"
                                ).strftime("%Y-%m-%d"),
                                "ReferenceNo": ref_no,
                                "TransactionDescription": description,
                                "TransactionAmount": amount,
                                "TransactionTypeDebitCredit": transaction_type,
                                "BookBalance": balance,
                            }
                        )

                        previous_balance = balance

    # Create DataFrame and sort by date in descending order
    df = pd.DataFrame(transactions)
    df["TransactionDate"] = pd.to_datetime(df["TransactionDate"])
    df = df.sort_values("TransactionDate", ascending=False).reset_index(drop=True)

    # Convert TransactionDate back to string format
    df["TransactionDate"] = df["TransactionDate"].dt.strftime("%Y-%m-%d")

    df = df.rename(
        columns={
            "TransactionDate": "transaction_date",
            "TransactionDescription": "transaction_description",
            "TransactionAmount": "transaction_amount",
            "TransactionTypeDebitCredit": "transaction_type",
            "BookBalance": "balance",
        }
    )

    return df


def extract_bank_windhoek_account_statement(pdf_path):
    """
    Extract account statement from Bank Windhoek PDF statement.
    Args:
        pdf_path (str): Path to the PDF file
    Returns:
        pd.DataFrame: Processed transactions with columns:
            - transaction_date (str): YYYY-MM-DD
            - transaction_description (str): Transaction description
            - transaction_amount (float): Transaction amount with 2 decimal places
            - transaction_type (str): 'debit' or 'credit'
            - balance (float): Account balance after transaction
    """
    transactions = []

    def clean_amount(amount_str):
        """Convert amount string to float, handling comma decimals and bracketed negatives"""
        if not amount_str or amount_str.strip() == "":
            return 0.00

        # Remove spaces
        amount_str = amount_str.strip()

        # Handle negative numbers in brackets
        is_negative = False
        if amount_str.startswith("(") and amount_str.endswith(")"):
            is_negative = True
            amount_str = amount_str[1:-1]

        # Convert comma to dot and parse float
        try:
            value = float(amount_str.replace(" ", "").replace(",", "."))
            if is_negative:
                value = -value
            return value
        except ValueError:
            return 0.00

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):

                tables = page.extract_tables()
                if not tables:
                    continue

                table = tables[0]
                start_row = 3 if page_num == 1 else 0

                for row in table[start_row:]:
                    try:
                        # Skip empty rows
                        if not row or all(
                            cell is None or str(cell).strip() == "" for cell in row
                        ):
                            continue

                        # Skip if no date match
                        if not re.match(r"\d{2}-\d{2}-\d{4}", str(row[0]).strip()):
                            continue

                        # Extract values
                        date = str(row[0]).strip()
                        description = (
                            str(row[5]).strip() if len(row) > 5 and row[5] else ""
                        )
                        debit = (
                            str(row[6]).strip() if len(row) > 6 and row[6] else "0,00"
                        )
                        credit = (
                            str(row[7]).strip() if len(row) > 7 and row[7] else "0,00"
                        )
                        balance = (
                            str(row[8]).strip() if len(row) > 8 and row[8] else "0,00"
                        )

                        # Clean amounts
                        debit_amount = abs(
                            clean_amount(debit)
                        )  # Remove negative sign for debits
                        credit_amount = clean_amount(credit)
                        balance_amount = abs(clean_amount(balance))

                        # Create transaction
                        transaction = {
                            "transaction_date": datetime.strptime(
                                date, "%d-%m-%Y"
                            ).strftime("%Y-%m-%d"),
                            "transaction_description": description,
                            "transaction_amount": (
                                debit_amount if debit_amount > 0 else credit_amount
                            ),
                            "transaction_type": (
                                "debit" if debit_amount > 0 else "credit"
                            ),
                            "balance": balance_amount,
                        }

                        transactions.append(transaction)

                    except Exception:
                        continue

            # Create DataFrame
            df = pd.DataFrame(transactions)

            if not df.empty:
                # Sort by date
                df["transaction_date"] = pd.to_datetime(df["transaction_date"])
                df = df.sort_values("transaction_date", ascending=False)
                df["transaction_date"] = df["transaction_date"].dt.strftime("%Y-%m-%d")

                # Format amounts to 2 decimal places
                df["transaction_amount"] = df["transaction_amount"].round(2)
                df["balance"] = df["balance"].round(2)

                return df
            else:
                return pd.DataFrame(
                    columns=[
                        "transaction_date",
                        "transaction_description",
                        "transaction_amount",
                        "transaction_type",
                        "balance",
                    ]
                )

    except Exception as e:
        raise e
