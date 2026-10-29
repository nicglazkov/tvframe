# TV Frame: photo source (Google Photos shared albums)

> [!CAUTION]
> **Off unless you set it up, and at your own risk.** This tool reads Google Photos share pages the way a browser does. That is not an API Google offers for this, it may be against Google's terms of service, and it can stop working at any time. Use it only with albums you own or have permission to copy, and only if you accept that responsibility. To stop, delete `albums.txt` and any schedule you added for `sync.sh`.

This folder pulls photos and videos from Google Photos albums onto the server, at full
original quality, and then re-runs the curation so the slideshow has fresh scores.

Google closed its photo API to outside apps in March 2025, so this works the way a
browser does: it opens the album's share link, reads the list of items from the page,
and downloads each original file. No Google login, no cookies, no API key.

## Files

- `gpalbum.py` downloads one album and keeps it in sync. Python standard library only.
- `sync.sh` runs gpalbum for every album in `albums.txt`, then runs `~/tvframe/curate/run.sh`.
- `albums.txt` is your list of albums.
- `logs/` has one log per sync run.

## Setting it up with your albums

1. In Google Photos (phone or web), open an album, tap **Share**, then **Create link**
   (on the web: Share, then "Create link"). Copy the link. It looks like
   `https://photos.app.goo.gl/AbCd1234...`.
2. Add one line per album to `~/tvframe/ingest/albums.txt`: a short name, then the link.

   ```
   family   https://photos.app.goo.gl/AbCd1234...
   trips    https://photos.app.goo.gl/EfGh5678...
   ```

   The name becomes the folder: `~/tvframe/photos/albums/family/`. Use letters, digits,
   `-` or `_`. Lines starting with `#` are ignored. Start from `albums.example.txt`; `albums.txt` itself stays out of git.
3. Run it on the server:

   ```
   ~/tvframe/ingest/sync.sh              # sync all albums, then curate
   ~/tvframe/ingest/sync.sh --no-curate  # sync only
   ```

The first run downloads everything (expect about 2 to 3 seconds per photo and much
longer for videos, which come down as the original files). Later runs only fetch new
items and take a few seconds per album.

One album by hand:

```
python3 ~/tvframe/ingest/gpalbum.py <link> <folder>               # download or update
python3 ~/tvframe/ingest/gpalbum.py <link> <folder> --list-only   # just list, as JSON
python3 ~/tvframe/ingest/gpalbum.py <link> <folder> --no-video    # photos only
```

Other options: `--delay 2` waits longer between downloads (default 1 second),
`--allow-empty` (see below).

## What you get

- Photos exactly as uploaded: same pixels, same file, EXIF kept (camera, date, and GPS
  location if the photo had it). The file's date is set to when the photo was taken.
- Videos as the original files (MP4 or MOV).
- Motion photos come down as the still JPEG, flagged `motion_photo` in the manifest.
- `manifest.json` in each album folder lists every item: id, link, width, height, photo
  or video, video length, when it was taken (with time zone), when it was added, file
  name, size and SHA-256.
- File names are the original name plus the end of the item id, for example
  `IMG_20200208_165858__farNx0Ea5M.jpg`, so two photos with the same name never clash.

## What happens when the album changes

- New items are downloaded on the next run.
- Items taken out of the album are marked `removed` in the manifest and their files are
  moved to `_removed/` inside the album folder, so the slideshow stops using them.
  If one is added back, it is moved back.
- A file you delete by hand is downloaded again on the next run.
- A download that fails (for example a video Google is still processing) is marked
  `failed` and tried again next run. The script keeps going with the rest.
- Safety checks: if an album suddenly lists 0 items but had items before, nothing is
  marked removed and the run stops with code 4 (use `--allow-empty` if you really emptied
  it). If Google lists fewer items than the album says it has, nothing is marked removed
  that run. A folder can only hold one album; pointing a different link at it is refused.

Exit codes: 0 fine, 1 some downloads failed, 2 bad or dead link, 3 network or page
format problem, 4 refused an empty listing.

## Running it on a schedule (not set up)

Nothing runs on its own yet. To sync every night at 4:00, add this with `crontab -e` on the server:

```
0 4 * * * $HOME/tvframe/ingest/sync.sh >/dev/null 2>&1
```

`sync.sh` writes its own log under `logs/` and will not start a second copy if one is
still running. A systemd user timer would work the same way.

## Privacy, plainly

A shared album link is not private. **Anyone who has the link can see every photo and
video in that album**, at full quality, without signing in. That includes the GPS
location stored inside most phone photos.

- The links are long random strings, so nobody will guess one. But they are not secret
  in the way a password is: they can be forwarded, pasted somewhere public, or show up in
  someone's browser history, and Google does not tell you who opened them.
- The link only exposes that one album, never the rest of your library.
- You can turn it off any time: in the album, Share options, turn off "Link sharing".
  The old link then stops working (this script will report a dead link).
- To lower the risk: share only albums you would be fine with a stranger seeing, and
  keep `albums.txt` on the server only.

## If this stops working

This depends on how the Google Photos share page is built today (checked 2026-10-24).
Google can change it without notice. If it breaks, the options are:

1. **Google Takeout, scheduled export.** Takeout can export your Google Photos every two
   months for a year, to Drive or as download links. Complete and official, but slow
   and not automatic to pull down. Good for history.
2. **Google Photos Picker API.** An app opens a Google picker and you choose up to 2,000
   items. It is supported by Google, but you have to pick again by hand whenever you want
   new photos included.
3. **Immich on the server.** A self-hosted photo library. The iPhone app backs up new photos
   to it directly, and the TV can read from it. Your old Google library can be loaded
   in once from Takeout. This removes Google from the loop entirely and is the most
   durable choice, at the cost of running one more service.

## How it works (for whoever fixes it later)

- The share link redirects to `photos.google.com/share/<album id>?key=<key>`.
- The page embeds the first 300 items in an `AF_initDataCallback` block. Each item is
  `[id, [base url, width, height, ...], taken ms, ?, tz offset ms, added ms, ..., {extras}]`.
  Videos have extras key `76647426` (first value is the length in ms); motion photos have
  `146008172`. The album block's index 21 is the item count.
- If there are more, the page gives a next-page token. The rest come from
  `POST /_/PhotosUi/data/batchexecute?rpcids=snAcKc` with
  `f.req=[[["snAcKc","[\"<album id>\",\"<token>\",null,\"<key>\"]",null,"generic"]]]`,
  300 items per page, no cookies needed.
- Download: base url + `=d` gives the original photo; base url + `=dv` redirects to the
  original video file.
