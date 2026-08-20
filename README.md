# Google Wifi Foyer

Experimental Home Assistant custom integration for Google Wifi / Nest Wifi client presence.

It uses Google's undocumented `googlehomefoyer-pa.googleapis.com` API and the
`https://www.googleapis.com/auth/accesspoints` OAuth scope. This may stop working
without notice if Google changes the private API or authentication flow.

## Current v0.1.5 scope

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
- Show an access point count/list diagnostic sensor on the Wifi network device

## Installation

Copy:

`custom_components/google_wifi_foyer`

to:

`/config/custom_components/google_wifi_foyer`

Restart Home Assistant, then add **Google Wifi Foyer** from
**Settings -> Devices & services -> Add integration**.

Home Assistant installs the Python dependency `gpsoauth==2.0.0` automatically.

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
