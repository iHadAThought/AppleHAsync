"""Constants for apple_hasync."""

DOMAIN = "apple_hasync"
CONF_AGENT_URL = "agent_url"
CONF_AGENT_TOKEN = "agent_token"
CONF_VERIFY_TLS = "verify_tls"
CONF_CA_PATH = "ca_path"
CONF_CERT_PIN = "cert_pin"
CONF_ALLOW_INSECURE_HTTP = "allow_insecure_http"
CONF_BACKEND = "backend"
CONF_SELECTED_CALENDARS = "selected_calendars"
CONF_SELECTED_LISTS = "selected_lists"
CONF_WEBHOOK_SECRET = "webhook_secret"
CONF_SCAN_INTERVAL = "scan_interval"

BACKEND_LOCAL_AGENT = "local_agent"
BACKEND_CALDAV = "caldav"

DEFAULT_SCAN_INTERVAL = 30
DEFAULT_VERIFY_TLS = True

PLATFORMS = ["calendar", "todo"]
