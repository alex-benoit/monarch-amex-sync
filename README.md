## Monarch Amex Sync Script

This small helper script keeps your **Monarch Money** Amex accounts in sync when you
have a **main** Amex card and an **additional cardholder** Amex card, and Monarch is showing
both transactions on the main card.

### What it does

- **Reads all transactions** from:
  - your **main Amex** Monarch account, and
  - your **additional Amex** Monarch account.
- **Only looks at additional-card transactions that do _not_ already have a sync tag**.
- Uses a **matching key** of:
  - **date**
  - **amount**
  - **merchant name** (case-insensitive)
- For each additional-card transaction without the sync tag:
  - **If there is a matching main-card transaction already marked as `SHARED`**:
    - Only adds the sync tag on the additional-card transaction
      (this respects manual edits you may have done in the UI).
  - **Otherwise**:
    - Marks the matching main-card transaction(s) as **`SHARED`**.
    - Then adds the **sync tag** to the additional-card transaction.

By default it runs in **dry-run** mode and prints what it would do without making
any changes.

### Files

- `sync_amex.py` – main script.
- `pyproject.toml` – project configuration (for `uv`).

### Setup (using uv)

```bash
uv sync
```

This will create and manage an isolated environment for you.

### Required environment variables

Create a `.env` in the project root directory with:

- **`MONARCH_BEARER_TOKEN`**: your Monarch web bearer token
  - You can grab this from your browser dev tools while using Monarch:
    - Open the **Network** tab.
    - Find any **GraphQL** request made by the app.
    - Copy the `Authorization: Token ...` request header value.
- **`MAIN_ACCOUNT_ID`**: Monarch account ID for the **main Amex** card.
  - You can grab this from the browser url when visiting the right account from the accounts tab. Copy the id from the url that will look like: https://app.monarch.com/accounts/details/123456789.
- **`ADDL_ACCOUNT_ID`**: Monarch account ID for the **additional Amex** card.
  - Get this from the account details URL (same as above) for the additional card holder account. **Note: Please keep the account connected to your Monarch account, but feel free to hide account, exclude from account balance, hide transactions, and exclude account from debt paydown in the account's settings to avoid duplicate transactions from messing up other parts of Monarch.**

### Optional environment variables

- **`SYNC_TAG_NAME`** (default: `synced`):
  - Name of the tag applied to additional-card transactions that have been synced.
  - The script will **create this tag** if it does not exist yet.
- **`DRY_RUN`** (default: `"true"`):
  - Any value other than the string `"false"` means **dry-run is ON**.
  - Set to `"false"` to perform **real updates**.

### Example `.env`

You can use something like:

```bash
MONARCH_BEARER_TOKEN="xxxxyyyyzzzz"
MAIN_ACCOUNT_ID="123456789"
ADDL_ACCOUNT_ID="123456789"
SYNC_TAG_NAME="synced"   # optional
DRY_RUN="true"           # optional
```

### Running with uv

Dry-run first (recommended):

```bash
uv run sync_amex.py
```

If the output looks correct, run it for real (either update `.env` or override via env var):

```bash
DRY_RUN=false uv run sync_amex.py
```

### Notes / Adjustments

- In this case, I am using the main Amex card as someone's card, and the additional
  card as a joint card - therefore this script detects the duplicates and marks the
  owner of the duplicate cards in the main card as "Shared". If you have a different
  setup, you can modify this script.
- If Monarch’s GraphQL schema differs slightly (e.g. different field names for
  `merchant`, `owner`, or `tags`), you can:
  - Open dev tools ➜ Network tab ➜ inspect a **transaction** GraphQL response.
  - Adjust the fields in `sync_amex.py` (the `fetch_transactions` query and
    the mutations) to match exactly.
- Matching is done strictly on **(date, amount, merchant name)**. If you want
  to tighten it further (e.g. compare description/merchant IDs), you can extend
  the key easily.
