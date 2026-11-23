"""Run one resumable pass of account-deletion purges.

Use this as a scheduled job on hosts where an in-process daemon may sleep or
restart. Provider cancellation is rechecked by the application before purge.
"""

import sys

import account_store
from app import _purge_due_account_deletions


def main():
    try:
        account_store.initialize()
        due_count = len(account_store.due_deletion_user_ids(limit=25))
        purged_count = _purge_due_account_deletions(limit=25)
    except Exception as error:
        print(f"Account deletion purge failed ({type(error).__name__}).", file=sys.stderr)
        return 1

    deferred_count = max(0, due_count - purged_count)
    print(f"Purge pass complete: purged={purged_count} deferred_or_remaining={deferred_count}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
