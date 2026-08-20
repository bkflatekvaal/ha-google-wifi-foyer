"""Constants for Google Wifi Foyer."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "google_wifi_foyer"

CONF_MASTER_TOKEN = "master_token"
CONF_ANDROID_ID = "android_id"
CONF_GROUP_ID = "group_id"
CONF_NETWORK_NAME = "network_name"

FOYER_BASE_URL = "https://googlehomefoyer-pa.googleapis.com"
FOYER_GRPC_TARGET = "googlehomefoyer-pa.googleapis.com:443"
ACCESSPOINTS_SCOPE = "oauth2:https://www.googleapis.com/auth/accesspoints"
GOOGLE_HOME_APP = "com.google.android.apps.chromecast.app"
GOOGLE_HOME_CLIENT_SIG = "24bb24c05e47e0aefa68a58a766179d9b613a600"

DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
TOKEN_REFRESH_MARGIN_SECONDS = 300

PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR]
