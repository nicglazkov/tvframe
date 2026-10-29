# Running TV Frame without Docker

Most people should use `./tvframe setup` (see the [README](../README.md#install)), which runs everything in Docker. This page is the other way: the services as `systemd --user` units straight on a Linux server, with only the photo scorer and image conversion in containers. It is how the first TV Frame server runs, and it suits a server you already look after by hand.

The systemd units expect the checkout at `~/tvframe`. Everything else finds its way from wherever the checkout is.

## 1. Server

Requirements: Python 3.12 with Pillow and NumPy, `ffmpeg`, `exiftool`, `adb`, and Docker. An NVIDIA GPU with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) makes builds fast; without one, put `TVFRAME_ENCODER=cpu` and `TVFRAME_DOCKER_GPU=none` in `config.env`. Node 18 or later is only for the screenshot tools.

On Ubuntu 24.04:

```bash
sudo apt install python3-pil python3-numpy ffmpeg libimage-exiftool-perl adb
```

```bash
git clone https://github.com/nicglazkov/tvframe ~/tvframe
cd ~/tvframe

# settings for this home
mkdir -p ~/.config/tvframe
cp config.example.env ~/.config/tvframe/config.env && chmod 600 ~/.config/tvframe/config.env
$EDITOR ~/.config/tvframe/config.env

# containers for image conversion and scoring
docker build -t tvframe-media:heif -f tools/Dockerfile.media tools
docker build -t tvframe-curate:local curate
```

Home Assistant is optional. To use it, put a long-lived access token in `~/.config/tvframe/ha_token` (chmod 600) and copy `home_entities.example.json` to `~/.config/tvframe/home_entities.json` with your own entity ids.

The scoring step needs two model files in `curate/models/` (the OpenCLIP weights download there on their own the first time):

```bash
mkdir -p curate/models && cd curate/models
curl -LO https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
curl -LO "https://github.com/christophschuhmann/improved-aesthetic-predictor/raw/main/sac+logos+ava1-l14-linearMSE.pth"
cd ../..
```

## 2. Photos

Put photos and videos in folders under `~/tvframe/library/`, one folder per group, then build everything once:

```bash
mkdir -p ~/tvframe/library/Trips
cp ~/Pictures/trip/*.jpg ~/tvframe/library/Trips/
tools/library_sync.sh
```

The first build reads every file, so it takes a while. Later builds only redo what changed.

Shoot S-Log3 video on a Sony camera? Put Sony's S-Log3 (S-Gamut3.Cine) to Rec.709 LUT, as a 33-point `.cube` file, at `tools/slog3_709.cube` and ungraded clips get it applied. Without it they play as shot. To pull from Google Photos shared albums instead, see [`ingest/README.md`](../ingest/README.md).

## 3. Services

```bash
cp deploy/systemd/tvframe-* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now tvframe-photos tvframe-tune tvframe-bridge tvframe-library tvframe-watchdog.timer
loginctl enable-linger "$USER"        # keep them running when you are logged out

docker compose -f deploy/site.compose.yml up -d     # the photo manager on :8150

# weather, air quality and headlines for Glance, every 20 minutes
( crontab -l; echo "*/20 * * * * python3 $HOME/tvframe/tools/fetch_live.py >> $HOME/tvframe/real/live.log 2>&1" ) | crontab -
```

Open `http://<server>:8150/` to see the photo manager.

Settings worth knowing for this way of running: `TVFRAME_ENCODER` (`auto`, `nvenc` or `cpu`), `TVFRAME_DOCKER_GPU` (the `docker run` flags that reach the GPU; the default is `--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all`, many hosts want `--gpus all`, and `none` runs the scorer on the CPU) and `TVFRAME_QUIET_HOME=0` to leave the Google TV home alone. All of them go in `~/.config/tvframe/config.env`.

## 4. The TV app

On the TV, turn on Developer options and USB debugging (Settings > System > About, select Android TV OS build seven times; then Settings > System > Developer options). Then, with the Android SDK and a JDK 17 on the server:

```bash
cd ~/tvframe/android
cat >> local.properties <<EOF
sdk.dir=$HOME/android/sdk
tvframe.server=http://192.168.1.20
EOF
tools/make-key.sh                  # once; keep android/keystore/ private and backed up
./gradlew assembleRelease

cd .. && tools/deploy.sh --install # install, make it the screensaver, set the home screen
```

`tools/deploy.sh` writes down every TV setting it is about to change, the first time it runs. `tools/rollback.sh` puts them all back, and `tools/rollback.sh --uninstall` removes the app as well.

`tvframe.server` is only the default. It can be changed later in the app's settings. Add `tvframe.tz=Area/City` if the TV's own time zone is wrong.
