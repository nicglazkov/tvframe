# Releasing

## How an install gets its parts

`./tvframe setup` downloads the server image from `ghcr.io/nicglazkov/tvframe` (`:cu128` with a GPU, `:cpu` without), and `./tvframe tv` downloads the signed app from the latest GitHub release. The app is told the server's address after it is installed, so one build serves everyone.

Each falls back to building on the user's computer when there is nothing to download (a private repository, no release yet, no internet to GitHub). Two `.env` settings force the local build:

| Setting | Effect |
| --- | --- |
| `TVFRAME_IMAGE=tvframe` | `setup` and `update` build the server image here (about 10 minutes) instead of pulling it. |
| `TVFRAME_APK_URL=build` | `tv` builds the app here, signed with a key made on this computer. |

A computer that has already built its own app keeps building it: an Android app only updates from an app signed with the same key, so switching to the released app would mean uninstalling first.

## The signing key

Releases are signed with a 3072-bit RSA key, alias `tvframe`, valid until 2054. It lives in two places:

- The repository secrets `APK_KEYSTORE_BASE64`, `APK_KEYSTORE_PASSWORD`, `APK_KEY_ALIAS` and `APK_KEY_PASSWORD` (Settings > Secrets and variables > Actions), which the Release workflow uses.
- `android/keystore/` on the first TV Frame server, which git ignores. Keep a copy of `tvframe-local.jks` and `key.properties` somewhere safe as well, such as a password manager.

If the key is ever lost, a new one works for new installs, but every existing TV has to uninstall the app before it can take an update.

## Cutting a release

1. Bump `versionCode` and `versionName` in `android/app/build.gradle.kts`, and add a `## <version>` section to `CHANGELOG.md`. The release notes are taken from that section.
2. Tag and push:
   ```bash
   git tag v0.4.0 && git push origin v0.4.0
   ```
3. The Release workflow (`.github/workflows/release.yml`) builds the signed app and attaches it to a draft release as `tvframe-v0.4.0.apk` and `tvframe.apk`, and pushes the server images as `:cu128` and `:cpu` (plus `:v0.4.0-cu128` and `:v0.4.0-cpu`).
4. Read the draft, then publish it. Only a published release counts as `latest`, which is what installs download.

## Keeping it hands-off

The repository is public with Issues, Discussions, the wiki and Projects turned off, and the README says there is no support. Pull requests cannot be turned off on GitHub; they are left unanswered. Before each release, check the history once more: `git log -p | grep -inE 'token|password|192\.168\.[0-9]+\.'` should only find the documentation examples (192.168.1.x) and the secret names in the Release workflow.

## The CI workflow

`.github/workflows/ci.yml` runs on every push: the Python compiles, shellcheck passes, the CPU video encoders produce the same 10-bit HEVC as NVENC, and the TV app builds. On a public repository these runs are free.

## An installer for people who never open a terminal

What exists covers "I have a Linux computer and can paste a command". The next steps, in order of reach:

- **Windows:** a short PowerShell script that installs WSL 2 and Docker Desktop, then runs `install.sh` inside WSL.
- **Pairing from the photo manager:** the server already runs `adb`, so the TV step could be a page in the manager (type the TV's address, approve on the TV) instead of `./tvframe tv`.
