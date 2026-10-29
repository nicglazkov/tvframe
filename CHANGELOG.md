# Changelog

## 0.3.9

- **Install on any computer with Docker.** `./tvframe setup` sets up the server, `./tvframe tv` puts the app on the TV. One container holds every service and the scoring models.
- **No GPU needed.** Without an NVIDIA card, videos and TV stills are encoded with x265, x264 and SVT-AV1 and photos are scored on the CPU. Same output, slower builds.
- **One app for every home.** The server's address is set after the app is installed, so a released app can be downloaded instead of built.
- The quiet home screen is optional.
- Docs: supported devices, running without Docker, releasing.

## 0.3.8

- Clips end on their last frame and fade out, instead of starting a third play and stopping.
- Photo manager: hover to preview photos and clips, select many at once, pick how each photo is framed on the TV, trim the part of a clip the TV plays.
