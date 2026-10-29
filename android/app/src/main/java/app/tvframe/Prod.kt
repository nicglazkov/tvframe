package app.tvframe

import android.content.Context
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URL

/** Sizes locked on the tune page (4K stage pixels). Defaults are the measured Google TV values. */
data class Tune(
    val cap: Float = 48f, val cclock: Float = 152f, val gclock: Float = 250f, val tiles: Float = 62f,
    val small: Float = 42f, val temp: Float = 140f, val side: Float = 122f, val bottom: Float = 74f,
    val glow: Float = 0.6f, val glowSize: Float = 40f,
) {
    val tileScale get() = tiles / 62f
    val smallScale get() = small / 42f

    companion object {
        fun parse(o: JSONObject?): Tune {
            if (o == null) return Tune()
            fun f(k: String, d: Float) = o.optDouble(k, d.toDouble()).toFloat()
            val d = Tune()
            return Tune(f("cap", d.cap), f("cclock", d.cclock), f("gclock", d.gclock), f("tiles", d.tiles), f("small", d.small),
                f("temp", d.temp), f("side", d.side), f("bottom", d.bottom), f("glow", d.glow), f("glowsize", d.glowSize))
        }
    }
}

data class Caption(val place: String, val whenText: String, val camera: String,
                   /** Clips only: rough settings, '4K · 60 fps · Dolby Vision' (tools/clip_look.py). */
                   val settings: String = "",
                   /** Extra lines for the details panel: how it was taken ('123 mm · f/2.8 · 1/160 s · ISO 200', tools/real_exif.py). */
                   val more: List<String> = emptyList()) {
    fun oneLine() = listOf(place, whenText, camera).filter { it.isNotBlank() }.joinToString(" · ")
    /** A clip's two lines: where and when, then camera and settings (the second moves up when the first is empty). */
    fun clipLines(): Pair<String, String> {
        val a = listOf(place, whenText).filter { it.isNotBlank() }.joinToString(" · ")
        val b = listOf(camera, settings).filter { it.isNotBlank() }.joinToString(" · ")
        return if (a.isEmpty()) b to "" else a to b
    }
    companion object {
        fun parse(o: JSONObject?) = Caption(o?.optString("place").orEmpty(), o?.optString("when").orEmpty(), o?.optString("camera").orEmpty(),
            o?.optString("settings").orEmpty(), o?.optJSONArray("more")?.strings()?.filter { it.isNotBlank() }.orEmpty())
    }
}


data class Photo(
    val id: String, val layout: String, val portrait: Boolean, val weight: Double, val cluster: Int?, val rep: Boolean,
    val caption: Caption, val band: Float?, val date: String, val chapter: String?,
    val url: String, val hevc: String, val hevc8: String?, val thumb: String?,
    /** Where it sits on screen alone, 4K x, y, w, h (the whole screen when it fills it). */
    val rect: FloatArray? = null,
    /** A small dark map around where it was taken (tools/maps.py), when it has GPS. */
    val map: String? = null,
    /** 0..1: how bright the photo is under the text; the frame already carries a graduated ND for it (server/shade.py),
     *  and the app adds a touch more halo. 0 on older prod.json. */
    val shade: Float = 0f,
) {
    val item by lazy { Item(id, "photo", url, hevc, null, weight.coerceAtLeast(0.05), cluster, .5f, .5f, layout, 0.0, null, hevc8) }
}

data class Spread(val id: String, val kind: String, val members: List<String>, val weight: Double,
                  val url: String, val hevc: String, val hevc8: String?, val line: String = "",
                  /** Glance spreads: the caption on two lines (places, then day and camera). */
                  val head: String = "", val sub: String = "",
                  /** Glance spreads whose photos are from different places: a caption over each column instead. */
                  val labels: List<ColLabel> = emptyList(),
                  /** Glance spreads: each member's cell on screen, 4K x, y, w, h, in member order. */
                  val cells: List<FloatArray> = emptyList()) {
    val item by lazy { Item(id, "photo", url, hevc, null, weight, null, .5f, .5f, "mat", 0.0, null, hevc8) }
}

/** One column's caption on a glance spread, in 4K stage pixels (prod_build.py column_labels). */
data class ColLabel(val x: Float, val w: Float, val head: String, val sub: String)

data class Clip(val id: String, val url: String, val url8: String?, val duration: Double, val caption: Caption,
                val map: String? = null, val rect: FloatArray? = null,
                /** How often it comes up relative to the other clips (prod_build.py clip_weight); null on older prod.json. */
                val weight: Double? = null,
                /** The loop's first frame, 1080p JPEG (tools/clip_loops.py); null for a plain clip. */
                val poster: String? = null,
                /** Seconds on screen: whole plays of the loop, at most 3 and about 30 s (prod_build.py clip_show_s). */
                val showS: Double? = null) {
    val item by lazy { Item(id, "video", url, null, null, 1.0, null, .5f, .5f, "cover", duration, null, null, url8, poster, showS?.let { (it * 1000).toLong() }) }
}

/** prod.json from the frame server (server/prod_build.py): everything the TV shows. */
data class Prod(
    val photoSeconds: Double, val fadeGlance: Long, val tune: Tune,
    val photos: List<Photo>, val clips: List<Clip>,
    /** Pairs and trios laid out for glance: photos above the clock and tile row (prod_build.py glance_pair/trio). */
    val glanceSpreads: List<Spread> = emptyList(),
    /** Clips per 100 slides, from the manager's Photos and clips slider (prod.json clip_share). */
    val clipShare: Double = 11.0,
) {
    val byId = photos.associateBy { it.id }

    /** Every still the TV may need, for the offline cache. */
    fun allStills(): List<Item> = photos.map { it.item } + glanceSpreads.map { it.item }

    companion object {
        private fun abs(base: String, u: String?): String? = u?.takeIf { it.isNotEmpty() && it != "null" }?.let { URL(URL(base), it).toString() }

        fun parse(text: String, base: String): Prod {
            val o = JSONObject(text)
            val fades = o.optJSONObject("transition_ms")
            val photos = o.optJSONArray("items").objs().map { j ->
                Photo(j.getString("id"), j.optString("layout", "cover"), j.optBoolean("portrait"), j.optDouble("weight", 50.0),
                    if (j.isNull("cluster")) null else j.optInt("cluster"), j.optBoolean("rep", true), Caption.parse(j.optJSONObject("caption")),
                    if (j.isNull("band")) null else j.optDouble("band").toFloat(), j.optString("date"), j.optString("chapter").takeIf { it.isNotEmpty() && it != "null" },
                    abs(base, j.getString("url"))!!, abs(base, j.getString("hevc"))!!, abs(base, j.optString("hevc8")), abs(base, j.optString("thumb")),
                    j.optJSONArray("rect")?.floats(), abs(base, j.optString("map")), j.optDouble("shade", 0.0).toFloat().coerceIn(0f, 1f))
            }
            fun spreads(key: String) = o.optJSONArray(key).objs().map { j ->
                Spread(j.getString("id"), j.getString("kind"), j.getJSONArray("members").strings(), j.optDouble("weight", 50.0), abs(base, j.getString("url"))!!, abs(base, j.getString("hevc"))!!, abs(base, j.optString("hevc8")),
                    j.optString("line"), j.optString("head"), j.optString("sub"),
                    j.optJSONArray("labels").objs().map { l ->
                        ColLabel(l.getDouble("x").toFloat(), l.getDouble("w").toFloat(), l.optString("head"), l.optString("sub"))
                    },
                    j.optJSONArray("cells").let { c -> if (c == null) emptyList() else (0 until c.length()).mapNotNull { c.optJSONArray(it)?.floats() } })
            }
            val clips = o.optJSONArray("videos").objs().map { j ->
                Clip(j.getString("id"), abs(base, j.getString("url"))!!, abs(base, j.optString("url8")), j.optDouble("duration", 15.0), Caption.parse(j.optJSONObject("caption")),
                    abs(base, j.optString("map")), j.optJSONArray("rect")?.floats(),
                    j.optDouble("weight").takeIf { !it.isNaN() && it > 0 }, abs(base, j.optString("poster")),
                    j.optDouble("show_s").takeIf { !it.isNaN() && it > 0 })
            }
            return Prod(o.optDouble("photo_seconds", 60.0), fades?.optLong("glance", 2400) ?: 2400,
                Tune.parse(o.optJSONObject("tune")), photos, clips, spreads("glance_spreads"), o.optDouble("clip_share", 11.0))
        }

        /** Network first, then the last copy that worked, so the TV keeps going when the server is down. */
        fun load(ctx: Context, url: String): Prod? {
            val cache = File(ctx.filesDir, "prod.json")
            try {
                val text = Http.text(url)
                val p = parse(text, url)
                cache.writeText(text)
                return p
            } catch (e: Exception) {
                Log.w(TAG, "prod.json fetch failed: $e")
            }
            return try { parse(cache.readText(), url).also { Log.i(TAG, "using cached prod.json") } } catch (e: Exception) { null }
        }
    }
}

fun JSONArray?.objs(): List<JSONObject> = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }
fun JSONArray.strings(): List<String> = (0 until length()).map { getString(it) }
fun JSONArray.floats(): FloatArray = FloatArray(length()) { getDouble(it).toFloat() }

/** Live data (weather, air, sun, news, home) from the bridge, cached on disk for offline use. */
object Live {
    @Volatile var data: JSONObject? = null; private set
    private var lastFetch = 0L

    fun cached(ctx: Context): JSONObject? {
        data?.let { return it }
        return try { JSONObject(File(ctx.filesDir, "live.json").readText()).also { data = it } } catch (e: Exception) { null }
    }

    /** Blocking. Returns fresh data, or the cached copy when the bridge is unreachable. */
    fun refresh(ctx: Context): JSONObject? {
        try {
            val text = Http.text(Prefs(ctx).bridgeUrl.trimEnd('/') + "/live")
            val o = JSONObject(text)
            File(ctx.filesDir, "live.json").writeText(text)
            data = o
            lastFetch = System.currentTimeMillis()
            return o
        } catch (e: Exception) {
            Log.w(TAG, "live fetch failed: $e")
        }
        return cached(ctx)
    }
}
