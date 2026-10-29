# Changelog

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
