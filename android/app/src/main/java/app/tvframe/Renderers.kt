package app.tvframe

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ObjectAnimator
import android.animation.PropertyValuesHolder
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.ImageDecoder
import android.os.Handler
import android.os.SystemClock
import android.util.Log
import android.view.Gravity
import android.view.SurfaceControl
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.animation.LinearInterpolator
import android.view.animation.PathInterpolator
import android.widget.FrameLayout
import android.widget.ImageView
import java.io.File
import java.util.concurrent.ExecutorService

interface Callbacks {
    /** The new slide starts fading in (text can start its own fade now). */
    fun onFadeStart(item: Item) {}
    fun onShown(item: Item, stats: String)
    fun onEnded(item: Item)
    fun onFailed(item: Item, e: Throwable)
}

interface Renderer {
    val root: View
    /** Path name for logs: a, b or c. */
    val path: String
    /** Which file the controller should prefetch for a photo (null = stream, nothing to fetch). */
    fun stillUrl(item: Item): String?
    fun show(item: Item, file: File?, cb: Callbacks)
    fun release()
}

/** Crossfades: a slow, symmetric ease in and out, so a fade has no visible start or end (no linear ramps). */
private val EASE = PathInterpolator(0.45f, 0f, 0.25f, 1f)

/** A clip that shows no picture this long after it was asked for is skipped (the old slide stays up meanwhile). */
const val CLIP_FIRST_FRAME_MS = 10_000L

/** After a fade, the 1080p cover on the UI plane dissolves into the 4K picture underneath over this long. */
const val VEIL_OUT_MS = 1_000L
/** How long before a clip's last play ends the show moves on: up to about 1.4 s for the next slide to get ready, then its
 *  fade starts over the clip's last half second of motion and the clip stops on its last frame under it. */
const val CLIP_LEAD_MS = 2_000L
/** How long before the end the last play is told not to loop: well before its decoder reaches the end. */
const val CLIP_FINAL_MS = 3_000L

private fun fullFrame() = FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)

/** Fit a SurfaceView to a clip's aspect ratio (stills always fill the frame). */
private fun fitSurface(sv: SurfaceView, parent: View, w: Int, h: Int) {
    val pw = parent.width.takeIf { it > 0 } ?: return
    val ph = parent.height.takeIf { it > 0 } ?: return
    val s = minOf(pw / w.toFloat(), ph / h.toFloat())
    val lp = FrameLayout.LayoutParams((w * s).toInt(), (h * s).toInt(), Gravity.CENTER)
    if (lp.width != sv.layoutParams.width || lp.height != sv.layoutParams.height) sv.layoutParams = lp
}

private fun SurfaceView.fill() {
    val lp = layoutParams
    if (lp == null || lp.width != FrameLayout.LayoutParams.MATCH_PARENT) layoutParams = fullFrame()
}

/** Tracks whether a SurfaceView's surface is alive; runs queued work once it is. */
private class SurfaceSlot(ctx: Context, overlay: Boolean) : SurfaceHolder.Callback {
    val view = SurfaceView(ctx).apply { if (overlay) setZOrderMediaOverlay(true) }
    var ready = false; private set
    private val waiting = ArrayList<() -> Unit>()
    var alpha = 1f
        set(v) { field = v; applyAlpha() }
    var onReady: (() -> Unit)? = null
    val still: StillDecoder by lazy { StillDecoder(view.handler ?: Handler(ctx.mainLooper)) }
    var clip: ClipPlayer? = null

    init { view.holder.addCallback(this) }

    fun whenReady(f: () -> Unit) { if (ready) f() else waiting += f }

    /** Alpha on the video-plane layer itself (public SurfaceControl API, API 29+). */
    fun applyAlpha() {
        if (!ready) return
        val sc = view.surfaceControl ?: return
        if (!sc.isValid) return
        SurfaceControl.Transaction().setAlpha(sc, alpha).apply()
    }

    override fun surfaceCreated(holder: SurfaceHolder) {
        ready = true
        applyAlpha()
        onReady?.invoke()
        val w = ArrayList(waiting); waiting.clear(); w.forEach { it() }
    }
    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) { applyAlpha() }
    override fun surfaceDestroyed(holder: SurfaceHolder) { ready = false; clip?.stop(); clip = null; still.release() }

    fun stopProducers() { clip?.stop(); clip = null; still.release() }
}

// ------------------------------------------------------------------------------------------------
// (a) UI plane: decode a Bitmap sized to the window and draw it in an ImageView.
// On a TV whose UI plane is 1920x1080 this can never show more than 1080p, whatever the photo.
// ------------------------------------------------------------------------------------------------
class BitmapRenderer(
    private val ctx: Context,
    private val io: ExecutorService,
    private val main: Handler,
    private val kenBurns: Boolean,
    private val holdMs: () -> Long,
    private val transitionMs: Long,
) : Renderer {
    override val path = "a"
    private val frame = FrameLayout(ctx)
    override val root: View get() = frame
    private val video = SurfaceSlot(ctx, overlay = false)
    private val imgs = Array(2) { ImageView(ctx).apply { scaleType = ImageView.ScaleType.CENTER_CROP; alpha = 0f } }
    private var front: ImageView? = null
    private var released = false

    init {
        frame.addView(video.view, fullFrame())
        imgs.forEach { frame.addView(it, fullFrame()) }
    }

    override fun stillUrl(item: Item) = item.url

    override fun show(item: Item, file: File?, cb: Callbacks) {
        if (item.isVideo) return showVideo(item, cb)
        val w = frame.width.takeIf { it > 0 } ?: 1920
        val h = frame.height.takeIf { it > 0 } ?: 1080
        val zoom = if (kenBurns) 1.08f else 1f
        io.execute {
            try {
                val t0 = SystemClock.elapsedRealtime()
                val src = ImageDecoder.createSource(file!!)
                var srcSize = ""
                val bmp = ImageDecoder.decodeBitmap(src) { d, info, _ ->
                    srcSize = "${info.size.width}x${info.size.height}"
                    val s = minOf(1f, maxOf(w * zoom / info.size.width, h * zoom / info.size.height))
                    d.setTargetSize((info.size.width * s).toInt().coerceAtLeast(1), (info.size.height * s).toInt().coerceAtLeast(1))
                    d.allocator = ImageDecoder.ALLOCATOR_HARDWARE
                }
                val dt = SystemClock.elapsedRealtime() - t0
                main.post {
                    if (released) { bmp.recycle(); return@post }
                    val stats = "decode=${dt}ms src=$srcSize bitmap=${bmp.width}x${bmp.height} view=${w}x$h"
                    reveal(item, bmp, stats, cb)
                }
            } catch (e: Throwable) {
                main.post { cb.onFailed(item, e) }
            }
        }
    }

    private fun reveal(item: Item, bmp: android.graphics.Bitmap, stats: String, cb: Callbacks) {
        val back = if (front === imgs[0]) imgs[1] else imgs[0]
        back.animate().cancel()
        back.setImageBitmap(bmp)
        back.alpha = 0f
        back.scaleX = 1f; back.scaleY = 1f
        back.bringToFront()
        if (kenBurns) {
            val cover = item.layout == "cover"
            back.pivotX = (if (cover) item.focalX else 0.5f) * frame.width
            back.pivotY = (if (cover) item.focalY else 0.5f) * frame.height
            val zIn = (item.id.hashCode() and 1) == 0
            val (s0, s1) = if (cover) (if (zIn) 1f to 1.08f else 1.08f to 1f) else (if (zIn) 1f to 1.035f else 1.035f to 1f)
            back.scaleX = s0; back.scaleY = s0
            ObjectAnimator.ofPropertyValuesHolder(back,
                PropertyValuesHolder.ofFloat(View.SCALE_X, s0, s1),
                PropertyValuesHolder.ofFloat(View.SCALE_Y, s0, s1)).apply {
                duration = holdMs() + 2 * transitionMs
                interpolator = LinearInterpolator()
                start()
            }
        }
        val old = front
        cb.onFadeStart(item)
        back.animate().alpha(1f).setDuration(transitionMs).setInterpolator(EASE).withEndAction {
            if (old != null && old !== back) { old.alpha = 0f; old.animate().cancel(); old.setImageDrawable(null) }
            video.stopProducers()
            front = back
            cb.onShown(item, stats)
        }.start()
    }

    private fun showVideo(item: Item, cb: Callbacks) {
        video.whenReady {
            val clip = ClipPlayer()
            video.clip?.stop()
            video.clip = clip
            val t0 = SystemClock.elapsedRealtime()
            clip.play(video.view.holder.surface, StillCodec.clipUrl(item),
                onFirstFrame = {
                    val stats = "firstFrame=${SystemClock.elapsedRealtime() - t0}ms"
                    val old = front
                    if (old == null) cb.onShown(item, stats)
                    else old.animate().alpha(0f).setDuration(transitionMs).setInterpolator(EASE).withEndAction {
                        old.setImageDrawable(null); front = null; cb.onShown(item, stats)
                    }.start()
                },
                onEnd = { Log.i(TAG, "clip end ${item.id} ${clip.metrics()}"); cb.onEnded(item) },
                onError = { cb.onFailed(item, it) },
                onSize = { w, h -> fitSurface(video.view, frame, w, h) })
        }
    }

    override fun release() {
        released = true
        video.stopProducers()
        imgs.forEach { it.animate().cancel(); it.setImageDrawable(null) }
    }
}

// ------------------------------------------------------------------------------------------------
// (b) Video plane: every photo is a one-frame 10-bit HEVC/AV1 stream at 3840x2160, decoded by
// MediaCodec straight onto a SurfaceView, two of them stacked.
// Fades: the Streamer's display hardware ignores the alpha of its video layers (SurfaceFlinger
// sends it, both layers stay DEVICE composited, and the TV still cuts), so a fade is drawn on the
// UI plane instead: the next picture (its 1080p frame JPEG, or a clip's first frame) fades in over
// the old one, the video layers swap underneath while it covers them, then it dissolves away to
// uncover the 4K picture. Without that JPEG it falls back to the layer-alpha crossfade.
// ------------------------------------------------------------------------------------------------
class PlaneRenderer(
    private val ctx: Context,
    private val decodeThread: ExecutorService,
    private val main: Handler,
    var transitionMs: Long,
    private val codecPref: String,
    /** Blocking: the slide's frame JPEG or clip poster, from the media cache (null when it has none or it fails). */
    private val veilFile: (Item) -> File? = { null },
) : Renderer {
    override val path = "b"
    private val frame = FrameLayout(ctx)
    override val root: View get() = frame
    private val lower = SurfaceSlot(ctx, overlay = false)
    private val upper = SurfaceSlot(ctx, overlay = true).apply { alpha = 0f }
    private var upperShown = false
    private var released = false
    private var fade: ValueAnimator? = null
    /** The fade on the UI plane, above both video layers (and under the frame's text). */
    private val veil = ImageView(ctx).apply { scaleType = ImageView.ScaleType.FIT_XY; alpha = 0f; setLayerType(View.LAYER_TYPE_HARDWARE, null) }

    init {
        frame.addView(lower.view, fullFrame())
        frame.addView(upper.view, fullFrame())
        frame.addView(veil, fullFrame())
    }

    /** The slide as a screen-sized bitmap for the veil; call on a worker thread. */
    private fun loadVeil(item: Item, w: Int, h: Int): android.graphics.Bitmap? = try {
        veilFile(item)?.let { f ->
            ImageDecoder.decodeBitmap(ImageDecoder.createSource(f)) { d, _, _ -> d.setTargetSize(w, h); d.allocator = ImageDecoder.ALLOCATOR_HARDWARE }
        }
    } catch (e: Throwable) { Log.w(TAG, "veil ${item.id}: $e"); null }

    override fun stillUrl(item: Item) = StillCodec.pick(codecPref, item)?.first

    /** A slide is on its way (decoding, waiting for a clip's first frame, or crossfading in). */
    private var busy = false
    /** The latest slide asked for while another was on its way; it goes as soon as that one has landed. */
    private var queued: (() -> Unit)? = null
    /** First-frame watchdog for clips: a clip that never shows a picture is given up, so the show moves on. */
    private var clipTimeout: Runnable? = null

    /** Length of the clip on screen, from the player (0 when unknown or a still is up). */
    fun clipDurationMs(): Int = (if (upperShown) upper else lower).clip?.durationMs ?: 0
    /** How far into the clip on screen playback is (0 when none). */
    fun clipPositionMs(): Int = (if (upperShown) upper else lower).clip?.positionMs ?: 0

    /** The clip on screen makes the play under way its last and holds its last frame; [done] runs [CLIP_LEAD_MS]
     *  before that end. Right away when no clip is up. */
    fun finishClip(done: () -> Unit) {
        val c = (if (upperShown) upper else lower).clip ?: return done()
        c.finishPlay(main, CLIP_LEAD_MS.toInt(), done)
    }

    override fun show(item: Item, file: File?, cb: Callbacks) {
        if (released) return
        // never start a second slide while one is still decoding or fading in: it would land on the layer that is
        // fading in and cut. Keep only the newest request and run it once the current one has landed.
        if (busy) { queued = { show(item, file, cb) }; return }
        busy = true
        val target = if (upperShown) lower else upper     // the hidden one
        val guarded = object : Callbacks {
            override fun onFadeStart(item: Item) = cb.onFadeStart(item)
            override fun onShown(item: Item, stats: String) = cb.onShown(item, stats)
            override fun onEnded(item: Item) = cb.onEnded(item)
            override fun onFailed(item: Item, e: Throwable) {
                // failed before it reached the screen: the old slide simply stays up, the controller moves on
                if (busy) landed()
                cb.onFailed(item, e)
            }
        }
        target.whenReady {
            if (released) return@whenReady
            if (item.isVideo) showVideo(item, file, target, guarded) else showStill(item, file, target, guarded)
        }
    }

    private fun landed() {
        busy = false
        val q = queued; queued = null
        q?.let { main.post(it) }
    }

    private fun showStill(item: Item, file: File?, target: SurfaceSlot, cb: Callbacks) {
        val choice = StillCodec.pick(codecPref, item)?.second
        if (file == null || choice == null) { cb.onFailed(item, IllegalStateException("no 4K still stream or decoder")); return }
        target.clip?.stop(); target.clip = null
        target.view.fill()
        val surface = target.view.holder.surface
        val t0 = SystemClock.elapsedRealtime()
        val vw = frame.width.takeIf { it > 0 } ?: 1920; val vh = frame.height.takeIf { it > 0 } ?: 1080
        decodeThread.execute {
            try {
                val bmp = loadVeil(item, vw, vh)
                var st: StillDecoder.Stats? = null
                st = target.still.decode(file, surface, choice) {
                    val s = st
                    val stats = "codec=${choice.label} " + (if (s != null) "extract=${s.extractMs}ms start=${s.startMs}ms frame=${s.frameMs}ms " else "") +
                        "toScreen=${SystemClock.elapsedRealtime() - t0}ms ${if (bmp != null) "veil" else "layers"}"
                    if (released) return@decode
                    cb.onFadeStart(item)
                    if (bmp != null) veilFade(target, bmp, {}) { cb.onShown(item, stats) }
                    else crossfade(target) { cb.onShown(item, stats) }
                }
            } catch (e: Throwable) {
                main.post { cb.onFailed(item, e) }
            }
        }
    }

    private fun showVideo(item: Item, file: File?, target: SurfaceSlot, cb: Callbacks) {
        val vw = frame.width.takeIf { it > 0 } ?: 1920; val vh = frame.height.takeIf { it > 0 } ?: 1080
        decodeThread.execute {
            target.still.release()
            val bmp = loadVeil(item, vw, vh)
            main.post { if (!released) startClip(item, file, target, cb, bmp) }
        }
    }

    private fun startClip(item: Item, file: File?, target: SurfaceSlot, cb: Callbacks, poster: android.graphics.Bitmap?) {
        // clips loop (seamless loops from tools/clip_loops.py); FrameView decides how long one stays up (clipShowMs)
        val clip = ClipPlayer(loop = true)
        target.clip?.stop()
        target.clip = clip
        val t0 = SystemClock.elapsedRealtime()
        var started = false
        // the clip only fades in once its first frame is on the (hidden) layer, so there is never a black or frozen cut
        val timeout = Runnable {
            if (started || released || target.clip !== clip) return@Runnable
            Log.w(TAG, "clip ${item.id}: no first frame after ${CLIP_FIRST_FRAME_MS}ms, skipping it")
            clip.stop(); target.clip = null
            cb.onFailed(item, IllegalStateException("clip first frame timeout"))
        }
        clipTimeout?.let { main.removeCallbacks(it) }
        clipTimeout = timeout
        main.postDelayed(timeout, CLIP_FIRST_FRAME_MS)
        // a local copy (prefetched into the media cache) starts at once and cannot stall on the network
        val src = file?.takeIf { it.length() > 0 }?.path ?: StillCodec.clipUrl(item)
        clip.play(target.view.holder.surface, src,
            onFirstFrame = {
                if (started || released || target.clip !== clip) return@play
                started = true
                main.removeCallbacks(timeout)
                cb.onFadeStart(item)
                val stats = "firstFrame=${SystemClock.elapsedRealtime() - t0}ms ${if (src.startsWith("/")) "local" else "stream"} ${if (poster != null) "veil" else "layers"}"
                if (poster != null) {
                    // hold on the first frame while its poster fades in and dissolves into it, then play (moving under a
                    // still poster would ghost)
                    clip.pause()
                    veilFade(target, poster, {}) { clip.resume(); cb.onShown(item, stats) }
                } else crossfade(target) { cb.onShown(item, stats) }
            },
            onEnd = { Log.i(TAG, "clip end ${item.id} ${clip.metrics()}"); if (target.clip === clip) cb.onEnded(item) },
            onError = {
                if (target.clip !== clip) return@play
                main.removeCallbacks(timeout)
                if (!started) { clip.stop(); target.clip = null }
                cb.onFailed(item, it)
            },
            onSize = { w, h -> fitSurface(target.view, frame, w, h) })
    }

    /**
     * Fade to [target] on the UI plane: [bmp] (the new picture) eases in over whatever is up, the layers swap under
     * it ([swapped] runs then), and it dissolves into the 4K picture. A slow symmetric ease, as for every crossfade.
     */
    private fun veilFade(target: SurfaceSlot, bmp: android.graphics.Bitmap, swapped: () -> Unit, done: () -> Unit) {
        if (released) return
        fade?.cancel()
        veil.animate().cancel()
        veil.setImageBitmap(bmp)
        veil.alpha = 0f
        veil.animate().alpha(1f).setDuration(transitionMs).setInterpolator(EASE).withEndAction {
            if (released) return@withEndAction
            val toUpper = target === upper
            upper.alpha = if (toUpper) 1f else 0f
            upperShown = toUpper
            swapped()
            // a few frames for SurfaceFlinger to put the new layer up before it is uncovered
            main.postDelayed({
                if (released) return@postDelayed
                val hidden = if (toUpper) lower else upper
                hidden.clip?.stop(); hidden.clip = null
                decodeThread.execute { hidden.still.release() }
                veil.animate().alpha(0f).setDuration(VEIL_OUT_MS).setInterpolator(EASE).withEndAction {
                    veil.setImageDrawable(null)
                    done()
                    landed()
                }.start()
            }, 150)
        }.start()
    }

    /** Fade the upper layer in (lower -> upper) or out (upper -> lower). */
    private fun crossfade(target: SurfaceSlot, done: () -> Unit) {
        if (released) return
        fade?.cancel()
        val toUpper = target === upper
        val from = upper.alpha
        val to = if (toUpper) 1f else 0f
        fade = ValueAnimator.ofFloat(from, to).apply {
            duration = transitionMs
            interpolator = EASE
            addUpdateListener { upper.alpha = it.animatedValue as Float }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    upperShown = toUpper
                    val hidden = if (toUpper) lower else upper
                    hidden.clip?.stop(); hidden.clip = null
                    decodeThread.execute { hidden.still.release() }
                    done()
                    landed()
                }
            })
            start()
        }
    }

    override fun release() {
        released = true
        queued = null
        clipTimeout?.let { main.removeCallbacks(it) }
        fade?.cancel()
        veil.animate().cancel(); veil.setImageDrawable(null)
        lower.clip?.stop(); upper.clip?.stop()
        decodeThread.execute { lower.still.release(); upper.still.release() }
    }
}

// ------------------------------------------------------------------------------------------------
// (c) Zero client logic: the server renders the whole slideshow (Ken Burns, crossfades, clips)
// into one 4K HEVC file and the TV loops it.
// ------------------------------------------------------------------------------------------------
class LoopRenderer(private val ctx: Context, private val main: Handler, private val loopUrl: String) : Renderer {
    override val path = "c"
    private val slot = SurfaceSlot(ctx, overlay = false)
    override val root: View get() = slot.view
    private val clip = ClipPlayer(loop = true)
    private var released = false
    private val tick = object : Runnable {
        override fun run() { Log.i(TAG, "loop ${clip.metrics()}"); main.postDelayed(this, 30_000) }
    }

    override fun stillUrl(item: Item): String? = null

    fun start(cb: Callbacks, item: Item) {
        slot.whenReady {
            if (released) return@whenReady
            val t0 = SystemClock.elapsedRealtime()
            clip.play(slot.view.holder.surface, loopUrl,
                onFirstFrame = { cb.onShown(item, "firstFrame=${SystemClock.elapsedRealtime() - t0}ms"); main.postDelayed(tick, 30_000) },
                onEnd = {}, onError = { cb.onFailed(item, it) })
        }
    }

    override fun show(item: Item, file: File?, cb: Callbacks) = start(cb, item)

    override fun release() {
        released = true
        main.removeCallbacks(tick)
        clip.stop()
    }
}
