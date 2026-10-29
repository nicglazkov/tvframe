# Devices

TV Frame has two halves: the app on the TV, and the server that prepares the photos. Each table says how sure we are.

- **Tested:** run end to end, photos and clips on screen.
- **Expected to work:** meets every requirement below, not tried yet. Reports welcome.
- **Not supported:** missing something the app needs.

## The TV

The app needs Android TV OS 12 or later (API 31), a 4K HEVC Main 10 video decoder for the 4K stills, and Developer options with USB or wireless debugging so it can be installed and set as the screensaver.

| Device | Android TV | Status | Notes |
| --- | --- | --- | --- |
| Google TV Streamer (2024) | 14 | **Tested**, runs every day | What TV Frame is built on. |
| Google TV emulator, Android Studio | 14 (API 34) | **Tested** | The install path and the app. No 4K output. |
| Chromecast with Google TV (4K) | 12 and later | Expected to work | Same platform as the Streamer, slower chip: the first photos load a little later. |
| Chromecast with Google TV (HD) | 12 and later | Expected to work | Outputs 1080p, so you get 1080p stills, not 4K. |
| onn 4K and onn 4K Pro (Walmart) | 12 | Expected to work | |
| Xiaomi TV Box S (2nd gen) | 12 | Expected to work | |
| TVs with Google TV built in (Sony, TCL, Hisense and others) | 12 or later | Expected to work on Android TV 12+ | Many sets from before 2023 are still on Android TV 10 or 11. Check Settings > System > About. |
| Nvidia Shield TV | 11 | Not supported | Android TV 11 is below the minimum. |
| Amazon Fire TV | Fire OS (Android 9 or 11) | Not supported | Below the minimum. |
| Roku, Apple TV, Samsung Tizen, LG webOS | none | Not supported | Not Android. |

The optional quiet home screen uses an accessibility service to step in when the Home button is pressed. It works on the Streamer and the emulator. Other devices may handle the Home button their own way; if it misbehaves, run `./tvframe tv` again and answer no to the quiet home screen.

## The server

Any computer that stays on and runs Docker. A GPU only changes how long a build takes.

| Server | Status | Notes |
| --- | --- | --- |
| Linux, NVIDIA GPU (driver 570 or later) | **Tested** (Ubuntu 24.04, RTX 5060 Ti) | Scoring on the GPU, video on NVENC. `./tvframe setup` offers to install the NVIDIA Container Toolkit if it is missing. |
| Linux, no GPU or an AMD or Intel GPU | **Tested** (CPU mode) | Everything runs on the CPU: x265, x264 and SVT-AV1 for video, PyTorch on the CPU for scoring. Same results, slower builds. |
| Windows 11 with WSL 2 and Docker Desktop, NVIDIA GPU | Expected to work | Run `./tvframe setup` inside the Ubuntu WSL shell. Docker Desktop passes the GPU through. |
| macOS with Docker Desktop | Expected to work, CPU mode | Docker on a Mac cannot reach the GPU. Fine for a small library. |
| Raspberry Pi and other ARM computers | Not tried | The image is built for x86-64 only today. |

What a server needs:

- **Memory:** 8 GB. The photo scorer uses about 3 GB while it runs.
- **Disk:** about 12 GB for the server image (3 GB with the CPU image), plus roughly a third of the library's size again for the TV copies and web images.
- **Network:** the TV reaches the server on ports 8151 (photos) and 8154 (weather and home), your phone on 8150 (the photo manager). Keep them on the same network.

How long a first build takes, measured on a test library of 40 stock photos and 2 short 1080p clips, on the same computer (Core i7-12700K, RTX 5060 Ti):

| | First build |
| --- | --- |
| With the GPU | 4 min 42 s |
| CPU only | 5 min 15 s (2 min 31 s of it encoding video) |

Most of the time on a small library is fixed work (loading the models, building the playlist). The gap grows with video: each minute of 4K clips takes several minutes to encode on the CPU, and seconds with NVENC. Later builds only redo what changed.
