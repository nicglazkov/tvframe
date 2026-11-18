# Changelog

## Unreleased

- A headline from a feed is labeled with the feed's full name ("NBC Bay Area", not "Local – NBC").

## 0.4.4

- **Long headlines show whole.** Instead of being skipped or cut off with "...", a long headline is set smaller, on up to four lines in the same space.

## 0.4.3

- **Headlines by level.** `TVFRAME_NEWS_FEEDS` lists a feed for each level of news you want, for example your town, your state, the country and the world, and Glance shows one headline at a time from each in turn, labeled with its level and source. Google News shortcuts cover local, national and world news. See [Headlines by level](docs/GUIDE.md#headlines-by-level).
- Headlines leave out questions, advice pieces and lists ("7 ways to..."), opinion, and titles too long to fit, whatever the feed.
- A comment after a value in `config.env` (`TVFRAME_MAPS=on   # ...`) no longer turns that setting off.

## 0.4.2

- **Guest Wi-Fi when it is needed.** The guest Wi-Fi card follows a Home Assistant entity that says whether guests are over (`"guests"` in `home_entities.json`), and **Up** on the remote brings it up for 5 minutes in any case. `TVFRAME_GUEST_WIFI` picks `auto`, `always` or `button`. While it is away, its place cycles through the headlines (when they are on). See [Guest Wi-Fi card](docs/GUIDE.md#guest-wi-fi-card), which also documents the card's file for the first time.
- Glance picks up new live data every 2 minutes instead of 5.
- **Memory.** The TV app's native memory grew by about 200 MB a day while the screensaver ran, until the Streamer ran short and swapped. The app now starts itself over when it has grown past 600 MB and nothing of it is on screen, and it logs its size with every slide while the cause is tracked down.

## 0.4.1

- **Online extras are off until you turn them on.** Weather and air quality (Open-Meteo), headlines (NPR), place names (OpenStreetMap) and satellite maps (Esri) each have an on/off line in `config.env`, and `setup` asks about each one. Off, TV Frame never contacts that service. See [Online extras](docs/GUIDE.md#online-extras).
- Glance's rotating tile skips air quality, headlines and sun times when there is nothing to show, instead of saying "No reading yet".
- The README says plainly that this is a personal project shared as is: no support, no promised updates, use at your own risk.

## 0.4.0

The first public release.

- **Downloads instead of builds.** `./tvframe setup` downloads the server image and `./tvframe tv` the signed TV app, so a new install takes minutes. Both still build on your computer when there is nothing to download, or with `TVFRAME_IMAGE=tvframe` and `TVFRAME_APK_URL=build` in `.env`.
- **MIT license.**
- Guests mode, which the TV stopped using in October, is gone from the app and the server. Glance is the only screen. The bridge no longer serves `/mode`.

## 0.3.10

- The quiet home screen shows every TV app you have installed: well-known ones first with their own colors, the rest by name. The app list no longer comes from one household.
- The README is short again. The details moved to [docs/GUIDE.md](docs/GUIDE.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Checks pass on every push again (a shell script used a test plain `sh` does not have), on current GitHub Actions versions.

## 0.3.9

- **Install on any computer with Docker.** `./tvframe setup` sets up the server, `./tvframe tv` puts the app on the TV. One container holds every service and the scoring models.
- **No GPU needed.** Without an NVIDIA card, videos and TV stills are encoded with x265, x264 and SVT-AV1 and photos are scored on the CPU. Same output, slower builds.
- **One app for every home.** The server's address is set after the app is installed, so a released app can be downloaded instead of built.
- The quiet home screen is optional.
- Docs: supported devices, running without Docker, releasing.
- Fixed: after switching between Photo ranking and Clip ranking, hover previews, Trim and Crop did nothing until the page was reloaded.
- Fixed: a build that failed is tried again when the server restarts, instead of waiting for the next change in the photo folder.
- The README is rewritten in depth, with new screenshots from a test install.

## 0.3.8

- Clips end on their last frame and fade out, instead of starting a third play and stopping.
- Photo manager: hover to preview photos and clips, select many at once, pick how each photo is framed on the TV, trim the part of a clip the TV plays.
