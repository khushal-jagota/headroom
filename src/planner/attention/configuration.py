"""Defaults for historical attention reads."""

TIMEZONE = "Europe/Berlin"
DEFAULT_DAYS = 7
DEFAULT_LIMIT = 50
SORT_FIELDS = ("owner_messages", "owner_approvals", "approval_wait_seconds")
ATTENTION_SOURCE_ACCOUNT = "panels-attention-history"
COLLECTION_LOCAL_TIME = "00:05"
DAILY_METRICS = ("owner_messages", "owner_approvals", "approval_wait_seconds")
COLLECTOR_PAGE_LIMIT = 50
