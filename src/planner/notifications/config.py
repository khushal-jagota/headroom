"""Native delivery settings and bounds. Provider secrets stay in the environment."""

from collections.abc import Mapping

from planner.notifications.contracts import APNsConfig

APNS_BATCH_SIZE = 50
APNS_DELIVERY_TTL_SECONDS = 300
APNS_MAX_ATTEMPTS = 5
APNS_RETRY_BASE_SECONDS = 30
APNS_TIMEOUT_SECONDS = 10
APNS_JWT_LIFETIME_SECONDS = 3000
APNS_PAYLOAD_MAX_BYTES = 4096
NOTIFICATION_LABEL_MAX_CHARS = 60


def load_apns_config(env: Mapping[str, str]) -> APNsConfig | None:
    key_path = env.get("PLAN_APNS_KEY_PATH", "").strip()
    key_id = env.get("PLAN_APNS_KEY_ID", "").strip()
    team_id = env.get("PLAN_APNS_TEAM_ID", "").strip()
    bundle_ids = tuple(
        item.strip() for item in env.get("PLAN_APNS_BUNDLE_IDS", "").split(",") if item.strip()
    )
    if not (key_path and key_id and team_id and bundle_ids):
        return None
    return APNsConfig(key_path, key_id, team_id, bundle_ids)
