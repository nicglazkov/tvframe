package app.tvframe

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import kotlin.random.Random

const val TAG = "TVFrame"

/** Everything the user can change. Also settable over adb, see SettingsActivity. */
class Prefs(ctx: Context) {
    private val sp: SharedPreferences = ctx.getSharedPreferences("tvframe", Context.MODE_PRIVATE)

    var playlistUrl: String
        get() = sp.getString("url", DEFAULT_URL) ?: DEFAULT_URL
        set(v) = sp.edit().putString("url", v.trim()).apply()

    /** "auto" follows the server's playlist.json, else "a", "b" or "c". */
    var mode: String
        get() = sp.getString("mode", "auto") ?: "auto"
        set(v) = sp.edit().putString("mode", v).apply()

    /** Still codec for mode b: "auto", "hevc" or "av1". */
    var codec: String
        get() = sp.getString("codec", "auto") ?: "auto"
        set(v) = sp.edit().putString("codec", v).apply()

    var pinned: List<String>
        get() = (sp.getString("pinned", "") ?: "").split('\n').filter { it.isNotBlank() }
        set(v) = sp.edit().putString("pinned", v.joinToString("\n")).apply()

    /** Production show data (server/prod_build.py). */
    var prodUrl: String
        get() = sp.getString("prod_url", DEFAULT_PROD) ?: DEFAULT_PROD
        set(v) = sp.edit().putString("prod_url", v.trim()).apply()

    /** The bridge on the server: live data for Glance, Now on TV, and the photo manager's commands. */
    var bridgeUrl: String
        get() = sp.getString("bridge_url", DEFAULT_BRIDGE) ?: DEFAULT_BRIDGE
        set(v) = sp.edit().putString("bridge_url", v.trim()).apply()

    /** Testing only: a second key code the star service also acts on (0 = none). */

    companion object {
        // the server's address comes from tvframe.server in android/local.properties (app/build.gradle.kts)
        const val DEFAULT_PROD = "${BuildConfig.TVFRAME_SERVER}:8151/prod.json"
        const val DEFAULT_BRIDGE = "${BuildConfig.TVFRAME_SERVER}:8154"
        const val DEFAULT_URL = "${BuildConfig.TVFRAME_SERVER}:8151/playlist.json"
    }
}

data class Item(
    val id: String,
    val type: String,          // "photo" or "video"
    val url: String,           // jpeg frame for photos, mp4 for videos
    val hevc: String?,         // one-frame 10-bit HEVC still (mode b)
    val av1: String?,          // one-frame 10-bit AV1 still (mode b)
    val weight: Double,
    val cluster: Int?,
    val focalX: Float,
    val focalY: Float,
    val layout: String,
    val duration: Double,
    val caption: String?,
    val hevc8: String? = null, // 8-bit HEVC still, for decoders without Main10
    val url8: String? = null,  // 8-bit copy of a clip
    val poster: String? = null, // a clip's first frame as a 1080p JPEG (tools/clip_loops.py), faded in before the clip starts
    val showMs: Long? = null,   // how long a clip stays up (prod_build.py clip_show_s); null on older prod.json
) {
    val isVideo get() = type == "video"
}

data class Loop(val url: String, val url8: String?, val duration: Double)

data class Playlist(
    val mode: String,
    val photoSeconds: Double,
    val transitionMs: Long,
    val kenBurns: Boolean,
    val captions: Boolean,
    val clock: Boolean,
    val loop: Loop?,
    val items: List<Item>,
) {
    companion object {
        fun parse(text: String, base: String): Playlist {
            val o = JSONObject(text)
            fun abs(u: String?): String? = u?.let { URL(URL(base), it).toString() }
            val arr = o.optJSONArray("items")
            val items = ArrayList<Item>()
            if (arr != null) for (i in 0 until arr.length()) {
                val j = arr.getJSONObject(i)
                val f = j.optJSONArray("focal")
                items += Item(
                    id = j.getString("id"),
                    type = j.optString("type", "photo"),
                    url = abs(j.getString("url"))!!,
                    hevc = abs(j.optString("hevc").ifEmpty { null }),
                    av1 = abs(j.optString("av1").ifEmpty { null }),
                    weight = j.optDouble("weight", 50.0).coerceAtLeast(0.05),
                    cluster = if (j.has("cluster") && !j.isNull("cluster")) j.getInt("cluster") else null,
                    focalX = f?.optDouble(0, 0.5)?.toFloat() ?: 0.5f,
                    focalY = f?.optDouble(1, 0.5)?.toFloat() ?: 0.5f,
                    layout = j.optString("layout", "cover"),
                    duration = j.optDouble("duration", 15.0),
                    caption = j.optString("caption").takeIf { it.isNotEmpty() && it != "null" },
                    hevc8 = abs(j.optString("hevc8").ifEmpty { null }),
                    url8 = abs(j.optString("url8").ifEmpty { null }),
                )
            }
            val lo = o.optJSONObject("loop")
            return Playlist(
                mode = o.optString("mode", "b"),
                photoSeconds = o.optDouble("photo_seconds", 12.0),
                transitionMs = o.optLong("transition_ms", 1600),
                kenBurns = o.optBoolean("ken_burns", true),
                captions = o.optBoolean("captions", true),
                clock = o.optBoolean("clock", true),
                loop = lo?.let { Loop(abs(it.getString("url"))!!, abs(it.optString("url8").ifEmpty { null }), it.optDouble("duration", 0.0)) },
                items = items,
            )
        }

        /** Network first, then the last copy that worked (so a server reboot does not blank the TV). */
        fun load(ctx: Context, url: String): Playlist? {
            val cache = File(ctx.filesDir, "playlist.json")
            try {
                val text = Http.text(url)
                val p = parse(text, url)
                cache.writeText(text)
                return p
            } catch (e: Exception) {
                Log.w(TAG, "playlist fetch failed: $e")
            }
            return try { parse(cache.readText(), url) } catch (e: Exception) { null }
        }
    }
}

object Http {
    fun open(url: String): HttpURLConnection =
        (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 4000
            readTimeout = 15000
            useCaches = false
        }

    fun text(url: String): String {
        val c = open(url)
        try {
            if (c.responseCode != 200) throw java.io.IOException("HTTP ${c.responseCode}")
            return c.inputStream.bufferedReader().readText()
        } finally { c.disconnect() }
    }
}

/**
 * Disk cache for stills: one-frame 4K streams, frames and thumbnails. It lives in filesDir (the system
 * never clears it) and holds the whole show, about 4 GB at most, so the TV keeps going with the server down.
 * Videos are streamed.
 */
class MediaCache(ctx: Context, private val maxBytes: Long = 4L shl 30) {
    private val dir = File(ctx.filesDir, "media").apply { mkdirs() }

    init {
        // the phase 1 cache lived in cacheDir; drop it once
        File(ctx.cacheDir, "media").takeIf { it.exists() }?.deleteRecursively()
    }

    private fun fileFor(url: String): File = File(dir, MessageDigest.getInstance("SHA-1").digest(url.toByteArray())
        .joinToString("") { "%02x".format(it) })

    fun has(url: String): Boolean = fileFor(url).let { it.exists() && it.length() > 0 }

    fun size(): Long = dir.listFiles()?.sumOf { it.length() } ?: 0L

    fun get(url: String): File {
        val f = fileFor(url)
        if (f.exists() && f.length() > 0) {
            f.setLastModified(System.currentTimeMillis())
            return f
        }
        val tmp = File(dir, "${f.name}.${Thread.currentThread().id}.part")
        val c = Http.open(url)
        try {
            if (c.responseCode != 200) throw java.io.IOException("HTTP ${c.responseCode} for $url")
            c.inputStream.use { inp -> tmp.outputStream().use { inp.copyTo(it, 1 shl 16) } }
        } catch (e: Exception) {
            tmp.delete(); throw e
        } finally { c.disconnect() }
        if (!tmp.renameTo(f)) tmp.delete()
        trim()
        return f
    }

    @Synchronized private fun trim() {
        val files = dir.listFiles()?.filter { !it.name.endsWith(".part") }?.sortedBy { it.lastModified() } ?: return
        var total = files.sumOf { it.length() }
        for (f in files) {
            if (total <= maxBytes) break
            total -= f.length()
            f.delete()
        }
    }
}

/**
 * Weighted pick that leans toward the best shots without annoying repeats:
 * nothing shown in the last ~half of the library (capped at 40) comes back, near-duplicate
 * clusters count as one, and two clips never play back to back.
 */
class Sampler(private val items: List<Item>, private val rnd: Random = Random.Default) {
    private val recent = ArrayDeque<Item>()
    private val window = (items.size / 2).coerceIn(1, 40)

    fun next(): Item {
        val last = recent.lastOrNull()
        val ids = recent.map { it.id }.toHashSet()
        val clusters = recent.mapNotNull { it.cluster }.toHashSet()
        var pool = items.filter { it.id !in ids && (it.cluster == null || it.cluster !in clusters) }
        if (last?.isVideo == true) pool.filter { !it.isVideo }.takeIf { it.isNotEmpty() }?.let { pool = it }
        if (pool.isEmpty()) pool = items.filter { it.id != last?.id }.ifEmpty { items }
        var r = rnd.nextDouble() * pool.sumOf { it.weight }
        var pick = pool.last()
        for (it in pool) { r -= it.weight; if (r <= 0) { pick = it; break } }
        recent.addLast(pick)
        while (recent.size > window) recent.removeFirst()
        return pick
    }
}
