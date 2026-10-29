<h1 align="center">TV Frame</h1>

<p align="center">
  <b>Your own photos and clips on the living room TV, in true 4K, with the time, the weather and the house at a glance.</b><br>
  An Android TV screensaver and home screen, a small home server that prepares the photos, and a web page to run it all from your phone.
</p>

<p align="center">
  <img src="docs/screenshots/glance.jpg" alt="The Glance screen: a full-screen landscape photo with the clock, weather, home and headline tiles along the bottom" width="100%">
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="docs/GUIDE.md">Guide</a> ·
  <a href="docs/DEVICES.md">Devices</a> ·
  <a href="docs/GUIDE.md#troubleshooting">Troubleshooting</a> ·
  <a href="docs/ARCHITECTURE.md">How it works</a>
</p>

---

## What it does

- **Real 4K.** Google TV draws its interface at 1080p. TV Frame plays each photo as a one-frame video instead, so the picture is a full 3840 x 2160 with crisp text on top.
- **Glance.** When the TV sits idle it fills with your photos, with the clock, today's weather, your home and one rotating tile: golden hour, air quality, a headline, or a photo from this week years ago.
- **Clips that never get cut off.** Short videos mix in, start on their best moment, loop seamlessly two or three times and always finish on their last frame.
- **Details on demand.** Press OK on the remote for where and when, the camera and lens, the exposure, and a satellite map of the spot.
- **A quiet home screen**, if you want it: your apps over a photo, no ads, with the Google TV home one click away.
- **A photo manager on your phone.** Folders become groups on the TV. Every photo is scored and near-duplicates are grouped. Hide, boost, trim clips and choose framing, for many at once.
- **Stays at home.** No account and no cloud. Your photos never leave your network.

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/details.jpg" alt="The details panel: title, date and photo credit over a darkened photo"></td>
    <td width="50%"><img src="docs/screenshots/home-screen.jpg" alt="The quiet home screen: apps over a full-screen photo"></td>
  </tr>
  <tr>
    <td>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/manager-rank-dark.png">
        <img src="docs/screenshots/manager-rank.png" alt="Photo ranking with three photos selected and a bar of actions for the selection">
      </picture>
    </td>
    <td>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/manager-trim-dark.png">
        <img src="docs/screenshots/manager-trim.png" alt="The trim dialog: the clip with handles for the part the TV plays">
      </picture>
    </td>
  </tr>
</table>

<sub>More screenshots in the <a href="docs/GUIDE.md">guide</a>. All of them use stock photos from <a href="https://unsplash.com">Unsplash</a> and clips from <a href="https://commons.wikimedia.org">Wikimedia Commons</a> (CC BY 3.0).</sub>

## Install

You need two things on the same home network:

- **A Google TV or Android TV 12+ device.** Built and tested on the Google TV Streamer; see [docs/DEVICES.md](docs/DEVICES.md) for others.
- **A computer that stays on**, running Linux with Docker. An NVIDIA graphics card makes builds fast but is optional. 8 GB of memory and 25 GB of free disk is enough to start.

### 1. The server

```bash
git clone https://github.com/nicglazkov/tvframe ~/tvframe
cd ~/tvframe
./tvframe setup
```

<p align="center"><img src="docs/screenshots/setup.png" alt="./tvframe setup in a terminal: Docker found, NVIDIA GPU found, four questions answered, the server running, and the photo manager's address" width="760"></p>

`setup` checks Docker and your graphics card (and offers to install what is missing), asks where your photos are, your town for the weather, your time zone and this computer's address, then downloads and starts the server. The first time takes a few minutes. It ends by printing the photo manager's address, `http://<this computer>:8150/`. Open it on your phone.

### 2. The TV

On the TV, once:

1. **Settings > System > About**, then select **Android TV OS build** seven times.
2. **Settings > System > Developer options**, then turn on **USB debugging**.
3. Note the TV's address under **Settings > Network & Internet**.

Then on the computer:

```bash
./tvframe tv
```

Enter the TV's address, and on the TV choose **Allow** with **Always allow from this computer** ticked. It asks whether you want the quiet home screen, then downloads and installs the app. Your photos appear once the first build finishes. `./tvframe tv remove` puts the TV back exactly as it was.

<details>
<summary>Other ways to install</summary>

- **One line:** `curl -fsSL https://raw.githubusercontent.com/nicglazkov/tvframe/main/install.sh | sh` clones into `~/tvframe` and runs `setup` (once the repository is public).
- **No questions:** `./tvframe setup --yes` takes every suggested answer. Edit `~/.config/tvframe/config.env` afterwards and run `./tvframe restart`.
- **Without Docker:** `systemd --user` services straight on a Linux server. See [docs/MANUAL.md](docs/MANUAL.md).

</details>

## Everyday commands

| Command | What it does |
| --- | --- |
| `./tvframe status` | Whether the server is up, the last build, the encoder and the TV |
| `./tvframe logs` | What the server is doing right now |
| `./tvframe rebuild` | Rebuild the library now instead of waiting for a change |
| `./tvframe restart` | Pick up a change to `~/.config/tvframe/config.env` |
| `./tvframe update` | Get the latest version, rebuild the server and update the TV app |
| `./tvframe tv remove` | Put the TV's own screensaver and home back (`--uninstall` removes the app too) |
| `./tvframe uninstall` | Remove TV Frame. Your photos and settings stay where they are |

Adding photos is just dropping them into a folder inside your photos folder, or uploading on the photo manager. A build starts on its own a minute later.

## Learn more

- [Guide](docs/GUIDE.md): the TV and the photo manager page by page, settings, Home Assistant and other extras, what leaves your network, troubleshooting.
- [Devices](docs/DEVICES.md): which TVs and servers work, and why.
- [How it works](docs/ARCHITECTURE.md): the parts, the build pipeline, the repository layout and development.
- [Running without Docker](docs/MANUAL.md) and [releasing](docs/RELEASING.md).

## License

[MIT](LICENSE). The scoring models in the server image (OpenCLIP ViT-L/14, the LAION aesthetic predictor, YuNet) come under their own licenses.
