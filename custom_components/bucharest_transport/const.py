"""Constants shared by the integration."""

DOMAIN = "bucharest_transport"
NAME = "Bucharest Public Transport"
VERSION = "0.1.0"
STATIC_URL = "https://gtfs.tpbi.ro/regional/BUCHAREST-REGION.zip"
FEED_URLS = {
    name: f"https://gtfs.tpbi.ro/api/gtfs-rt/{name}"
    for name in ("vehiclePositions", "tripUpdates", "serviceAlerts")
}
POLL_SECONDS = 30
ALERT_SECONDS = 300
STATIC_SECONDS = 21600
MAX_AGE = 90
ALERT_MAX_AGE = 600
FUTURE_SKEW = 60
CARD_URL = "/bucharest_transport/bucharest-transport-card.js"
