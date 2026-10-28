package app.tvframe

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.Window
import android.widget.FrameLayout
import android.widget.TextClock
import android.widget.TextView
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.Future

/**
 * The slideshow: loads the playlist, picks items, prefetches the next still, and hands each
 * one to the renderer for the chosen path (a, b or c). Used by the screensaver, the preview
 * activity and (dimmed, path a) the home screen background.
 */
class SlideshowView(
    ctx: Context,
    private val forceMode: String? = null,
    private val dim: Float = 0f,
    private val overlays: Boolean = true,
    private val label: String = "dream",
    private val pinItem: String? = null,   // testing: show only this item id
) : FrameLayout(ctx), Callbacks {

    private val prefs = Prefs(ctx)
    private val main = Handler(Looper.getMainLooper())
    private val net = Executors.newSingleThreadExecutor { Thread(it, "tvframe-net") }
    private val io = Executors.newSingleThreadExecutor { Thread(it, "tvframe-decode") }
    private val cache = MediaCache(ctx)
    private var renderer: Renderer? = null
    private var playlist: Playlist? = null
    private var sampler: Sampler? = null
    private var nextItem: Item? = null
    private var nextFile: Future<File?>? = null
    private var stopped = false
    private var failures = 0
    private var shownCount = 0
    private val t0 = SystemClock.elapsedRealtime()
    var mode = "?"; private set

    private val caption = TextView(ctx).apply {
        setTextColor(0xCCF3F1EC.toInt())
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        typeface = Typeface.create("sans-serif", Typeface.NORMAL)
        letterSpacing = 0.02f
        setShadowLayer(8f, 0f, 1f, 0x99000000.toInt())
        alpha = 0f
        isSingleLine = true
    }
    private val clock = TextClock(ctx).apply {
        format12Hour = "h:mm"
        format24Hour = "H:mm"
        setTextColor(0xE6F3F1EC.toInt())
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 26f)
        typeface = Typeface.create("sans-serif-light", Typeface.NORMAL)
        setShadowLayer(10f, 0f, 1f, 0x99000000.toInt())
        visibility = View.GONE
    }
    private val scrim = View(ctx).apply { setBackgroundColor(Color.BLACK); alpha = dim }

    private val advanceRunnable = Runnable { advance() }
    private val refreshRunnable = object : Runnable {
        override fun run() { refreshPlaylist(); main.postDelayed(this, 15 * 60_000L) }
    }

    init {
        setBackgroundColor(Color.BLACK)
        addView(scrim, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        if (overlays) {
            // 48dp / 27dp keeps text inside the 5% TV-safe area
            addView(caption, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.END)
                .apply { setMargins(0, 0, dp(48), dp(30)) })
            addView(clock, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.START)
                .apply { setMargins(dp(48), 0, 0, dp(24)) })
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    fun start() {
        net.execute {
            val p = Playlist.load(context, prefs.playlistUrl)
            main.post { if (!stopped) begin(p) }
        }
    }

    private fun begin(p: Playlist?) {
        if (p == null || (p.items.isEmpty() && p.loop == null)) {
            Log.w(TAG, "no playlist yet, retry in 30 s")
            main.postDelayed({ if (!stopped) start() }, 30_000)
            return
        }
        playlist = p
        var m = forceMode ?: prefs.mode.takeIf { it != "auto" } ?: p.mode
        if (m == "c" && p.loop == null) m = "b"
        if (m == "b" && p.items.none { !it.isVideo && StillCodec.pick(prefs.codec, it) != null }) m = "a"
        mode = m
        val r: Renderer = when (m) {
            "c" -> LoopRenderer(context, main, if (!StillCodec.hevc10 && p.loop!!.url8 != null) p.loop.url8!! else p.loop!!.url)
            "b" -> PlaneRenderer(context, io, main, p.transitionMs, prefs.codec)
            else -> BitmapRenderer(context, io, main, p.kenBurns, { (p.photoSeconds * 1000).toLong() }, p.transitionMs)
        }
        renderer = r
        addView(r.root, 0, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        clock.visibility = if (overlays && p.clock) View.VISIBLE else View.GONE
        Log.i(TAG, "[$label] start path=$m items=${p.items.size} loop=${p.loop != null} " +
            "codecs=${StillCodec.all().values.joinToString { it?.label ?: "none" }} startup=${SystemClock.elapsedRealtime() - t0}ms")
        if (r is LoopRenderer) {
            r.start(this, Item("loop", "video", p.loop!!.url, null, null, 1.0, null, .5f, .5f, "cover", p.loop.duration, null))
            return
        }
        val pinned = pinItem?.let { id -> p.items.filter { it.id == id } }?.takeIf { it.isNotEmpty() }
        sampler = Sampler(pinned ?: p.items)
        if (pinned == null) main.postDelayed(refreshRunnable, 15 * 60_000L)
        advance()
    }

    private fun refreshPlaylist() {
        net.execute {
            val p = Playlist.load(context, prefs.playlistUrl) ?: return@execute
            main.post {
                if (stopped || p.items.isEmpty() || renderer is LoopRenderer) return@post
                playlist = p
                sampler = Sampler(p.items)
                Log.i(TAG, "[$label] playlist refreshed: ${p.items.size} items")
            }
        }
    }

    private fun prefetch(item: Item): Future<File?> {
        val url = if (item.isVideo) null else renderer?.stillUrl(item)
        return net.submit<File?> { url?.let { cache.get(it) } }
    }

    private fun advance() {
        main.removeCallbacks(advanceRunnable)
        val s = sampler ?: return
        val item = nextItem ?: s.next()
        val fut = nextFile ?: prefetch(item)
        nextItem = null; nextFile = null
        val tReq = SystemClock.elapsedRealtime()
        net.execute {
            val file = try { fut.get() } catch (e: Exception) {
                main.post { onFailed(item, e) }; return@execute
            }
            val waited = SystemClock.elapsedRealtime() - tReq
            main.post {
                if (stopped) return@post
                if (waited > 50) Log.i(TAG, "[$label] waited ${waited}ms for ${item.id}")
                renderer?.show(item, file, this)
            }
        }
    }

    override fun onShown(item: Item, stats: String) {
        if (stopped) return
        failures = 0
        shownCount++
        Log.i(TAG, "[$label] shown #$shownCount path=$mode ${item.type} ${item.id} $stats")
        val p = playlist ?: return
        if (overlays && p.captions) {
            caption.animate().cancel()
            caption.alpha = 0f
            caption.text = item.caption ?: ""
            if (item.caption != null) caption.animate().alpha(1f).setStartDelay(600).setDuration(900).start()
        }
        if (renderer is LoopRenderer) return
        val s = sampler ?: return
        // pick and fetch the next one now, while this one is on screen
        nextItem = s.next().also { nextFile = prefetch(it) }
        val holdMs = if (item.isVideo) ((item.duration + 3) * 1000).toLong() else (p.photoSeconds * 1000).toLong()
        main.postDelayed(advanceRunnable, holdMs)
    }

    override fun onEnded(item: Item) {
        if (stopped) return
        advance()
    }

    override fun onFailed(item: Item, e: Throwable) {
        if (stopped) return
        failures++
        Log.w(TAG, "[$label] failed ${item.id}: $e")
        main.removeCallbacks(advanceRunnable)
        main.postDelayed(advanceRunnable, if (failures > 5) 10_000L else 300L)
    }

    fun stop() {
        stopped = true
        main.removeCallbacksAndMessages(null)
        renderer?.release()
        net.shutdownNow()
        io.shutdown()
    }

    companion object {
        /** Ask for the display's highest-resolution mode at the current refresh rate. */
        fun preferHighestMode(window: Window?, display: android.view.Display?) {
            if (window == null || display == null) return
            val cur = display.mode
            val best = display.supportedModes
                .filter { kotlin.math.abs(it.refreshRate - cur.refreshRate) < 0.5f }
                .maxByOrNull { it.physicalWidth * it.physicalHeight } ?: return
            Log.i(TAG, "display mode now ${cur.physicalWidth}x${cur.physicalHeight}@${cur.refreshRate}, " +
                "best ${best.physicalWidth}x${best.physicalHeight}@${best.refreshRate}, modes=${display.supportedModes.size}")
            if (best.modeId != cur.modeId) {
                window.attributes = window.attributes.apply { preferredDisplayModeId = best.modeId }
            }
        }
    }
}
