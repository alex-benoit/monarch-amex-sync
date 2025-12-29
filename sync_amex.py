#!/usr/bin/env python3
"""
Sync additional Amex card transactions in Monarch Money to the main Amex card.

Behavior:
- Fetches all transactions for the main and additional Amex accounts.
- Only considers additional-card transactions that DO NOT already have the sync tag.
- Matching key: (date, amount, merchant name).
- For each additional-card transaction:
  1) If there is at least one matching main-card transaction already marked SHARED:
       - Only add the sync tag to the additional-card transaction (respecting your manual edit).
  2) Otherwise:
       - Mark all matching main-card transactions as SHARED.
       - Then add the sync tag to the additional-card transaction.

Configuration is via environment variables (see README.md).
"""

import os
import sys
from typing import Dict, Any, List, Tuple

import requests
from dotenv import load_dotenv


MONARCH_GRAPHQL_URL = "https://api.monarch.com/graphql"

# Load environment variables from a .env file if present
load_dotenv()

# Required env vars
MAIN_ACCOUNT_ID = os.getenv("MAIN_ACCOUNT_ID")              # main Amex account ID
ADDL_ACCOUNT_ID = os.getenv("ADDL_ACCOUNT_ID")              # additional Amex account ID
# Monarch web uses "Authorization: Token <token_value>"
API_TOKEN = os.getenv("MONARCH_API_TOKEN") or os.getenv("MONARCH_BEARER_TOKEN")

# Optional env vars
SYNC_TAG_NAME = os.getenv("SYNC_TAG_NAME", "synced")        # name of the tag to apply on additional card
DRY_RUN = os.getenv("DRY_RUN", "true").lower() != "false"   # default: dry-run enabled unless explicitly set to "false"


session = requests.Session()
session.headers.update(
    {
        # match the browser: "Authorization: Token <token>"
        "authorization": f"Token {API_TOKEN}" if API_TOKEN else "",
        "content-type": "application/json",
        # optional, but helps mimic the web client
        "client-platform": "web",
        "monarch-client": "monarch-core-web-app-graphql",
    }
)


def gql(query: str, variables: Dict[str, Any], operation_name: str | None = None) -> Dict[str, Any]:
    """Execute a GraphQL query/mutation against Monarch."""
    payload: Dict[str, Any] = {"query": query, "variables": variables}
    if operation_name:
        payload["operationName"] = operation_name

    resp = session.post(MONARCH_GRAPHQL_URL, json=payload)
    # Helpful debug on schema / auth errors
    if not resp.ok:
        print("GraphQL request failed:", resp.status_code)
        try:
            print("Response JSON:", resp.json())
        except Exception:
            print("Response text:", resp.text)
        resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def fetch_transactions(account_id: str) -> List[Dict[str, Any]]:
    """
    Fetch all transactions for a given account.

    Uses the real Web_GetTransactionsList query observed from the Monarch web app.
    """
    query = """
    query Web_GetTransactionsList(
      $offset: Int,
      $limit: Int,
      $filters: TransactionFilterInput,
      $orderBy: TransactionOrdering
    ) {
      allTransactions(filters: $filters) {
        totalCount
        totalSelectableCount
        results(offset: $offset, limit: $limit, orderBy: $orderBy) {
          id
          amount
          pending
          date
          hideFromReports
          hiddenByAccount
          plaidName
          notes
          isRecurring
          reviewStatus
          needsReview
          isSplitTransaction
          dataProviderDescription
          attachments {
            id
            __typename
          }
          goal {
            id
            name
            __typename
          }
          savingsGoalEvent {
            id
            goal {
              id
              name
              __typename
            }
            __typename
          }
          category {
            id
            name
            icon
            group {
              id
              type
              __typename
            }
            __typename
          }
          merchant {
            name
            id
            transactionsCount
            logoUrl
            recurringTransactionStream {
              frequency
              isActive
              __typename
            }
            __typename
          }
          tags {
            id
            name
            color
            order
            __typename
          }
          account {
            id
            displayName
            icon
            logoUrl
            __typename
          }
          ownedByUser {
            id
            displayName
            profilePictureUrl
            __typename
          }
          __typename
        }
        __typename
      }
      transactionRules {
        id
        __typename
      }
    }
    """

    offset = 0
    limit = 200
    results: List[Dict[str, Any]] = []

    while True:
        variables = {
            "offset": offset,
            "limit": limit,
            "orderBy": "date",
            "filters": {
                "accounts": [account_id],
                "transactionVisibility": "all_transactions",
            },
        }
        data = gql(query, variables, operation_name="Web_GetTransactionsList")["allTransactions"]
        batch = data["results"]

        # Normalize shape to what the rest of the script expects
        for t in batch:
            normalized = {
                "id": t["id"],
                "date": t["date"],
                "amount": t["amount"],
                # use plaidName or notes as a description-ish field
                "description": t.get("plaidName") or t.get("notes") or "",
                # derive owner status from ownedByUser: None => SHARED, otherwise INDIVIDUAL
                "owner": "SHARED" if not t.get("ownedByUser") else "INDIVIDUAL",
                "merchant": t.get("merchant"),
                "tags": t.get("tags") or [],
            }
            results.append(normalized)

        total = data.get("totalSelectableCount") or data.get("totalCount") or 0
        offset += len(batch)
        if offset >= total or not batch:
            break

    return results


def ensure_tag_id(name: str) -> str:
    """
    Return the tag ID for the given name.

    Uses the real GetHouseholdTransactionTags query (same as the unofficial client)
    and reads from householdTransactionTags. Does NOT create tags; create the
    tag once in the UI (e.g. "synced") and then reference it here.
    """
    query = """
    query GetHouseholdTransactionTags(
      $search: String,
      $limit: Int,
      $bulkParams: BulkTransactionDataParams
    ) {
      householdTransactionTags(
        search: $search,
        limit: $limit,
        bulkParams: $bulkParams
      ) {
        id
        name
        color
        order
        transactionCount
        __typename
      }
    }
    """

    variables: Dict[str, Any] = {
        "search": None,
        "limit": 500,
        "bulkParams": None,
    }
    data = gql(query, variables, operation_name="GetHouseholdTransactionTags")
    tags = data.get("householdTransactionTags") or []
    for t in tags:
        if t["name"].lower() == name.lower():
            return t["id"]

    raise RuntimeError(
        f"Tag '{name}' not found in householdTransactionTags. "
        f"Please create it in Monarch first (e.g. as a transaction tag) and rerun."
    )


def set_owner_shared(txn_id: str) -> None:
    """Set a transaction to shared ownership by clearing ownerUserId."""
    mutation = """
    mutation Web_UpdateTransactionOverview($input: UpdateTransactionMutationInput!) {
      updateTransaction(input: $input) {
        transaction {
          id
          __typename
        }
        errors {
          fieldErrors {
            field
            messages
            __typename
          }
          message
          code
          __typename
        }
        __typename
      }
    }
    """
    variables: Dict[str, Any] = {
        "input": {
            "id": txn_id,
            # In Monarch, shared ownership is represented as ownerUserId = null
            "ownerUserId": None,
        }
    }
    gql(mutation, variables, operation_name="Web_UpdateTransactionOverview")


def update_tags_replace(txn_id: str, tag_ids: List[str]) -> None:
    """
    Replace the set of tags on a transaction with the provided tag IDs.

    Uses the real Web_SetTransactionTags mutation (same as the unofficial client).
    """
    mutation = """
    mutation Web_SetTransactionTags($input: SetTransactionTagsInput!) {
      setTransactionTags(input: $input) {
        errors {
          fieldErrors {
            field
            messages
            __typename
          }
          message
          code
          __typename
        }
        transaction {
          id
          tags {
            id
            name
            __typename
          }
          __typename
        }
        __typename
      }
    }
    """
    variables: Dict[str, Any] = {
        "input": {
            "transactionId": txn_id,
            "tagIds": tag_ids,
        }
    }
    gql(mutation, variables, operation_name="Web_SetTransactionTags")


def has_sync_tag(txn: Dict[str, Any], sync_tag_name: str) -> bool:
    """Return True if the transaction already has the sync tag."""
    for t in txn.get("tags") or []:
        if t["name"].lower() == sync_tag_name.lower():
            return True
    return False


def key_for(txn: Dict[str, Any]) -> Tuple[str, float, str]:
    """Return the (date, amount, merchant_name_lower) matching key for a transaction."""
    merchant_name = (txn.get("merchant") or {}).get("name") or ""
    return (
        txn["date"],
        txn["amount"],
        merchant_name.strip().lower(),
    )


def main() -> None:
    # Basic env validation
    missing = [
        name
        for name, value in [
            ("MAIN_ACCOUNT_ID", MAIN_ACCOUNT_ID),
            ("ADDL_ACCOUNT_ID", ADDL_ACCOUNT_ID),
            ("MONARCH_API_TOKEN (or MONARCH_BEARER_TOKEN)", API_TOKEN),
        ]
        if not value
    ]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    print("Fetching transactions from Monarch...")
    main_txns = fetch_transactions(MAIN_ACCOUNT_ID)
    addl_txns = fetch_transactions(ADDL_ACCOUNT_ID)
    tag_id = ensure_tag_id(SYNC_TAG_NAME)

    # Build lookup for main card by (date, amount, merchant_name)
    main_index: Dict[Tuple[str, float, str], List[Dict[str, Any]]] = {}
    for t in main_txns:
        k = key_for(t)
        main_index.setdefault(k, []).append(t)

    # Only additional-card transactions without the sync tag
    addl_candidates = [t for t in addl_txns if not has_sync_tag(t, SYNC_TAG_NAME)]
    print(f"Additional-card transactions without '{SYNC_TAG_NAME}' tag: {len(addl_candidates)}")

    count_synced_existing_shared = 0
    count_synced_new_shared = 0
    count_no_match = 0

    for addl in addl_candidates:
        k = key_for(addl)
        mains = main_index.get(k, [])

        merchant_name = (addl.get("merchant") or {}).get("name") or ""
        desc = addl.get("description") or ""

        if not mains:
            print(f"[NO MATCH] {addl['date']} ${addl['amount']} merch='{merchant_name}' desc='{desc}'")
            count_no_match += 1
            continue

        # 1) If any main card transaction is already SHARED, only tag the additional card
        shared_mains = [m for m in mains if (m.get("owner") or "").upper() == "SHARED"]

        if shared_mains:
            print(f"[EXISTING SHARED] {addl['date']} ${addl['amount']} merch='{merchant_name}' desc='{desc}'")
            if DRY_RUN:
                print("  DRY RUN: would add sync tag to additional-card txn only (main already SHARED)")
            else:
                existing_tag_ids = [t["id"] for t in (addl.get("tags") or [])]
                if tag_id not in existing_tag_ids:
                    new_tag_ids = existing_tag_ids + [tag_id]
                    update_tags_replace(addl["id"], new_tag_ids)
                print("  updated: tagged additional-card txn as synced")
            count_synced_existing_shared += 1
            continue

        # 2) Otherwise, mark main as SHARED and then sync-tag the additional card
        print(f"[NEW SHARED] {addl['date']} ${addl['amount']} merch='{merchant_name}' desc='{desc}'")
        if DRY_RUN:
            print(
                "  DRY RUN: would set owner=SHARED on main-card match(es) "
                "and tag additional-card txn as synced"
            )
        else:
            for m in mains:
                set_owner_shared(m["id"])
            existing_tag_ids = [t["id"] for t in (addl.get("tags") or [])]
            if tag_id not in existing_tag_ids:
                new_tag_ids = existing_tag_ids + [tag_id]
                update_tags_replace(addl["id"], new_tag_ids)
            print("  updated: main-card owner=SHARED and additional-card tagged as synced")
        count_synced_new_shared += 1

    print("\nSummary:")
    print(f"  Existing SHARED matches synced: {count_synced_existing_shared}")
    print(f"  New SHARED set & synced:        {count_synced_new_shared}")
    print(f"  No match in main card:          {count_no_match}")
    if DRY_RUN:
        print("DRY RUN was enabled; no real changes were made.")


if __name__ == "__main__":
    main()


