package app.tvframe

import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Path
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.View
import android.widget.FrameLayout

import org.json.JSONObject
import java.io.File
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.concurrent.Executors
import java.util.concurrent.Future
import kotlin.math.abs
import kotlin.random.Random

val LA: ZoneId = BuildConfig.TVFRAME_TZ.takeIf { it.isNotEmpty() }?.let { ZoneId.of(it) } ?: ZoneId.systemDefault()
/** Clock and tile changes dissolve over this long. */
const val ROW_FADE_MS = 900L
fun laNow(): ZonedDateTime = ZonedDateTime.now(LA)
private val MONTHS = listOf("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
private val DAYS = listOf("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
fun fmtClock(t: ZonedDateTime) = "${(t.hour % 12).let { if (it == 0) 12 else it }}:${"%02d".format(t.minute)}"
fun fmtDay(t: ZonedDateTime) = "${DAYS[t.dayOfWeek.value - 1]}, ${MONTHS[t.monthValue - 1]} ${t.dayOfMonth}"
fun fmtAt(mins: Int): String { val h = mins / 60; val m = mins % 60; return "${(h % 12).let { if (it == 0) 12 else it }}:${"%02d".format(m)} ${if (h < 12) "AM" else "PM"}" }
fun fmtIn(mins: Int) = if (mins < 60) "in $mins min" else Math.round(mins / 60.0).toInt().let { "in $it ${if (it == 1) "hour" else "hours"}" }
fun toMin(hhmm: String?): Int? = hhmm?.split(":")?.takeIf { it.size >= 2 }?.let { (it[0].toIntOrNull() ?: return null) * 60 + (it[1].toIntOrNull() ?: return null) }
fun fmtDate(iso: String): String = iso.split("-").takeIf { it.size == 3 }?.let { "${MONTHS[it[1].toInt() - 1]} ${it[2].toInt()}, ${it[0]}" } ?: ""

/** What goes on screen next, and why. */
/** A clip plays twice, or three times when it is [CLIP_SHORT_MS] or shorter, always to the end of a play (see
 *  advanceRunnable). The loops are seamless (tools/clip_loops.py). prod.json's show_s wins when present. */
const val CLIP_SHORT_MS = 15_000L
fun clipShowMs(item: Item): Long {
    item.showMs?.let { return it }
    // the loop file is the clip minus its dissolve (clip_loops.py fade_len)
    val len = ((if (item.poster != null) item.duration - minOf(1.6, item.duration / 4) else item.duration) * 1000).toLong().coerceAtLeast(1_000L)
    return len * (if (len <= CLIP_SHORT_MS) 3 else 2)
}
/** The details panel (OK) closes by itself after this long. */
const val DETAILS_OPEN_MS = 30_000L
/** After the details close, the slide stays this long with no key pressed before the show moves on. */
const val DETAILS_AFTER_MS = 10_000L

class Slide(val item: Item, val kind: String, val photo: Photo? = null, val spread: Spread? = null, val clip: Clip? = null)

/**
 * Picks slides. Guests mixes full-screen photos with pairs and trios the way guests.html does;
 * Glance shows only photos that fill the screen. Weighted toward the best shots, no repeats within
 * about half the library, near-duplicates count as one.
 */
class Sequencer(private val prod: Prod, private val mode: String, private val singles: Boolean = false, private val rnd: Random = Random.Default) {
    private val shown = prod.photos.filter { it.rep }
    /** Behind the home screen: one big photo at a time, only ones that fill the screen. */
    private val big = shown.filter { !it.portrait && it.layout == "cover" }.ifEmpty { shown }
    private val recent = ArrayDeque<String>()
    private val recentClusters = ArrayDeque<Int>()
    private val mem = (shown.size * 0.55).toInt().coerceAtLeast(1)
    private val pairsOf = HashMap<String, MutableList<Spread>>()
    private val trioOf = HashMap<String, MutableList<Spread>>()
    private var n = 0
    private var lastWasClip = false
    private var lastClip: String? = null
    private val clipShare = prod.clipShare.coerceIn(0.0, 100.0) / 100.0
    private var clipCredit = 0.0

    /** True when this slide should be a clip: clipShare of every 100 slides, spread out evenly. */
    private fun clipDue(): Boolean {
        if (prod.clips.isEmpty() || clipShare <= 0.0) return false
        clipCredit += clipShare
        if (clipCredit < 1.0) return false
        clipCredit -= 1.0
        return true
    }

    private val gpairsOf = HashMap<String, MutableList<Spread>>()
    private val gtrioOf = HashMap<String, MutableList<Spread>>()

    init {
        for (s in prod.spreads) {
            if (s.kind == "pair") s.members.forEach { pairsOf.getOrPut(it) { ArrayList() } += s }
            else if (s.kind == "trio") trioOf.getOrPut(s.members[0]) { ArrayList() } += s
        }
        for (s in prod.glanceSpreads) {
            if (s.kind == "gpair" || s.kind == "gtall") s.members.forEach { gpairsOf.getOrPut(it) { ArrayList() } += s }
            else if (s.kind == "gtrio") gtrioOf.getOrPut(s.members[0]) { ArrayList() } += s
        }
    }

    /** Glance: every photo can come up. Tall ones mostly arrive as a pair, now and then a wide one leads a trio,
     *  and anything else that does not fill the screen shows alone on its blurred backdrop. */
    private fun nextGlance(): Slide? {
        // a muted clip as often as the manager's slider says, not one seen recently, the better ones more often
        if (clipDue()) {
            n++; lastWasClip = true
            val c = pickClip(); lastClip = c.id
            recent.addLast(c.id); while (recent.size > mem) recent.removeFirst()
            return Slide(c.item, "video", clip = c)
        }
        lastWasClip = false
        val p = pick(shown) ?: return null
        n++
        if (p.portrait) {
            // fewest recently seen partners first; between equals, three across beats a pair (fills the screen)
            val sp = gpairsOf[p.id]?.sortedWith(compareBy<Spread>({ s -> s.members.count { it != p.id && it in recent } },
                { if (it.kind == "gtall") 0 else 1 }))?.firstOrNull()
            if (sp != null && n % 4 != 0) { sp.members.forEach { mark(it) }; return Slide(sp.item, "pair", spread = sp) }
        } else if (n % 7 == 0) {
            val sp = gtrioOf[p.id]?.firstOrNull { s -> s.members.drop(1).none { it in recent } }
            if (sp != null) { sp.members.forEach { mark(it) }; return Slide(sp.item, "trio", spread = sp) }
        }
        mark(p.id)
        return Slide(p.item, "full", photo = p)
    }

    /** Clip weights from prod.json (clip score x how often); a clip without one gets the median of the others, or 1. */
    private val clipWeight: Map<String, Double> = run {
        val known = prod.clips.mapNotNull { it.weight }.sorted()
        val fallback = if (known.isEmpty()) 1.0 else known[known.size / 2]
        prod.clips.associate { it.id to (it.weight ?: fallback).coerceAtLeast(0.01) }
    }

    /** Weighted pick among clips not seen recently (all of them when every clip was). */
    internal fun pickClip(): Clip {
        val pool = prod.clips.filter { it.id !in recent }.ifEmpty { prod.clips.filter { it.id != lastClip }.ifEmpty { prod.clips } }
        var r = rnd.nextDouble() * pool.sumOf { clipWeight[it.id] ?: 1.0 }
        for (c in pool) { r -= clipWeight[c.id] ?: 1.0; if (r <= 0) return c }
        return pool.last()
    }

    private fun mark(id: String) {
        recent.addLast(id); while (recent.size > mem) recent.removeFirst()
        prod.byId[id]?.cluster?.let { recentClusters.addLast(it); while (recentClusters.size > mem) recentClusters.removeFirst() }
    }

    private fun pick(pool: List<Photo>): Photo? {
        var cands = pool.filter { it.id !in recent && (it.cluster == null || it.cluster !in recentClusters) }
        if (cands.isEmpty()) cands = pool.filter { it.id !in recent }
        if (cands.isEmpty()) cands = pool
        if (cands.isEmpty()) return null
        var r = rnd.nextDouble() * cands.sumOf { it.weight.coerceAtLeast(0.05) }
        for (p in cands) { r -= p.weight.coerceAtLeast(0.05); if (r <= 0) return p }
        return cands.last()
    }

    fun next(): Slide? {
        if (singles) { val p = pick(big) ?: return null; n++; mark(p.id); return Slide(p.item, "full", photo = p) }
        if (mode == ModeStore.GLANCE) return nextGlance()
        val forced = listOf("full", "pair", "full", "trio").getOrNull(n)
        if (clipDue()) {
            n++; lastWasClip = true
            val c = prod.clips[rnd.nextInt(prod.clips.size)]
            return Slide(c.item, "video", clip = c)
        }
        lastWasClip = false
        val p = when (forced) {
            "pair" -> pick(shown.filter { it.portrait && pairsOf[it.id] != null })
            "trio" -> pick(shown.filter { trioOf[it.id] != null })
            "full" -> pick(shown.filter { !it.portrait && it.layout == "cover" })
            else -> null
        } ?: pick(shown) ?: return null
        n++
        if (p.portrait && (forced == "pair" || n % 5 != 0)) {
            val sp = pairsOf[p.id]?.sortedBy { s -> s.members.count { it != p.id && it in recent } }?.firstOrNull()
            if (sp != null) { sp.members.forEach { mark(it) }; return Slide(sp.item, "pair", spread = sp) }
        }
        if (!p.portrait && (forced == "trio" || (forced == null && n % 4 == 0))) {
            val sp = trioOf[p.id]?.firstOrNull { s -> s.members.drop(1).none { it in recent } } ?: trioOf[p.id]?.firstOrNull()
            if (sp != null) { sp.members.forEach { mark(it) }; return Slide(sp.item, "trio", spread = sp) }
        }
        mark(p.id)
        return Slide(p.item, "full", photo = p)
    }
}

/** Stroke icons from glance.js, on a 48 grid. */
object Icons {
    private fun p(d: String): Path = PathData.parse(d)
    private fun circle(cx: Float, cy: Float, r: Float) = Path().apply { addCircle(cx, cy, r, Path.Direction.CW) }
    val sun by lazy { listOf(circle(24f, 24f, 8f), p("M24 6v5M24 37v5M6 24h5M37 24h5M11.3 11.3l3.5 3.5M33.2 33.2l3.5 3.5M11.3 36.7l3.5-3.5M33.2 14.8l3.5-3.5")) }
    val moon by lazy { listOf(p("M32 30a12 12 0 0 1-14-18 13 13 0 1 0 18 14 12 12 0 0 1-4 4z")) }
    val cloud by lazy { listOf(p("M15 35h19a8 8 0 0 0 .5-16A11 11 0 0 0 13.6 22 6.5 6.5 0 0 0 15 35z")) }
    val part by lazy { listOf(p("M18 12.5a7 7 0 0 1 11.5 4.5M14 9l1.8 2.4M24 5.5v3M7 18l3 .8"), p("M15 38h19a7.5 7.5 0 0 0 .4-15A10 10 0 0 0 15.4 26 6 6 0 0 0 15 38z")) }
    val rain by lazy { listOf(p("M15 30h19a8 8 0 0 0 .5-16A11 11 0 0 0 13.6 17 6.5 6.5 0 0 0 15 30z"), p("M17 35l-2 5M25 35l-2 5M33 35l-2 5")) }
    val fog by lazy { listOf(p("M8 20h32M12 27h28M8 34h26")) }
    fun weather(code: Int, day: Boolean) = when { code <= 1 -> if (day) sun else moon; code == 2 -> part; code == 3 -> cloud; code < 50 -> fog; else -> rain }
}

/**
 * The slideshow with its text: photos on the video plane at 4K (one-frame HEVC stills on two
 * SurfaceViews that crossfade in SurfaceFlinger), text on the UI plane in Google Sans at the
 * locked sizes. Used by the screensaver, the show activity and (role HOME_BG) behind the home screen.
 */
class FrameView(ctx: Context, private val role: Role = Role.SHOW, private val label: String = "dream") : FrameLayout(ctx), Callbacks {
    enum class Role { SHOW, HOME_BG }

    companion object {
        @Volatile private var precacheStarted = false

        /** The show on screen right now, if any (the star button toggles it in place). */
        @Volatile var active: FrameView? = null
    }

    private val prefs = Prefs(ctx)
    private val main = Handler(Looper.getMainLooper())
    private val net = Executors.newSingleThreadExecutor { Thread(it, "tvframe-net").apply { isDaemon = true } }
    private val io = Executors.newSingleThreadExecutor { Thread(it, "tvframe-decode").apply { isDaemon = true } }
    private val cache = MediaCache(ctx)
    private var renderer: Renderer? = null
    private var prod: Prod? = null
    private var seq: Sequencer? = null
    private var next: Slide? = null
    private var nextFile: Future<File?>? = null
    private var current: Slide? = null
    private var stopped = false
    private var failures = 0
    private var shownCount = 0
    private val t0 = SystemClock.elapsedRealtime()
    var mode: String = ModeStore.GLANCE; private set
    var onPhoto: ((Photo?) -> Unit)? = null

    private val scrim = View(ctx).apply { setBackgroundColor(Color.BLACK); alpha = 0f }
    /** A faint line along the bottom edge that fills over a clip's time on screen, so a looping clip shows where it is. */
    private val clipBar = View(ctx).apply { setBackgroundColor(0x59FFFFFF); alpha = 0f; pivotX = 0f; scaleX = 0f }
    private var clipBarAnim: android.animation.ObjectAnimator? = null
    private val scene = SceneView(ctx)
    private val textRoot = Group()
    private val clockG = Group()
    private var capG = Group()
    private val rowG = Group()
    private val glanceCap = TextNode("", TStyle(Fonts.Face.SANS_MEDIUM, 48f), 200f, 150f, anchorBaseline = false)
    /** Second caption line under glanceCap: day and camera for pairs and trios, empty for single photos. */
    private val glanceSub = TextNode("", TStyle(Fonts.Face.SANS, 42f), 200f, 220f, anchorBaseline = false)
    /** Per-column captions for spreads whose photos are from different places (Spread.labels). */
    private val glanceCols = Group()
    private val glanceCapG = Group().apply { add(glanceCap); add(glanceSub); add(glanceCols) }
    private var night = false
    private var tileIx = 0
    private var newsIx = 0
    private var otdThumb: android.graphics.Bitmap? = null
    private val tune get() = prod?.tune ?: Tune()

    // the timed move on: a clip is never cut mid-play: the play under way becomes its last, and the next slide fades in over its end
    private var finishTok = 0
    private val advanceRunnable = Runnable {
        val r = renderer as? PlaneRenderer
        if (r != null && r.clipDurationMs() > 0) { val t = ++finishTok; r.finishClip { if (t == finishTok) advance() } } else advance()
    }

    // photo details (OK on the remote), what is on screen for the photo manager, and its skip buttons
    private val mapIo = Executors.newSingleThreadExecutor { Thread(it, "tvframe-maps").apply { isDaemon = true } }
    private val ctl = Executors.newSingleThreadExecutor { Thread(it, "tvframe-ctl").apply { isDaemon = true } }
    private val details: DetailsOverlay? = if (role == Role.SHOW) DetailsOverlay(ctx) { url, cb -> loadMap(url, cb) } else null
    private val history = ArrayList<Slide>()
    private var goingBack = false
    private var shownAt = 0L
    private var holdMs = 0L
    private var pausedLeft = -1L
    private val detailsTimeout = Runnable { closeDetails() }
    private val heartbeat = object : Runnable { override fun run() { report(); main.postDelayed(this, 60_000) } }
    private val tick = object : Runnable { override fun run() { tickInfo(); main.postDelayed(this, 15_000) } }
    private val liveTick = object : Runnable { override fun run() { refreshLive(); main.postDelayed(this, 5 * 60_000L) } }
    private val prodTick = object : Runnable { override fun run() { refreshProd(); main.postDelayed(this, 30 * 60_000L) } }
    private val swapTick = object : Runnable { override fun run() { rotateTile(); main.postDelayed(this, 90_000) } }
    private val shiftTick = object : Runnable { override fun run() { burnIn(); main.postDelayed(this, 75_000) } }
    private val modeListener: (String) -> Unit = { _ -> }

    init {
        setBackgroundColor(Color.BLACK)
        addView(scrim, LayoutParams(-1, -1))
        if (role == Role.SHOW) {
            addView(scene, LayoutParams(-1, -1))
            addView(clipBar, LayoutParams(-1, 2, android.view.Gravity.BOTTOM))
            addView(details, LayoutParams(-1, -1))
            scene.root.add(textRoot)
            textRoot.add(rowG); textRoot.add(clockG)
        }
    }

    fun start() {
        if (role == Role.SHOW) {
            active = this
            Live.cached(context)
        }
        net.execute {
            val p = Prod.load(context, prefs.prodUrl)
            if (role == Role.SHOW) Live.refresh(context)
            main.post { if (!stopped) begin(p) }
        }
    }

    private fun begin(p: Prod?) {
        if (p == null || p.photos.isEmpty()) {
            Log.w(TAG, "[$label] no prod.json yet, retry in 30 s")
            main.postDelayed({ if (!stopped) start() }, 30_000)
            return
        }
        prod = p
        val fade = if (mode == ModeStore.GUESTS) p.fadeGuests else p.fadeGlance
        val any = p.photos.first().item
        val r: Renderer = if (StillCodec.pick(prefs.codec, any) != null) PlaneRenderer(context, io, main, fade, prefs.codec) { veilFile(it) }
            else BitmapRenderer(context, io, main, false, { (p.photoSeconds * 1000).toLong() }, fade)
        renderer = r
        addView(r.root, 0, LayoutParams(-1, -1))
        scene.glow = Glow(p.tune.glow, p.tune.glowSize)
        seq = Sequencer(p, mode, singles = role == Role.HOME_BG)
        Log.i(TAG, "[$label] start role=$role mode=$mode path=${r.path} photos=${p.photos.size} spreads=${p.spreads.size} " +
            "fonts=${Fonts.loaded} startup=${SystemClock.elapsedRealtime() - t0}ms")
        if (role == Role.SHOW) {
            buildChrome()
            textRoot.alpha = 0f
            main.post(tick)
            main.postDelayed(liveTick, 5 * 60_000L)
            main.postDelayed(swapTick, 90_000)
            // no burn-in drift: you want the text to stay put, and the TV it was built on is LCD (no burn-in)
        }
        main.postDelayed(prodTick, 30 * 60_000L)
        main.postDelayed(heartbeat, 60_000)
        startCommands()
        advance()
    }

    // ---------------------------------------------------------------------------------------------
    // slides

    /** The 1080p picture the renderer fades in on the UI plane: a still's frame JPEG, a clip's first frame. */
    private fun veilUrl(item: Item): String? = if (item.isVideo) item.poster else item.url

    /** Blocking (renderer's decode thread): the veil picture from the cache, fetched if prefetch has not got it yet. */
    private fun veilFile(item: Item): File? = veilUrl(item)?.let { u -> try { cache.get(u) } catch (e: Exception) { Log.w(TAG, "[$label] veil fetch failed: $e"); null } }

    private fun prefetch(s: Slide): Future<File?> {
        val veil = veilUrl(s.item)
        if (s.item.isVideo) {
            // clips are fetched whole during the slide before, so they start at once and never stall on the network;
            // if that fails the renderer streams the url as before
            val url = StillCodec.clipUrl(s.item).takeIf { renderer is PlaneRenderer }
            return net.submit<File?> {
                veil?.let { runCatching { cache.get(it) } }
                url?.let { u -> try { cache.get(u) } catch (e: Exception) { Log.w(TAG, "[$label] clip prefetch failed, will stream: $e"); null } }
            }
        }
        val url = renderer?.stillUrl(s.item)
        return net.submit<File?> { veil?.let { runCatching { cache.get(it) } }; url?.let { cache.get(it) } }
    }

    private fun advance() {
        main.removeCallbacks(advanceRunnable); finishTok++
        val sq = seq ?: return
        leaving = current
        val s = next ?: sq.next() ?: return
        val fut = nextFile ?: prefetch(s)
        next = null; nextFile = null
        val tReq = SystemClock.elapsedRealtime()
        net.execute {
            val file = try { fut.get() } catch (e: Exception) { main.post { onFailed(s.item, e) }; return@execute }
            val waited = SystemClock.elapsedRealtime() - tReq
            main.post {
                if (stopped) return@post
                if (waited > 50) Log.i(TAG, "[$label] waited ${waited}ms for ${s.item.id}")
                pending = s
                renderer?.show(s.item, file, this)
            }
        }
    }

    private var pending: Slide? = null
    private var capAnim: android.animation.ValueAnimator? = null
    private var capSwap = Runnable { }
    /** The slide on screen when the next one was asked for (a clip that ends after that must not advance again). */
    private var leaving: Slide? = null
    private var glowAnim: android.animation.ValueAnimator? = null

    override fun onFadeStart(item: Item) {
        val s = pending?.takeIf { it.item.id == item.id } ?: return
        current = s
        afterDetails = false
        if (!goingBack) { history += s; if (history.size > 40) history.removeAt(0) }
        goingBack = false
        report()
        // a new slide while the details are up (photo manager skip): the cards slide away with the old photo
        details?.let { if (it.isOpen) closeDetails(instant = false, resume = false) }
        if (role == Role.SHOW) {
            fadeGlow(s)
            showText(s)
            clipBarFor(s)
            // first slide of a show: the clock and tiles arrive with the photo instead of sitting on black
            if (shownCount == 0 && textRoot.alpha < 1f) scene.fade(textRoot, 1f, fadeMs(), ease = EASE_XFADE)
        } else onPhoto?.invoke(s.photo)
    }

    private fun fadeMs(): Long = (renderer as? PlaneRenderer)?.transitionMs ?: (prod?.fadeGlance ?: 2400)

    /** Bright photos (Photo.shade, server/shade.py) get a slightly stronger halo; it eases over with the crossfade. */
    private fun fadeGlow(s: Slide) {
        val k = s.photo?.shade ?: 0f
        val to = Glow(tune.glow + 0.15f * k, tune.glowSize + 12f * k)
        val from = scene.glow
        if (from == to) return
        glowAnim?.cancel()
        glowAnim = android.animation.ValueAnimator.ofFloat(0f, 1f).apply {
            duration = fadeMs(); interpolator = EASE_XFADE
            addUpdateListener {
                val t = it.animatedValue as Float
                scene.glow = Glow(from.alpha + (to.alpha - from.alpha) * t, from.size4k + (to.size4k - from.size4k) * t)
                scene.invalidate()
            }
            start()
        }
    }

    override fun onShown(item: Item, stats: String) {
        if (stopped) return
        failures = 0
        shownCount++
        Log.i(TAG, "[$label] shown #$shownCount mode=$mode ${current?.kind} ${item.id} $stats")
        if (shownCount == 1 && !precacheStarted) { precacheStarted = true; startPrecache() }
        val sq = seq ?: return
        next = sq.next()?.also { nextFile = prefetch(it) }
        val p = prod ?: return
        // counted from where playback is: a clip with a poster waits on its first frame through the fade-in, one without
        // is already a fade into its first play. Shortly before the last play ends, advanceRunnable lets it finish
        // and fades out from its last frame
        val played = if (item.isVideo) (renderer as? PlaneRenderer)?.clipPositionMs()?.toLong() ?: 0L else 0L
        val hold = if (item.isVideo) (clipShowMs(item) - played - CLIP_FINAL_MS).coerceAtLeast(2_000L) else (p.photoSeconds * 1000).toLong()
        shownAt = SystemClock.elapsedRealtime(); holdMs = hold
        if (item.isVideo && role == Role.SHOW) runClipBar(hold + CLIP_FINAL_MS - CLIP_LEAD_MS + fadeMs())
        if (details?.isOpen == true) { pausedLeft = hold; clipBarAnim?.pause() } else main.postDelayed(advanceRunnable, hold)
    }

    /** A clip's bar fades in with it at zero; any other slide fades the bar out with the clip it replaces. */
    private fun clipBarFor(s: Slide) {
        if (s.item.isVideo) {
            clipBarAnim?.cancel(); clipBarAnim = null
            clipBar.animate().cancel()
            clipBar.scaleX = 0f
            clipBar.animate().alpha(1f).setDuration(fadeMs()).setInterpolator(EASE_XFADE).start()
        } else if (clipBar.alpha > 0f) {
            clipBar.animate().cancel()
            clipBar.animate().alpha(0f).setDuration(fadeMs()).setInterpolator(EASE_XFADE).withEndAction {
                if (current?.item?.isVideo != true) { clipBarAnim?.cancel(); clipBarAnim = null; clipBar.scaleX = 0f }
            }.start()
        }
    }

    /** Fill the clip bar from where it is to full over [ms] (linear: it is a clock). */
    private fun runClipBar(ms: Long) {
        clipBarAnim?.cancel()
        clipBarAnim = android.animation.ObjectAnimator.ofFloat(clipBar, View.SCALE_X, clipBar.scaleX, 1f).apply {
            duration = ms.coerceAtLeast(1L); interpolator = android.view.animation.LinearInterpolator(); start()
        }
    }

    override fun onEnded(item: Item) {
        // a clip reached its end before its planned crossfade (shorter than prod.json said): move on now, once
        if (stopped || current?.item?.id != item.id || leaving === current) return
        if (details?.isOpen == true) return     // held while the details are up; closing them resumes the show
        advance()
    }

    override fun onFailed(item: Item, e: Throwable) {
        if (stopped) return
        failures++
        Log.w(TAG, "[$label] failed ${item.id}: $e")
        main.removeCallbacks(advanceRunnable)
        main.postDelayed(advanceRunnable, if (failures > 5) 10_000L else 300L)
    }

    private fun switchMode(m: String) {
        val p = prod ?: run { mode = m; return }
        mode = m
        (renderer as? PlaneRenderer)?.transitionMs = if (m == ModeStore.GUESTS) p.fadeGuests else p.fadeGlance
        seq = Sequencer(p, m, singles = role == Role.HOME_BG)
        next = null; nextFile = null
        buildChrome()
        advance()
    }

    // ---------------------------------------------------------------------------------------------
    // offline cache: every still for both modes, fetched slowly in the background

    private fun startPrecache() {
        val p = prod ?: return
        val r = renderer ?: return
        val urls = p.allStills().mapNotNull { r.stillUrl(it) }
        Thread {
            var got = 0; var bytes = 0L
            val t = SystemClock.elapsedRealtime()
            for (u in urls) {
                try {
                    val had = cache.has(u)
                    val f = cache.get(u)
                    if (!had) { got++; bytes += f.length(); Thread.sleep(150) }
                } catch (e: Exception) { Log.w(TAG, "precache $u: $e") }
            }
            Log.i(TAG, "[$label] offline cache: ${urls.size} stills ready, fetched $got (${bytes / 1_000_000} MB) in ${(SystemClock.elapsedRealtime() - t) / 1000}s, total ${cache.size() / 1_000_000} MB")
        }.apply { isDaemon = true; priority = Thread.MIN_PRIORITY; name = "tvframe-precache" }.start()
    }

    // ---------------------------------------------------------------------------------------------
    // text: guests

    private fun corner() = TStyle(Fonts.Face.SANS_MEDIUM, tune.cap)

    private fun buildChrome() {
        clockG.clear(); rowG.clear()
        capG.alpha = 0f
        if (mode == ModeStore.GUESTS) {
            setNight(false, animate = false)
            clockG.add(TextNode(fmtClock(laNow()), TStyle(Fonts.Face.SANS, tune.cclock), 3840f - tune.side * 0.46f, 2160f - tune.bottom, alignRight = true))
        } else {
            buildGlanceRow()
            rowG.add(glanceCapG)
            glanceCapG.alpha = 0f
        }
        current?.let { showText(it) }
        scene.invalidate()
    }

    private fun guestBlock(g: Group, b: Block) {
        val s = scene.scale
        var y = b.y
        for (l in b.lines) {
            when (l.style) {
                "t" -> { val n = ParaNode(l.text, TStyle(Fonts.Face.SANS, 64f, lineHeight = 1.12f), b.x, y, b.w, maxLines = 2); g.add(n); y += n.height(s, scene.glow) }
                "s" -> { val st = TStyle(Fonts.Face.TEXT, 42f, withAlpha(INK, .8f)); y += 14f; g.add(TextNode(l.text, st, b.x, y, anchorBaseline = false, maxW = b.w)); y += lh(st) }
                else -> { val st = TStyle(Fonts.Face.TEXT, 38f, withAlpha(INK, .62f), tracking = .015f); y += 8f; g.add(TextNode(l.text, st, b.x, y, anchorBaseline = false, maxW = b.w)); y += lh(st) }
            }
        }
    }

    private fun lh(st: TStyle): Float { val s = scene.scale; val p = paintFor(st, s, scene.glow); return lineBox(p, st, s).second / s }

    private fun showText(s: Slide) {
        if (mode == ModeStore.GUESTS) {
            val old = capG
            scene.fade(old, 0f, (prod?.fadeGuests ?: 2200)) { textRoot.kids.remove(old) }
            val g = Group().apply { alpha = 0f }
            val cap = s.photo?.caption ?: s.clip?.caption
            val band = s.photo?.band
            val clock = clockG.kids.firstOrNull() as? TextNode
            if (s.spread != null) {
                s.spread.blocks.forEach { guestBlock(g, it) }
                clock?.apply { x = 3840f - tune.side * 0.46f; y = 2160f - tune.bottom }
            } else if (cap != null && band != null) {
                // a tall photo with a wide side band: the caption sits in the band
                val st = corner()
                val n = ParaNode(cap.oneLine(), st, 200f, 0f, band, maxLines = 3, balance = false)
                val p = paintFor(st, scene.scale, scene.glow)
                val (base, lhPx) = lineBox(p, st, scene.scale)
                n.y = 2160f - 104f - n.height(scene.scale, scene.glow) + (lhPx - base) / scene.scale
                g.add(n)
                clock?.apply { x = 3840f - 200f; y = 2160f - 104f }
            } else if (cap != null) {
                g.add(TextNode(cap.oneLine(), corner(), tune.side, 2160f - tune.bottom, maxW = 3840f - tune.side - 700f))
                clock?.apply { x = 3840f - tune.side * 0.46f; y = 2160f - tune.bottom }
            }
            textRoot.kids.add(0, g)
            capG = g
            scene.fade(g, 1f, 1400, delay = 900, ease = EASE_CALM)
        } else {
            // pairs and trios: places on the first line, day and camera on the second
            val cols = s.spread?.labels.orEmpty()
            val two = s.spread?.takeIf { it.head.isNotEmpty() && cols.isEmpty() }
            // clips: where and when, then camera and rough settings
            val clipLines = s.clip?.caption?.clipLines()
            val cap = if (cols.isNotEmpty()) "" else two?.head ?: s.photo?.caption?.oneLine() ?: s.spread?.line ?: clipLines?.first
            val newSub = two?.sub ?: clipLines?.second ?: ""
            val same = cols.isEmpty() && glanceCols.kids.isEmpty() && cap.orEmpty() == glanceCap.text && newSub == glanceSub.text
            if (same && glanceCap.text.isNotEmpty()) return     // same words on the next slide: leave them be
            // the old words leave during the first ~40% of the photo crossfade and the new ones arrive over the rest,
            // on the same ease, so text and picture change together
            val t = fadeMs()
            val out = (t * 0.4f).toLong()
            capAnim?.cancel()
            capAnim = scene.fade(glanceCapG, 0f, out, ease = EASE_XFADE)
            main.removeCallbacks(capSwap)
            capSwap = Runnable {
                if (stopped || current !== s) return@Runnable
                glanceCap.text = cap ?: ""
                glanceCap.style = TStyle(Fonts.Face.SANS_MEDIUM, tune.cap)
                glanceCap.maxW = 3440f
                glanceSub.text = newSub
                glanceSub.style = TStyle(Fonts.Face.SANS, tune.cap * 0.88f, withAlpha(INK, .82f))
                glanceSub.y = glanceCap.y + lh(glanceCap.style) + 4f
                glanceSub.maxW = 3440f
                // different places: each column says where its photo is, at the same height as the shared caption
                glanceCols.kids.clear()
                for (c in cols) {
                    val h = TextNode(c.head, glanceCap.style, c.x, glanceCap.y, anchorBaseline = false, maxW = c.w)
                    glanceCols.add(h)
                    if (c.sub.isNotEmpty()) glanceCols.add(TextNode(c.sub, glanceSub.style, c.x, glanceSub.y, anchorBaseline = false, maxW = c.w))
                }
                if (!night && (glanceCap.text.isNotEmpty() || glanceCols.kids.isNotEmpty())) {
                    capAnim?.cancel()
                    capAnim = scene.fade(glanceCapG, 1f, (t - out).coerceAtLeast(600L), ease = EASE_XFADE)
                }
            }
            main.postDelayed(capSwap, out + 16)
        }
    }

    // ---------------------------------------------------------------------------------------------
    // text: glance

    private val ts get() = tune.tileScale
    private val ss get() = tune.smallScale
    private val tiles = listOf("sun", "air", "news", "otd")
    private val weatherG = Group(); private val homeG = Group(); private val swapG = Group(); private val timeG = Group()
    private val wifiG = Group()

    private fun kStyle() = TStyle(Fonts.Face.TEXT_MEDIUM, 38f * ss, withAlpha(INK, .66f))
    private fun smStyle() = TStyle(Fonts.Face.TEXT, 42f * ss, withAlpha(INK, .8f), lineHeight = 1.3f)

    /** A column built top-down, then placed so its content ends at [bottom]. */
    private inner class Stack(val x: Float, val w: Float) {
        val parts = ArrayList<Pair<Float, (Float) -> Node>>()   // height, build(top)
        var gaps = ArrayList<Float>()
        fun add(h: Float, marginTop: Float = 0f, build: (Float) -> Node) { parts += h to build; gaps += marginTop }
        fun height() = parts.indices.sumOf { (parts[it].first + gaps[it]).toDouble() }.toFloat()
        fun place(g: Group, bottom: Float): Float {
            var y = bottom - height()
            val top = y
            for (i in parts.indices) { y += gaps[i]; g.add(parts[i].second(y)); y += parts[i].first }
            return top
        }
    }

    private fun live(): JSONObject? = Live.data

    /** What a group shows and where, to tell whether a rebuild changed it. */
    private fun sig(n: Node): String = when (n) {
        is Group -> n.kids.joinToString("|", "{", "}") { sig(it) }
        is TextNode -> "T${n.text}@${n.x.toInt()},${n.y.toInt()}"
        is ParaNode -> "P${n.text}@${n.x.toInt()},${n.y.toInt()},${n.w.toInt()}"
        is RectNode -> "R${n.x.toInt()},${n.y.toInt()},${n.w.toInt()},${n.h.toInt()},${n.color}"
        is IconNode -> "C${System.identityHashCode(n.paths)}@${n.x.toInt()},${n.y.toInt()}"
        is ImageNode -> "I${System.identityHashCode(n.bmp)}@${n.x.toInt()},${n.y.toInt()}"
        else -> n.javaClass.simpleName
    }

    /**
     * Rebuild the clock and tile row. With [animate], every column whose content or place changed dissolves from the
     * old version to the new one (the old one is kept as a ghost that fades out), so a minute ticking over, a new
     * reading, a tile rotating or a column coming or going never pops.
     */
    private fun buildGlanceRow(animate: Boolean = false) {
        val groups = listOf(timeG, wifiG, weatherG, homeG, swapG)
        val before = groups.associateWith { Triple(ArrayList(it.kids), sig(it), it.alpha) }
        val ghosts = rowG.kids.filter { it is Group && it.javaClass == Group::class.java && it !== glanceCapG && it !in groups }
        rowG.kids.removeAll { it !== glanceCapG && it !in ghosts }
        timeG.clear(); weatherG.clear(); homeG.clear(); swapG.clear(); wifiG.clear()
        val bottom = 2160f - 170f
        val warm = 0xC7FFD6AA.toInt()
        // time and date, bottom left
        val dateSt = TStyle(Fonts.Face.TEXT_MEDIUM, 52f * ss, if (night) warm else withAlpha(INK, .84f))
        val timeSt = TStyle(Fonts.Face.SANS, tune.gclock, if (night) warm else INK, lineHeight = .8f)
        val n = laNow()
        val dTop = bottom - lh(dateSt)
        timeG.add(TextNode(fmtDay(n), dateSt, 200f, dTop, anchorBaseline = false))
        timeG.add(TextNode(fmtClock(n), timeSt, 200f, dTop - 44f - lh(timeSt), anchorBaseline = false))
        rowG.add(timeG)

        // columns from the right edge: weather, home, the rotating tile (calendar is not wired, so it is left out)
        val cols = ArrayList<Pair<Group, Float>>()
        val lv = live()
        val wifi = lv?.optJSONObject("wifi")?.takeIf { (it.optJSONArray("qr")?.length() ?: 0) > 0 }
        if (wifi != null) cols += wifiG to 720f
        if (lv?.optJSONObject("weather") != null) cols += weatherG to 480f
        val homeRows = lv?.optJSONObject("home")?.optJSONArray("rows")
        if (homeRows != null && homeRows.length() > 0) cols += homeG to 660f
        cols += swapG to 700f
        var right = 3840f - 200f
        val placed = ArrayList<Triple<Group, Float, Float>>()
        for ((g, w) in cols.reversed()) {
            val x = right - w * ts
            placed += Triple(g, x, w * ts)
            right = x - 64f * ts
        }
        for ((g, x, w) in placed) {
            val cx = x + 2f + 64f * ts
            val cw = w - 2f - 64f * ts
            val st = Stack(cx, cw)
            when (g) {
                wifiG -> wifiStack(st, wifi!!)
                weatherG -> weatherStack(st, lv!!)
                homeG -> homeStack(st, homeRows!!)
                else -> swapStack(st, tiles[tileIx % tiles.size])
            }
            val top = st.place(g, bottom - 4f)
            g.kids.add(0, RectNode(x, top, 2f, bottom - top, 0x38FFFFFF))
            rowG.add(g)
            g.alpha = if (night) 0f else 1f
        }
        if (!animate) return
        for (g in groups) {
            val (oldKids, oldSig, oldAlpha) = before.getValue(g)
            if (oldSig == sig(g) || !rowG.kids.contains(g) && oldKids.isEmpty()) continue
            val target = if (night || !rowG.kids.contains(g)) 0f else 1f
            if (oldKids.isNotEmpty() && oldAlpha > 0.01f) {
                val ghost = Group().apply { kids.addAll(oldKids); alpha = oldAlpha }
                rowG.kids.add(ghost)
                scene.fade(ghost, 0f, ROW_FADE_MS, ease = EASE_XFADE) { rowG.kids.remove(ghost); scene.invalidate() }
            }
            if (rowG.kids.contains(g)) { g.alpha = 0f; scene.fade(g, target, ROW_FADE_MS, ease = EASE_XFADE) }
        }
    }

    private fun weatherStack(st: Stack, lv: JSONObject) {
        val w = lv.getJSONObject("weather")
        val today = w.optJSONArray("days")?.optJSONObject(0)
        val kSt = kStyle()
        st.add(lh(kSt)) { y -> TextNode(lv.optString("where", "Weather"), kSt, st.x, y, anchorBaseline = false) }
        val nSt = TStyle(Fonts.Face.SANS, tune.temp, lineHeight = .86f)
        val ic = 104f * ts
        val rowH = maxOf(ic, lh(nSt))
        st.add(rowH, 20f * ts) { y ->
            Group().apply {
                add(IconNode(Icons.weather(w.optInt("code"), w.optInt("is_day", 1) == 1), st.x, y + (rowH - ic) / 2, ic))
                add(TextNode("${w.optInt("temp")}°", nSt, st.x + ic + 26f * ts, y + (rowH - lh(nSt)) / 2, anchorBaseline = false))
            }
        }
        val sm = smStyle()
        val text = w.optString("text") + "\n" + (if (today != null) "High ${today.optInt("hi")}°, low ${today.optInt("lo")}°" else "")
        val para = ParaNode(text.trim(), sm, st.x, 0f, st.w + 200f, maxLines = 2, balance = false)
        st.add(para.height(scene.scale, scene.glow), 12f * ss) { y -> para.also { it.y = y } }
    }

    /** Guest Wi-Fi: a QR code that joins the network (camera app), with the name beside it. Always on. */
    private fun wifiStack(st: Stack, wifi: JSONObject) {
        val q = 280f * ts
        val kSt = kStyle()
        val b = TStyle(Fonts.Face.TEXT_MEDIUM, 44f * ss, INK, lineHeight = 1.25f)
        val tx = st.x + q + 36f * ts; val tw = st.w - q - 36f * ts
        st.add(q) { y ->
            Group().apply {
                add(QrNode(wifi.getJSONArray("qr"), st.x, y, q))
                val h = lh(kSt) + 10f * ts + lh(b)
                var ty = y + (q - h) / 2
                add(TextNode("Guest Wi-Fi", kSt, tx, ty, anchorBaseline = false, maxW = tw)); ty += lh(kSt) + 10f * ts
                add(TextNode(wifi.optString("ssid"), b, tx, ty, anchorBaseline = false, maxW = tw))
            }
        }
    }

    private fun homeStack(st: Stack, rows: org.json.JSONArray) {
        val kSt = kStyle()
        st.add(lh(kSt)) { y -> TextNode("Home", kSt, st.x, y, anchorBaseline = false) }
        val a = TStyle(Fonts.Face.TEXT, 44f * ss, withAlpha(INK, .7f), lineHeight = 1.25f)
        val b = TStyle(Fonts.Face.TEXT_MEDIUM, 44f * ss, INK, lineHeight = 1.25f)
        val s = scene.scale
        val labels = (0 until rows.length()).map { rows.getJSONArray(it) }
        val col1 = labels.maxOf { paintFor(a, s, scene.glow).measureText(it.getString(0)) / s }
        val rowH = lh(a); val gap = 14f * ts
        val h = labels.size * rowH + (labels.size - 1) * gap
        st.add(h, 20f * ts) { y ->
            Group().apply {
                labels.forEachIndexed { i, r ->
                    val ry = y + i * (rowH + gap)
                    add(TextNode(r.getString(0), a, st.x, ry, anchorBaseline = false))
                    add(TextNode(r.getString(1), b, st.x + col1 + 40f * ts, ry, anchorBaseline = false, maxW = st.w - col1 - 40f * ts))
                }
            }
        }
    }

    private fun swapStack(st: Stack, key: String) {
        val lv = live()
        val kSt = kStyle()
        val big = TStyle(Fonts.Face.TEXT_MEDIUM, 62f * ts, lineHeight = 1.1f)
        val sm = smStyle()
        fun k(t: String) = st.add(lh(kSt)) { y -> TextNode(t, kSt, st.x, y, anchorBaseline = false, maxW = st.w) }
        fun para(t: String, style: TStyle, mt: Float, lines: Int = 2) {
            val p = ParaNode(t, style, st.x, 0f, st.w, maxLines = lines)
            st.add(p.height(scene.scale, scene.glow), mt) { y -> p.also { it.y = y } }
        }
        val n = laNow(); val mins = n.hour * 60 + n.minute
        when (key) {
            "sun" -> {
                val s = lv?.optJSONObject("sun")?.optJSONObject("today"); val tm = lv?.optJSONObject("sun")?.optJSONObject("tomorrow")
                val gs = toMin(s?.optString("golden_start")); val ss0 = toMin(s?.optString("sunset"))
                if (s == null || gs == null || ss0 == null) { k("Sun"); para("No sun times yet", sm, 12f * ss); return }
                when {
                    mins < gs -> { k("Golden hour"); para(fmtAt(gs), big, 20f * ts); para("Sunset ${fmtAt(ss0)}", sm, 12f * ss) }
                    mins < ss0 -> { k("Golden hour now"); para("Sunset ${fmtIn(ss0 - mins)}", big, 20f * ts); para("At ${fmtAt(ss0)}", sm, 12f * ss) }
                    else -> {
                        k("Tomorrow")
                        para("Sunrise ${toMin(tm?.optString("sunrise"))?.let { fmtAt(it) } ?: ""}", big, 20f * ts)
                        para("Golden hour until ${toMin(tm?.optString("golden_end"))?.let { fmtAt(it) } ?: ""}", sm, 12f * ss)
                    }
                }
            }
            "air" -> {
                val a = lv?.optJSONObject("air")
                k("Air quality")
                if (a == null) { para("No reading yet", sm, 12f * ss); return }
                val aqi = a.optInt("aqi")
                val band = when { aqi <= 50 -> "Good"; aqi <= 100 -> "Moderate"; aqi <= 150 -> "Unhealthy for some"; aqi <= 200 -> "Unhealthy"; else -> "Very unhealthy" }
                val nSt = TStyle(Fonts.Face.SANS, tune.temp * .857f, lineHeight = .86f)
                val bSt = TStyle(Fonts.Face.TEXT_MEDIUM, 50f * ss)
                val h = lh(nSt)
                st.add(h, 20f * ts) { y ->
                    Group().apply {
                        val nn = TextNode("$aqi", nSt, st.x, y, anchorBaseline = false)
                        add(nn)
                        val s = scene.scale
                        val base = y + lineBox(paintFor(nSt, s, scene.glow), nSt, s).first / s
                        add(TextNode(band, bSt, st.x + nn.width(s, scene.glow) + 28f * ts, base))
                    }
                }
                val bw = 460f * ts
                st.add(8f, 26f * ts) { y ->
                    Group().apply {
                        val c = intArrayOf(0xFF7FCF8D.toInt(), 0xFFE9CF6A.toInt(), 0xFFEC9A5B.toInt(), 0xFFD9665F.toInt())
                        for (i in 0..3) add(RectNode(st.x + i * bw / 4, y, bw / 4, 8f, c[i], if (i == 0 || i == 3) 4f else 0f))
                        val at = (aqi / 200f).coerceIn(0f, 1f)
                        add(RectNode(st.x + at * bw - 5f, y - 12f, 10f, 32f, 0x59000000))
                        add(RectNode(st.x + at * bw - 2f, y - 9f, 4f, 26f, 0xFFFFFFFF.toInt(), 2f))
                    }
                }
                para("US AQI, PM2.5 ${a.optDouble("pm25")} µg/m³", sm, 12f * ss + 14f)
            }
            "news" -> {
                val items = lv?.optJSONObject("news")?.optJSONArray("items").objs().map { it.optString("title") }.filter { it.isNotEmpty() && it.length <= 84 }
                if (items.isEmpty()) { k("Headlines"); para("No headlines yet", sm, 12f * ss); return }
                val src = lv?.optJSONObject("news")?.optString("source", "News") ?: "News"
                k("$src headlines  ${newsIx % items.size + 1} of ${items.size}")
                para(items[newsIx % items.size], TStyle(Fonts.Face.SANS, 56f * ts, lineHeight = 1.18f), 0f, lines = 3)
            }
            else -> {
                val o = otd()
                if (o == null) { k("From your photos"); para("Nothing from this time of year yet", sm, 12f * ss); return }
                k(o.second)
                val th = 170f * ts
                val gap = 40f * ts
                val mid = TStyle(Fonts.Face.TEXT_MEDIUM, 50f * ts, lineHeight = 1.15f)
                // the place's own name only ("Redwood Junction", not "Redwood Junction, Redwood City"): the column is
                // narrow, and a cut-off second line reads worse than a shorter name
                val place = o.first.caption.place.substringBefore(", ").ifBlank { o.first.caption.place }
                // the same breathing room under the label as the other tiles have
                st.add(th, 22f * ts) { y ->
                    Group().apply {
                        add(ImageNode(otdThumb, st.x, y, th, th, 14f))
                        val tx = st.x + th + gap
                        val pl = ParaNode(place, mid, tx, 0f, st.w - th - gap, maxLines = 2)
                        val ph = pl.height(scene.scale, scene.glow)
                        val dSt = smStyle()
                        val total = ph + 14f * ss + lh(dSt)
                        pl.y = y + (th - total) / 2
                        add(pl)
                        add(TextNode(fmtDate(o.first.date), dSt, tx, pl.y + ph + 14f * ss, anchorBaseline = false))
                    }
                }
                if (otdThumb == null) loadThumb(o.first)
            }
        }
    }

    /** A photo from this day (or week, or month) in an earlier year, and its label. */
    private fun otd(): Pair<Photo, String>? {
        val p = prod ?: return null
        val t = laNow()
        val cands = p.photos.filter { it.rep && it.date.length == 10 && it.date.substring(0, 4).toInt() < t.year }.map { ph ->
            val m = ph.date.substring(5, 7).toInt(); val d = ph.date.substring(8, 10).toInt()
            val a = java.time.LocalDate.of(2000, m, d); val b = java.time.LocalDate.of(2000, t.monthValue, t.dayOfMonth)
            Triple(ph, abs(java.time.temporal.ChronoUnit.DAYS.between(a, b)).toInt(), m == t.monthValue)
        }.filter { it.second <= 7 || it.third }.sortedWith(compareBy({ it.second }, { -it.first.weight }))
        val x = cands.firstOrNull() ?: return null
        val yrs = t.year - x.first.date.substring(0, 4).toInt()
        val ago = "$yrs ${if (yrs == 1) "year" else "years"} ago"
        return x.first to when { x.second == 0 -> "On this day, $ago"; x.second <= 7 -> "This week, $ago"; else -> "This month, $ago" }
    }

    private fun loadThumb(p: Photo) {
        val u = p.thumb ?: return
        net.execute {
            try {
                val f = cache.get(u)
                val b = BitmapFactory.decodeFile(f.path, BitmapFactory.Options().apply { inSampleSize = 2 })
                main.post { if (!stopped && b != null) { otdThumb = b; if (tiles[tileIx % tiles.size] == "otd") buildGlanceRow(animate = true); scene.invalidate() } }
            } catch (e: Exception) { Log.w(TAG, "thumb failed: $e") }
        }
    }

    private fun rotateTile() {
        if (mode != ModeStore.GLANCE || night) return
        tileIx++
        if (tiles[tileIx % tiles.size] == "news") newsIx++
        buildGlanceRow(animate = true)      // the old tile dissolves into the new one
    }

    private fun tickInfo() {
        val n = laNow()
        if (mode == ModeStore.GUESTS) {
            (clockG.kids.firstOrNull() as? TextNode)?.text = fmtClock(n)
        } else {
            buildGlanceRow(animate = true)
        }
        scene.invalidate()
    }

    private fun setNight(on: Boolean, animate: Boolean) {
        night = on
        val dim = if (on) .7f else 0f
        if (animate) scrim.animate().alpha(dim).setDuration(3000).setInterpolator(EASE_SOFT).start() else scrim.alpha = dim
        if (mode == ModeStore.GLANCE) {
            for (g in listOf(wifiG, weatherG, homeG, swapG)) if (animate) scene.fade(g, if (on) 0f else 1f, 2000) else g.alpha = if (on) 0f else 1f
            if (on) scene.fade(glanceCapG, 0f, 2000) else if (glanceCap.text.isNotEmpty()) scene.fade(glanceCapG, 1f, 2000)
        }
    }

    private fun refreshLive() {
        net.execute { Live.refresh(context); main.post { if (!stopped && mode == ModeStore.GLANCE) { buildGlanceRow(animate = true); scene.invalidate() } } }
    }

    private fun refreshProd() {
        net.execute {
            val p = Prod.load(context, prefs.prodUrl) ?: return@execute
            main.post {
                if (stopped || p.photos.isEmpty()) return@post
                prod = p
                seq = Sequencer(p, mode, singles = role == Role.HOME_BG)
                Log.i(TAG, "[$label] prod.json refreshed: ${p.photos.size} photos, ${p.spreads.size} spreads")
            }
        }
    }

    private fun burnIn() {
        val r = Random.Default
        val px = if (mode == ModeStore.GLANCE) 12f else 10f
        val tx = (r.nextFloat() * 2 - 1) * px; val ty = (r.nextFloat() * 2 - 1) * px
        val x0 = textRoot.dx; val y0 = textRoot.dy
        android.animation.ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 2400; interpolator = EASE_SOFT
            addUpdateListener { textRoot.dx = x0 + (tx - x0) * (it.animatedValue as Float); textRoot.dy = y0 + (ty - y0) * (it.animatedValue as Float); scene.invalidate() }
            start()
        }
    }

    // ---------------------------------------------------------------------------------------------
    // photo details: OK opens and closes them; Back closes them. Other keys are left to the caller.

    /** True when the key was used here. */
    fun handleKey(e: android.view.KeyEvent): Boolean {
        val d = details ?: return false
        val k = e.keyCode
        val ok = k == android.view.KeyEvent.KEYCODE_DPAD_CENTER || k == android.view.KeyEvent.KEYCODE_ENTER ||
            k == android.view.KeyEvent.KEYCODE_NUMPAD_ENTER || k == android.view.KeyEvent.KEYCODE_BUTTON_A
        // any key while the slide waits after its details closed starts the wait over
        if (e.action == android.view.KeyEvent.ACTION_DOWN && !d.isOpen && afterDetails) {
            main.removeCallbacks(advanceRunnable); main.postDelayed(advanceRunnable, DETAILS_AFTER_MS)
            shownAt = SystemClock.elapsedRealtime() - (holdMs - DETAILS_AFTER_MS).coerceAtLeast(0)
            if (current?.item?.isVideo == true) runClipBar(DETAILS_AFTER_MS + fadeMs())
        }
        if (ok) {
            if (e.action == android.view.KeyEvent.ACTION_DOWN && e.repeatCount == 0) { if (d.isOpen) closeDetails() else openDetails() }
            return true
        }
        if (k == android.view.KeyEvent.KEYCODE_BACK && d.isOpen) {
            if (e.action == android.view.KeyEvent.ACTION_DOWN) closeDetails()
            return true
        }
        return false
    }

    private fun openDetails() {
        val d = details ?: return
        val s = current ?: return
        val p = prod ?: return
        val list = ArrayList<Detail>(); val rects = ArrayList<FloatArray>()
        val full = floatArrayOf(0f, 0f, 3840f, 2160f)
        when {
            s.spread != null -> s.spread.members.forEachIndexed { i, id ->
                val ph = p.byId[id] ?: return@forEachIndexed
                list += Detail(ph.caption.place, ph.caption.whenText, ph.caption.camera, ph.map, ph.caption.more)
                rects += s.spread.cells.getOrNull(i) ?: full
            }
            s.photo != null -> { list += Detail(s.photo.caption.place, s.photo.caption.whenText, s.photo.caption.camera, s.photo.map, s.photo.caption.more); rects += s.photo.rect ?: full }
            s.clip != null -> {
                val c = s.clip.caption
                list += Detail(c.place, c.whenText, listOf(c.camera, c.settings).filter { it.isNotBlank() }.joinToString(" · "), s.clip.map, c.more)
                rects += s.clip.rect ?: full
            }
        }
        if (list.isEmpty()) return
        // hold the photo while its details are up; it moves on DETAILS_AFTER_MS after they close
        afterDetails = false
        if (pausedLeft < 0 && shownAt > 0) {
            main.removeCallbacks(advanceRunnable)
            pausedLeft = (holdMs - (SystemClock.elapsedRealtime() - shownAt)).coerceAtLeast(0)
            clipBarAnim?.pause()
        }
        d.open(list, rects, scene)
        Log.i(TAG, "[$label] details open: ${s.item.id} (${list.size} photo${if (list.size == 1) "" else "s"})")
        main.removeCallbacks(detailsTimeout)
        main.postDelayed(detailsTimeout, DETAILS_OPEN_MS)
    }

    /** The details were closed on this slide and it is waiting DETAILS_AFTER_MS for no input before moving on. */
    private var afterDetails = false

    /** [resume]: carry on with the rest of the photo's time (false when a new slide is coming anyway). */
    private fun closeDetails(instant: Boolean = false, resume: Boolean = !instant) {
        val d = details ?: return
        main.removeCallbacks(detailsTimeout)
        if (!d.isOpen) return
        d.close(scene, instant)
        if (pausedLeft >= 0) {
            main.removeCallbacks(advanceRunnable)
            if (resume) {
                // stay DETAILS_AFTER_MS after the panel closes (any key starts that over), then move on
                val left = DETAILS_AFTER_MS
                shownAt = SystemClock.elapsedRealtime() - (holdMs - left).coerceAtLeast(0); main.postDelayed(advanceRunnable, left)
                if (current?.item?.isVideo == true) runClipBar(left + fadeMs())
                afterDetails = true
            }
            pausedLeft = -1
        }
    }

    private fun loadMap(url: String, cb: (android.graphics.Bitmap?) -> Unit) {
        mapIo.execute {
            val b = try { BitmapFactory.decodeFile(cache.get(url).path) } catch (e: Exception) { Log.w(TAG, "map $url: $e"); null }
            main.post { if (!stopped) cb(b) }
        }
    }

    // ---------------------------------------------------------------------------------------------
    // Now on TV: tell the bridge what is on screen; take next and previous from the photo manager

    private fun report() {
        val s = current ?: return
        val body = JSONObject().put("role", if (role == Role.HOME_BG) "home" else "show").put("id", s.spread?.id ?: s.photo?.id ?: s.clip?.id ?: s.item.id)
            .put("kind", s.kind).put("members", org.json.JSONArray(s.spread?.members ?: listOfNotNull(s.photo?.id ?: s.clip?.id?.substringAfter("video:"))))
        val url = prefs.bridgeUrl.trimEnd('/') + "/now"
        try {
            ctl.execute {
                try {
                    val c = Http.open(url).apply { requestMethod = "POST"; doOutput = true; connectTimeout = 2000; readTimeout = 3000; setRequestProperty("Content-Type", "application/json") }
                    c.outputStream.use { it.write(body.toString().toByteArray()) }
                    c.responseCode; c.disconnect()
                } catch (e: Exception) { }
            }
        } catch (e: java.util.concurrent.RejectedExecutionException) { }
    }

    private fun startCommands() {
        Thread {
            var seq = -1
            while (!stopped) {
                try {
                    val c = Http.open(prefs.bridgeUrl.trimEnd('/') + "/cmd?seq=$seq&wait=25").apply { readTimeout = 40_000 }
                    val o = try { JSONObject(c.inputStream.bufferedReader().readText()) } finally { c.disconnect() }
                    val n = o.optInt("seq", 0)
                    val fresh = seq >= 0 && n > seq && System.currentTimeMillis() / 1000.0 - o.optDouble("at", 0.0) < 20
                    seq = n
                    if (fresh) main.post { if (!stopped) command(o.optString("cmd")) }
                } catch (e: Exception) {
                    if (!stopped) try { Thread.sleep(10_000) } catch (_: InterruptedException) { }
                }
            }
        }.apply { isDaemon = true; name = "tvframe-cmd" }.start()
    }

    private fun command(cmd: String) {
        Log.i(TAG, "[$label] from the photo manager: $cmd")
        if (cmd == "prev" && history.size < 2) return
        // the cards (if up) slide away while the next photo crossfades in
        closeDetails(instant = false, resume = false)
        pausedLeft = -1
        when (cmd) {
            "next" -> advance()
            "prev" -> {
                history.removeAt(history.size - 1)
                val back = history.last()
                goingBack = true
                next = back; nextFile = prefetch(back)
                advance()
            }
        }
    }

    fun stop() {
        if (stopped) return
        stopped = true
        details?.close(scene, instant = true)
        clipBarAnim?.cancel()
        mapIo.shutdownNow(); ctl.shutdown()
        if (role == Role.SHOW) {
            if (active === this) active = null
        }
        main.removeCallbacksAndMessages(null)
        renderer?.release()
        net.shutdownNow()
        io.shutdown()
    }
}

/** A QR code from rows of "0"/"1" on a light rounded card, drawn pixel-snapped with no smoothing so phones read it. */
class QrNode(rows: org.json.JSONArray, var x: Float, var y: Float, var size: Float) : Node() {
    private val n = rows.length()
    private val quiet = 3
    private val bmp: android.graphics.Bitmap = android.graphics.Bitmap.createBitmap(n, n, android.graphics.Bitmap.Config.ARGB_8888).apply {
        for (r in 0 until n) { val row = rows.getString(r); for (c in 0 until n) setPixel(c, r, if (row.getOrNull(c) == '1') 0xFF141414.toInt() else 0) }
    }
    private val card = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFECEDEF.toInt() }
    private val px = android.graphics.Paint().apply { isFilterBitmap = false; isAntiAlias = false }
    override fun draw(c: Canvas, s: Float, a: Float, glow: Glow) {
        val k = kotlin.math.floor(size * s / (n + 2 * quiet)).coerceAtLeast(1f)   // whole device pixels per module
        val side = k * (n + 2 * quiet)
        val x0 = kotlin.math.round(x * s); val y0 = kotlin.math.round(y * s + (size * s - side) / 2)
        card.alpha = (255 * a).toInt(); px.alpha = (255 * a).toInt()
        c.drawRoundRect(x0, y0, x0 + side, y0 + side, 3 * k, 3 * k, card)
        val d = android.graphics.RectF(x0 + quiet * k, y0 + quiet * k, x0 + (quiet + n) * k, y0 + (quiet + n) * k)
        c.drawBitmap(bmp, null, d, px)
    }
}
