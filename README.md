# Google Wifi Foyer

Experimental Home Assistant custom integration for Google Wifi / Nest Wifi client presence.

It uses Google's undocumented `googlehomefoyer-pa.googleapis.com` API and the
`https://www.googleapis.com/auth/accesspoints` OAuth scope. This may stop working
without notice if Google changes the private API or authentication flow.

## Current v0.4.6 scope

- UI config flow
- EmbeddedSetup `oauth_token` -> reusable `aas_et` master token
- Select one Wifi network per config entry
- Multiple config entries are supported for multiple Wifi networks
- Poll `/v2/groups/<group_id>/stations` every 30 seconds
- Create a modern Home Assistant `ScannerEntity` device tracker for each station
- Online/offline presence
- IP address and DHCP hostname when provided by Google
- Connection type (wired/wireless)
- MAC address through Foyer's optional sensitive-info RPC
- IPv6 address metadata when returned by sensitive info
- Wireless band
- Friendly type/manufacturer metadata
- Offline `last_seen`
- Automatic reauthentication flow if the stored master token stops working
- Create one child device for each access point, named with its room
- Show an IP address diagnostic sensor on each access point device
- Show local firmware, update, uptime, restart, and status diagnostics from each
  access point's `/api/v1/status` endpoint, plus WAN IP on the primary router
- Show a connected-client count and structured client list on each access point
- Show total connected-client and access-point count/list sensors on the network
- Show Family Wi-Fi connected-client, pause, filtering, and schedule information
- Pause and resume Family Wi-Fi groups from Home Assistant switches
- Show the currently prioritized device and prioritization expiry
- Show main and guest SSIDs, guest-network state, and connected guest clients
- Enable and disable the guest network from a Home Assistant switch
- Set each access point's indicator brightness to Off, Low, or High
- Show DHCP-reserved static IP addresses on device trackers

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant and select **Integrations**.
2. Open the menu in the upper-right corner and select **Custom repositories**.
3. Enter `https://github.com/bkflatekvaal/ha-google-wifi-foyer`, select
   **Integration** as the category, and add the repository.
4. Find **Google Wifi Foyer** in HACS and select **Download**.
5. Restart Home Assistant.
6. Go to **Settings -> Devices & services -> Add integration** and select
   **Google Wifi Foyer**.

### Manual installation

Copy `custom_components/google_wifi_foyer` from the
[repository](https://github.com/bkflatekvaal/ha-google-wifi-foyer) to
`/config/custom_components/google_wifi_foyer`, then restart Home Assistant and
add **Google Wifi Foyer** from **Settings -> Devices & services**.

Home Assistant installs the required Python dependencies automatically.

## Getting the oauth_token

1. Open `https://accounts.google.com/EmbeddedSetup` in a browser.
2. Sign in using the Google account that owns/manages the Wifi network.
3. Open browser developer tools.
4. Find the `oauth_token` cookie for `accounts.google.com`.
5. Copy the **entire** value, including the `oauth2_4/` prefix.
6. Paste it into the integration config flow.

The short-lived `oauth_token` is exchanged during setup and is **not stored**.
The resulting reusable `aas_et/...` master token **is stored in the Home Assistant
config entry**, because it is required to refresh access tokens during normal operation.
Protect Home Assistant backups and `.storage` as you would other stored credentials.

## Entity behavior

Google's station API reports current clients with `connected: true`. Offline clients
are normally returned with `status.type = STATION_OFFLINE` and a `lastSeen` timestamp.

All stations returned by Google are created as device trackers, including old/offline
stations. Trackers do not create device-registry entries. Disable unwanted entities
in Home Assistant's entity registry.

## Notes

This integration intentionally uses the unique domain `google_wifi_foyer` rather than
overriding Home Assistant's existing `google_wifi` integration.
