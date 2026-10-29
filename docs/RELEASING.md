# Releasing

The repository is private for now. Everything below is ready so that going public is a short list, not a project.

## How an install works today

`./tvframe setup` builds the server image on the user's computer (about 10 minutes) and `./tvframe tv` builds the TV app there too, signed with a key made on that computer. Nothing has to be published for this to work, which is why it already works from a private clone.

Two settings in `.env` switch an install to downloads instead, once releases exist:

| Setting | Effect |
| --- | --- |
| `TVFRAME_IMAGE=ghcr.io/nicglazkov/tvframe` | `setup` and `update` pull the server image (`:cu128` or `:cpu`, from `TVFRAME_TORCH`) instead of building it. |
| `TVFRAME_APK_URL=https://github.com/nicglazkov/tvframe/releases/latest/download/tvframe.apk` | `tv` downloads the signed app instead of building it. The app is told the server's address after it is installed, so one build serves everyone. |

When the project goes public, make both the defaults in `tvframe` (`get_server` and `build_apk`), keeping the local build as the fallback.

## Before going public

1. **Pick a license** and add `LICENSE`. The README says all rights reserved until then. MIT or Apache-2.0 suit a project like this; the models baked into the server image are MIT licensed too (YuNet, the LAION aesthetic head, and OpenAI's CLIP ViT-L/14 weights).
2. **Make the release signing key**, once, and keep a copy somewhere safe. An app signed with a different key cannot update an installed one; users would have to uninstall first.
   ```bash
   keytool -genkeypair -keystore tvframe-release.jks -alias tvframe -keyalg RSA -keysize 3072 -validity 10000 -dname "CN=TV Frame"
   ```
3. **Add the repository secrets** (Settings > Secrets and variables > Actions): `APK_KEYSTORE_BASE64` (`base64 -i tvframe-release.jks`), `APK_KEYSTORE_PASSWORD`, `APK_KEY_ALIAS` (`tvframe`), `APK_KEY_PASSWORD`.
4. **Check the history** for anything personal: `git log -p | grep -inE 'token|password|192\.168\.[0-9]+\.'` should only find the documentation examples (192.168.1.x).
5. **Make it public:** Settings > General > Danger Zone > Change visibility. Then the one-line installer in the README works: `curl -fsSL https://raw.githubusercontent.com/nicglazkov/tvframe/main/install.sh | sh`.
6. **Make the images public** after the first release: the package page on GitHub (Packages > tvframe > Package settings > Change visibility).

## Cutting a release

1. Bump `versionCode` and `versionName` in `android/app/build.gradle.kts`, and add a section to `CHANGELOG.md`.
2. Tag and push:
   ```bash
   git tag v0.4.0 && git push origin v0.4.0
   ```
3. The Release workflow (`.github/workflows/release.yml`) builds the signed app and attaches it to a draft release, and pushes the server images to `ghcr.io/nicglazkov/tvframe:cu128` and `:cpu` (plus `:v0.4.0-cu128` and `:v0.4.0-cpu`).
4. Read the draft's notes, then publish it.

The CI workflow (`.github/workflows/ci.yml`) runs on every push: the Python compiles, shellcheck passes, the CPU video encoders produce the same 10-bit HEVC as NVENC, and the TV app builds. On a private repository these runs count against the account's free Actions minutes.

## An installer for people who never open a terminal

What exists covers "I have a Linux computer and can paste a command". The next steps, in order of reach:

- **A prebuilt app on a release** (above), so no Android tools are downloaded.
- **Pulling the published server image**, so setup takes a few minutes instead of building.
- **Windows:** a short PowerShell script that installs WSL 2 and Docker Desktop, then runs `install.sh` inside WSL.
- **Pairing from the photo manager:** the server already runs `adb`, so the TV step could be a page in the manager (type the TV's address, approve on the TV) instead of `./tvframe tv`.
