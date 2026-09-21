DOMAIN = "sportabler"

BASE_URL = "https://www.abler.io"
GRAPHQL_URL = f"{BASE_URL}/graphql"

CONF_REFRESH_TOKEN = "refresh_token"

CONF_SCAN_INTERVAL = "scan_interval_minutes"
DEFAULT_SCAN_INTERVAL_MINUTES = 60
SCHEDULE_LOOKAHEAD_DAYS = 30
SCHEDULE_LOOKBACK_DAYS = 1

STATUS_GOING = "G"
STATUS_NOT_GOING = "N"

ATTENDANCE_STATUS_MAP = {
    STATUS_GOING: "going",
    STATUS_NOT_GOING: "not_going",
    None: "not_responded",
}

SERVICE_SET_ATTENDANCE = "set_attendance"
ATTR_CHILD_ID = "child_id"
ATTR_EVENT_ID = "event_id"
ATTR_STATUS = "status"
