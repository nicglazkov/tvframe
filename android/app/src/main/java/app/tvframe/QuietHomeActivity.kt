package app.tvframe

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Outline
import android.graphics.Paint
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.AbsoluteSizeSpan
import android.text.style.ForegroundColorSpan
import android.text.style.TypefaceSpan
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewOutlineProvider
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.abs

/**
 * E, the Quiet Home: every app on the Streamer and nothing else, the frame's photos behind it, and
 * Google TV's own home one tile away. No ads, no rows of suggestions. Leave it alone for 30 seconds
 * and the photos take over. Laid out in 4K stage pixels like home.html, scaled to the UI plane.
 */
class QuietHomeActivity : Activity() {
    data class AppTile(val id: String, val name: String, val pkg: String?, val sys: Boolean = false,
                       val bg: Int = 0xFF1A1C1E.toInt(), val fg: Int = Color.WHITE)

    companion object {
        const val LAUNCHERX = "com.google.android.apps.tv.launcherx"
        /** Well-known apps, in shelf order, each with a fallback color for when it ships no banner. Only installed ones show. */
        val KNOWN = listOf(
            AppTile("netflix", "Netflix", "com.netflix.ninja", bg = 0xFF0B0B0B.toInt(), fg = 0xFFE50914.toInt()),
            AppTile("youtube", "YouTube", "com.google.android.youtube.tv", bg = 0xFFFF0033.toInt()),
            AppTile("youtubetv", "YouTube TV", "com.google.android.youtube.tvunplugged", bg = 0xFF181818.toInt()),
            AppTile("prime", "Prime Video", "com.amazon.amazonvideo.livingroom", bg = 0xFF0F1B2B.toInt(), fg = 0xFF1AA6E3.toInt()),
            AppTile("disney", "Disney+", "com.disney.disneyplus", bg = 0xFF0E1A47.toInt()),
            AppTile("max", "Max", "com.wbd.stream", bg = 0xFF002BE7.toInt()),
            AppTile("hulu", "Hulu", "com.hulu.livingroomplus", bg = 0xFF0B0C0F.toInt(), fg = 0xFF1CE783.toInt()),
            AppTile("appletv", "Apple TV", "com.apple.atve.androidtv.appletv", bg = 0xFF000000.toInt()),
            AppTile("peacock", "Peacock", "com.peacocktv.peacockandroid", bg = 0xFF000000.toInt()),
            AppTile("paramount", "Paramount+", "com.cbs.ott", bg = 0xFF0064FF.toInt()),
            AppTile("plex", "Plex", "com.plexapp.android", bg = 0xFF1F2326.toInt(), fg = 0xFFE5A00D.toInt()),
            AppTile("spotify", "Spotify", "com.spotify.tv.android", bg = 0xFF121212.toInt(), fg = 0xFF1ED760.toInt()),
            AppTile("ytmusic", "YouTube Music", "com.google.android.youtube.tvmusic", bg = 0xFF030303.toInt()),
            AppTile("twitch", "Twitch", "tv.twitch.android.app", bg = 0xFF9146FF.toInt()),
            AppTile("jellyfin", "Jellyfin", "org.jellyfin.androidtv", bg = 0xFF4B4FB8.toInt()),
            AppTile("emby", "Emby", "tv.emby.embyatv", bg = 0xFF3D8F3A.toInt()),
            AppTile("kodi", "Kodi", "org.xbmc.kodi", bg = 0xFF17B2E7.toInt()),
        )
        val PHOTOS = AppTile("photos", "Photos", null, bg = 0xFF222222.toInt())
        /** Right end of the second row. */
        val SYSTEM = listOf(
            AppTile("settings", "Settings", "com.android.tv.settings", sys = true),
            AppTile("google", "Google TV home", LAUNCHERX, sys = true),
        )
        /** Launchable but not wanted on the shelf: this app, the store, and Google's own surfaces. */
        private val SKIP = setOf("app.tvframe", "com.android.vending", "com.android.tv.settings", LAUNCHERX,
            "com.google.android.play.games", "com.google.android.videos", "com.google.android.tvrecommendations")
        private val GEAR = listOf("M24 17.5a6.5 6.5 0 1 0 0 13a6.5 6.5 0 1 0 0-13z", "M24 5.5v6M24 36.5v6M5.5 24h6M36.5 24h6M10.9 10.9l4.3 4.3M32.8 32.8l4.3 4.3M10.9 37.1l4.3-4.3M32.8 15.2l4.3-4.3")
        private val HOUSE = listOf("M8 22 24 9l16 13M13 19v19h22V19", "M21 38V28h6v10")
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var root: FrameLayout
    private lateinit var ui: FrameLayout
    private lateinit var scrim: View
    private lateinit var timeV: TextView
    private lateinit var dateV: TextView
    private lateinit var wxV: TextView
    private lateinit var noteV: TextView
    private lateinit var labelV: TextView
    private lateinit var awayClock: TextView
    private var photosImg: ImageView? = null
    private var show: FrameView? = null
    private val rows = ArrayList<ArrayList<Pair<AppTile, FrameLayout>>>()
    private var fr = 0
    private var fc = 0
    private var away = false
    private var s = 0.5f                     // UI px per 4K stage px
    private val ink = INK
    private val glow get() = Glow(.6f, 40f)
    private val idle = Runnable { goAway() }
    private val tick = object : Runnable { override fun run() { updateClock(); main.postDelayed(this, 15_000) } }
    private val fontsChanged = { applyFonts() }

    // what the Sonos speakers play: a card on the apps shelf, only while music plays (bridge /sonos)
    private lateinit var sonosCard: LinearLayout
    private lateinit var sonosBody: LinearLayout
    private lateinit var artV: ImageView
    private lateinit var roomV: TextView
    private lateinit var songV: TextView
    private lateinit var artistV: TextView
    private var sonosShown = false
    private var sonosKey = ""
    @Volatile private var sonosRun = 0

    private fun px(v: Float) = v * s
    private fun pxi(v: Float) = Math.round(v * s)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        s = resources.displayMetrics.widthPixels / 3840f
        root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK); clipChildren = false }
        scrim = object : View(this) {
            private val p = Paint()
            override fun onDraw(c: Canvas) {
                val h = height.toFloat(); val k = h / 2160f
                // bottom: rgba(6,8,8,.9) 0, .78 at 1050, .36 at 1400, 0 at 1800 (from the bottom)
                p.shader = android.graphics.LinearGradient(0f, h, 0f, h - 1800f * k,
                    intArrayOf(0xE6060808.toInt(), 0xC7060808.toInt(), 0x5C060808, 0x00060808),
                    floatArrayOf(0f, 1050f / 1800f, 1400f / 1800f, 1f), android.graphics.Shader.TileMode.CLAMP)
                c.drawRect(0f, 0f, width.toFloat(), h, p)
                p.shader = android.graphics.LinearGradient(0f, 0f, 0f, 620f * k,
                    intArrayOf(0x80000000.toInt(), 0x33000000, 0x00000000), floatArrayOf(0f, 380f / 620f, 1f), android.graphics.Shader.TileMode.CLAMP)
                c.drawRect(0f, 0f, width.toFloat(), 620f * k, p)
            }
        }
        root.addView(scrim, FrameLayout.LayoutParams(-1, -1))
        ui = FrameLayout(this).apply { clipChildren = false; clipToPadding = false }
        root.addView(ui, FrameLayout.LayoutParams(-1, -1))

        // top bar: time and date on the left, weather on the right, sharing a baseline
        timeV = text(96f, ink); dateV = text(48f, withAlpha(Color.WHITE, .78f)); wxV = text(52f, withAlpha(Color.WHITE, .88f))
        val left = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; isBaselineAligned = true
            addView(timeV); addView(dateV, LinearLayout.LayoutParams(-2, -2).apply { marginStart = pxi(36f) })
        }
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; isBaselineAligned = true
            addView(left, LinearLayout.LayoutParams(0, -2, 1f))
            addView(wxV)
        }
        ui.addView(bar, FrameLayout.LayoutParams(-1, -2).apply { setMargins(pxi(200f), pxi(130f), pxi(200f), 0) })
        noteV = text(42f, withAlpha(Color.WHITE, .8f)).apply { isSingleLine = true; ellipsize = android.text.TextUtils.TruncateAt.END }
        ui.addView(noteV, FrameLayout.LayoutParams(-1, -2).apply { setMargins(pxi(200f), pxi(290f), pxi(200f), 0) })

        buildRows()
        buildSonos()
        awayClock = text(64f, ink).apply { alpha = 0f }
        root.addView(awayClock, FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM or Gravity.END).apply { setMargins(0, 0, pxi(200f), pxi(170f) - 0) })
        setContentView(root)
        applyFonts()
        Live.cached(this)
        updateClock()
    }

    private fun text(size4k: Float, color: Int) = TextView(this).apply {
        setTextSize(TypedValue.COMPLEX_UNIT_PX, px(size4k))
        setTextColor(color)
        includeFontPadding = false
        setShadowLayer(((40f * s / 2f - .5f) / .57735f).coerceIn(1f, 25f), 0f, 0f, 0x66000000)
    }

    private fun installed(pkg: String?): Boolean = pkg == null || try { packageManager.getPackageInfo(pkg, 0); true } catch (e: PackageManager.NameNotFoundException) { false }

    /** Every other app with a TV launcher entry, by name. */
    private fun others(): List<AppTile> {
        val pm = packageManager
        val known = KNOWN.mapNotNull { it.pkg }.toSet()
        return pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LEANBACK_LAUNCHER), 0)
            .map { it.activityInfo.packageName to it.loadLabel(pm).toString() }
            .distinctBy { it.first }
            .filter { it.first !in known && it.first !in SKIP }
            .sortedBy { it.second.lowercase() }
            .map { (pkg, label) -> AppTile(pkg, label, pkg) }
    }

    private fun banner(pkg: String): Drawable? = try {
        val pm = packageManager
        pm.getLeanbackLaunchIntentForPackage(pkg)?.let { pm.getActivityBanner(it) } ?: pm.getApplicationBanner(pkg)
    } catch (e: Exception) { null }

    private fun buildRows() {
        val tileW = (3440f - 6 * 44f) / 7f
        val tileH = tileW * 9f / 16f
        val row1Top = 2160f - 140f - tileH
        val row0Top = row1Top - 44f - tileH
        labelV = text(44f, Color.WHITE).apply { alpha = .6f }
        ui.addView(labelV.apply { text = "Apps" }, FrameLayout.LayoutParams(-2, -2).apply { setMargins(pxi(200f), pxi(row0Top - 34f - 58f), 0, 0) })
        rows.clear(); rows.add(ArrayList()); rows.add(ArrayList())
        // first row: installed apps, known ones first; second row: Photos, more apps, then the system tiles
        val sys = SYSTEM.filter { installed(it.pkg) }
        val apps = KNOWN.filter { installed(it.pkg) } + others()
        apps.take(7).forEachIndexed { c, a -> place(a, 0, c, row0Top, row1Top, tileW, tileH) }
        (listOf(PHOTOS) + apps.drop(7)).take(7 - sys.size).forEachIndexed { c, a -> place(a, 1, c, row0Top, row1Top, tileW, tileH) }
        sys.forEachIndexed { i, a -> place(a, 1, 7 - sys.size + i, row0Top, row1Top, tileW, tileH) }
        focus(0, 0)
    }

    /** The Now Playing card: right end of the apps shelf, bottom on the "Apps" line, hidden until music plays. */
    private fun buildSonos() {
        val tileW = (3440f - 6 * 44f) / 7f
        val row0Top = 2160f - 140f - 2 * (tileW * 9f / 16f) - 44f
        val radius = px(22f)
        artV = ImageView(this).apply {
            scaleType = ImageView.ScaleType.CENTER_CROP
            outlineProvider = object : ViewOutlineProvider() { override fun getOutline(v: View, o: Outline) { o.setRoundRect(0, 0, v.width, v.height, px(14f)) } }
            clipToOutline = true
            background = GradientDrawable().apply { cornerRadius = px(14f); setColor(0x24FFFFFF) }
        }
        roomV = text(34f, withAlpha(Color.WHITE, .66f)).apply { setShadowLayer(0f, 0f, 0f, 0) }
        songV = text(50f, Color.WHITE).apply { maxLines = 2; maxWidth = pxi(1000f); setShadowLayer(0f, 0f, 0f, 0); setLineSpacing(0f, 1.08f) }
        artistV = text(40f, withAlpha(Color.WHITE, .8f)).apply { maxLines = 2; maxWidth = pxi(1000f); setShadowLayer(0f, 0f, 0f, 0) }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_VERTICAL
            addView(roomV)
            addView(songV, LinearLayout.LayoutParams(-2, -2).apply { topMargin = pxi(10f) })
            addView(artistV, LinearLayout.LayoutParams(-2, -2).apply { topMargin = pxi(6f) })
        }
        sonosBody = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            addView(artV, LinearLayout.LayoutParams(pxi(168f), pxi(168f)))
            addView(col, LinearLayout.LayoutParams(-2, -2).apply { marginStart = pxi(32f) })
        }
        sonosCard = LinearLayout(this).apply {
            setPadding(pxi(26f), pxi(26f), pxi(40f), pxi(26f))
            background = GradientDrawable().apply { cornerRadius = radius; setColor(0x2EFFFFFF) }
            addView(sonosBody)
            alpha = 0f; visibility = View.INVISIBLE
            setLayerType(View.LAYER_TYPE_HARDWARE, null)
        }
        ui.addView(sonosCard, FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM or Gravity.END).apply {
            rightMargin = pxi(200f); bottomMargin = pxi(2160f - (row0Top - 40f))
        })
    }

    private fun watchSonos() {
        val run = ++sonosRun
        Thread {
            var v = -1
            val base = Prefs(this).bridgeUrl.trimEnd('/')
            while (run == sonosRun) {
                try {
                    val c = Http.open("$base/sonos?v=$v&wait=25").apply { readTimeout = 40_000 }
                    val o = try { org.json.JSONObject(c.inputStream.bufferedReader().readText()) } finally { c.disconnect() }
                    v = o.optInt("v", v)
                    val art = o.optString("art").takeIf { o.optBoolean("playing") && it.isNotEmpty() && it != "null" }?.let { a ->
                        try { Http.open(base + a).let { cc -> try { android.graphics.BitmapFactory.decodeStream(cc.inputStream) } finally { cc.disconnect() } } } catch (e: Exception) { null }
                    }
                    main.post { if (run == sonosRun) showSonos(o, art) }
                } catch (e: Exception) {
                    try { Thread.sleep(10_000) } catch (_: InterruptedException) { }
                }
            }
        }.apply { isDaemon = true; name = "tvframe-sonos" }.start()
    }

    private fun showSonos(o: org.json.JSONObject, art: android.graphics.Bitmap?) {
        val playing = o.optBoolean("playing")
        val key = if (playing) listOf("title", "artist", "room").joinToString("|") { o.optString(it) } else ""
        if (key == sonosKey) { if (art != null && playing) artV.setImageBitmap(art); return }
        sonosKey = key
        fun fill() {
            roomV.text = "Playing in ${o.optString("room", "Living Room")}"
            songV.text = o.optString("title")
            artistV.text = listOf(o.optString("artist"), o.optString("album")).filter { it.isNotBlank() }.distinct().joinToString(" · ")
            artistV.visibility = if (artistV.text.isNullOrBlank()) View.GONE else View.VISIBLE
            artV.setImageBitmap(art)
        }
        sonosCard.animate().cancel(); sonosBody.animate().cancel()
        when {
            playing && !sonosShown -> {
                // rises onto the shelf
                sonosShown = true
                fill(); sonosBody.alpha = 1f
                sonosCard.visibility = View.VISIBLE
                sonosCard.translationY = px(28f)
                sonosCard.animate().alpha(1f).translationY(0f).setDuration(440).setInterpolator(EASE_ARRIVE).start()
            }
            playing -> {
                // next song: the contents cross through, the card stays put
                sonosBody.animate().alpha(0f).setDuration(150).setInterpolator(EASE_LEAVE).withEndAction {
                    fill()
                    sonosBody.animate().alpha(1f).setDuration(280).setInterpolator(EASE_ARRIVE).start()
                }.start()
            }
            sonosShown -> {
                sonosShown = false
                sonosCard.animate().alpha(0f).translationY(px(14f)).setDuration(260).setInterpolator(EASE_LEAVE)
                    .withEndAction { if (!sonosShown) sonosCard.visibility = View.INVISIBLE }.start()
            }
        }
    }

    private fun place(a: AppTile, r: Int, col: Int, row0Top: Float, row1Top: Float, w: Float, h: Float) {
        val t = tile(a, w, h)
        val x = 200f + col * (w + 44f)
        ui.addView(t, FrameLayout.LayoutParams(pxi(w), pxi(h)).apply { setMargins(pxi(x), pxi(if (r == 0) row0Top else row1Top), 0, 0) })
        rows[r].add(a to t)
        t.tag = col
    }

    private fun glyph(paths: List<String>, size: Float) = object : View(this) {
        private val ps = paths.map { PathData.parse(it) }
        private val p = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 2.6f; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND; color = Color.WHITE }
        override fun onDraw(c: Canvas) { val k = width / 48f; c.save(); c.scale(k, k); ps.forEach { c.drawPath(it, p) }; c.restore() }
    }.also { it.layoutParams = LinearLayout.LayoutParams(pxi(size), pxi(size)) }

    private fun tile(a: AppTile, w: Float, h: Float): FrameLayout {
        val radius = px(22f)
        val f = FrameLayout(this)
        f.outlineProvider = object : ViewOutlineProvider() {
            override fun getOutline(view: View, o: Outline) { o.setRoundRect(0, 0, view.width, view.height, radius) }
        }
        f.clipToOutline = true
        f.elevation = px(18f)
        f.background = GradientDrawable().apply { cornerRadius = radius; setColor(if (a.sys) 0x2EFFFFFF else a.bg) }
        val ban = if (!a.sys && a.pkg != null) banner(a.pkg) else null
        if (ban != null) {
            f.addView(ImageView(this).apply { setImageDrawable(ban); scaleType = ImageView.ScaleType.CENTER_CROP }, FrameLayout.LayoutParams(-1, -1))
        } else {
            if (a.id == "photos") {
                val img = ImageView(this).apply { scaleType = ImageView.ScaleType.CENTER_CROP }
                photosImg = img
                f.addView(img, FrameLayout.LayoutParams(-1, -1))
                f.addView(View(this).apply { setBackgroundColor(0x6B080A0A) }, FrameLayout.LayoutParams(-1, -1))
            }
            val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER }
            when (a.id) {
                "settings" -> col.addView(glyph(GEAR, 76f))
                "google" -> col.addView(glyph(HOUSE, 76f))
            }
            val wm = text(if (a.sys) 44f else 58f, a.fg).apply {
                text = if (a.id == "netflix") a.name.uppercase() else a.name
                gravity = Gravity.CENTER
                tag = if (a.sys) "wm-sys" else "wm"
                if (a.id != "photos") setShadowLayer(0f, 0f, 0f, 0)
            }
            col.addView(wm, LinearLayout.LayoutParams(-2, -2).apply { topMargin = if (a.sys) pxi(10f) else 0 })
            if (a.id == "google") col.addView(text(30f, withAlpha(Color.WHITE, .62f)).apply { text = "The stock home screen"; tag = "sub"; setShadowLayer(0f, 0f, 0f, 0) },
                LinearLayout.LayoutParams(-2, -2).apply { topMargin = pxi(10f) })
            f.addView(col, FrameLayout.LayoutParams(-1, -1))
        }
        // the focus ring: 8 px white, drawn over the tile edge
        f.foreground = GradientDrawable().apply { cornerRadius = radius; setStroke(pxi(8f), Color.WHITE); setColor(0) }
        f.foreground.alpha = 0
        f.contentDescription = a.name
        return f
    }

    private fun applyFonts() {
        timeV.typeface = Fonts.get(Fonts.Face.SANS)
        awayClock.typeface = Fonts.get(Fonts.Face.SANS)
        dateV.typeface = Fonts.get(Fonts.Face.TEXT)
        wxV.typeface = Fonts.get(Fonts.Face.TEXT)
        noteV.typeface = Fonts.get(Fonts.Face.TEXT)
        labelV.typeface = Fonts.get(Fonts.Face.TEXT_MEDIUM)
        roomV.typeface = Fonts.get(Fonts.Face.TEXT_MEDIUM)
        songV.typeface = Fonts.get(Fonts.Face.SANS_MEDIUM)
        artistV.typeface = Fonts.get(Fonts.Face.TEXT)
        for (r in rows) for ((_, t) in r) walk(t) { v ->
            if (v is TextView) v.typeface = when (v.tag) {
                "wm" -> Fonts.get(Fonts.Face.SANS_MEDIUM)
                "wm-sys" -> Fonts.get(Fonts.Face.SANS_MEDIUM)
                else -> Fonts.get(Fonts.Face.TEXT)
            }
        }
    }

    private fun walk(v: View, f: (View) -> Unit) { f(v); if (v is android.view.ViewGroup) for (i in 0 until v.childCount) walk(v.getChildAt(i), f) }

    private fun updateClock() {
        val n = laNow()
        timeV.text = fmtClock(n)
        dateV.text = fmtDay(n)
        awayClock.text = fmtClock(n)
        val w = Live.data?.optJSONObject("weather")
        wxV.text = if (w != null) "${w.optInt("temp")}°, ${w.optString("text").lowercase()}" else ""
    }

    private fun onPhoto(p: Photo?) {
        if (p == null) { noteV.text = ""; return }
        val c = p.caption
        val sb = SpannableStringBuilder()
        if (c.place.isNotBlank()) {
            val st = sb.length
            sb.append(c.place)
            sb.setSpan(AbsoluteSizeSpan(pxi(56f)), st, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            sb.setSpan(ForegroundColorSpan(Color.WHITE), st, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            sb.setSpan(TypefaceSpan(Fonts.get(Fonts.Face.SANS)), st, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            sb.append("   ")
        }
        sb.append(listOf(c.whenText, c.camera).filter { it.isNotBlank() }.joinToString("  ·  "))
        noteV.animate().alpha(0f).setDuration(600).withEndAction {
            noteV.text = sb
            noteV.animate().alpha(if (away) 0f else 1f).setDuration(900).start()
        }.start()
        p.thumb?.let { u ->
            Thread {
                try {
                    val f = MediaCache(this).get(u)
                    val b = android.graphics.BitmapFactory.decodeFile(f.path)
                    main.post { if (b != null) photosImg?.setImageBitmap(b) }
                } catch (e: Exception) { }
            }.start()
        }
    }

    override fun onStart() {
        super.onStart()
        Fonts.onChange(fontsChanged)
        applyFonts()
        val v = FrameView(this, FrameView.Role.HOME_BG, label = "home")
        v.onPhoto = { onPhoto(it) }
        root.addView(v, 0, FrameLayout.LayoutParams(-1, -1))
        show = v
        v.start()
        main.post(tick)
        Thread { Live.refresh(this); main.post { updateClock() } }.start()
        watchSonos()
        wake()
    }

    override fun onStop() {
        sonosRun++
        Fonts.removeListener(fontsChanged)
        main.removeCallbacks(tick)
        main.removeCallbacks(idle)
        show?.let { it.stop(); root.removeView(it) }
        show = null
        super.onStop()
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        // Home pressed while already home: back to the first tile
        wake()
        focus(0, 0)
    }

    private fun focus(r: Int, c: Int) {
        rows.getOrNull(fr)?.getOrNull(fc)?.second?.let { setFocused(it, false) }
        fr = r.coerceIn(0, rows.size - 1)
        fc = c.coerceIn(0, rows[fr].size - 1)
        rows[fr].getOrNull(fc)?.second?.let { setFocused(it, true) }
        labelV.animate().alpha(1f).setDuration(300).start()
    }

    private fun setFocused(t: FrameLayout, on: Boolean) {
        t.animate().scaleX(if (on) 1.08f else 1f).scaleY(if (on) 1.08f else 1f).setDuration(260).setInterpolator(EASE_CALM).start()
        t.translationZ = if (on) px(30f) else 0f
        t.foreground.alpha = if (on) 255 else 0
    }

    private fun wake() {
        main.removeCallbacks(idle)
        main.postDelayed(idle, 30_000)
        if (!away) return
        away = false
        ui.animate().alpha(1f).setDuration(600).setInterpolator(EASE_SOFT).start()
        scrim.animate().alpha(1f).setDuration(600).setInterpolator(EASE_SOFT).start()
        awayClock.animate().alpha(0f).setDuration(600).start()
    }

    private fun goAway() {
        away = true
        ui.animate().alpha(0f).setDuration(1600).setInterpolator(EASE_SOFT).start()
        scrim.animate().alpha(0f).setDuration(2000).setInterpolator(EASE_SOFT).start()
        awayClock.animate().alpha(.86f).setDuration(1600).setInterpolator(EASE_SOFT).start()
    }

    private fun centerX(t: FrameLayout) = (t.layoutParams as FrameLayout.LayoutParams).leftMargin + t.layoutParams.width / 2f

    override fun dispatchKeyEvent(e: KeyEvent): Boolean {
        val k = e.keyCode
        val nav = k == KeyEvent.KEYCODE_DPAD_LEFT || k == KeyEvent.KEYCODE_DPAD_RIGHT || k == KeyEvent.KEYCODE_DPAD_UP ||
            k == KeyEvent.KEYCODE_DPAD_DOWN || k == KeyEvent.KEYCODE_DPAD_CENTER || k == KeyEvent.KEYCODE_ENTER || k == KeyEvent.KEYCODE_BACK
        if (!nav) return super.dispatchKeyEvent(e)
        if (e.action == KeyEvent.ACTION_UP) return true
        val wasAway = away
        wake()
        if (wasAway) return true
        when (k) {
            KeyEvent.KEYCODE_DPAD_LEFT -> focus(fr, fc - 1)
            KeyEvent.KEYCODE_DPAD_RIGHT -> focus(fr, fc + 1)
            KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN -> {
                val nr = (fr + if (k == KeyEvent.KEYCODE_DPAD_DOWN) 1 else -1).coerceIn(0, rows.size - 1)
                if (nr != fr) {
                    val cx = centerX(rows[fr][fc].second)
                    val best = rows[nr].indices.minByOrNull { abs(centerX(rows[nr][it].second) - cx) } ?: 0
                    focus(nr, best)
                }
            }
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> if (e.repeatCount == 0) open(rows[fr][fc].first)
            KeyEvent.KEYCODE_BACK -> {}         // this is home: nowhere to go back to
        }
        return true
    }

    private fun open(a: AppTile) {
        Log.i(TAG, "home: open ${a.id}")
        try {
            when (a.id) {
                "photos" -> startActivity(Intent(this, ShowActivity::class.java))
                "settings" -> startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                "google" -> {
                    HomeKeyService.allowGoogleHome = true
                    val i = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME).setPackage(LAUNCHERX).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    val ri = packageManager.resolveActivity(i, 0)
                    if (ri != null) startActivity(i.setComponent(ComponentName(ri.activityInfo.packageName, ri.activityInfo.name)))
                    else Apps.launch(this, LAUNCHERX)
                }
                else -> Apps.launch(this, a.pkg ?: return)
            }
        } catch (e: Exception) {
            Log.w(TAG, "could not open ${a.id}: $e")
        }
    }

}
