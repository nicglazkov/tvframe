package app.tvframe

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Outline
import android.graphics.Paint
import android.graphics.Shader
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.view.View
import android.view.ViewOutlineProvider
import android.view.animation.PathInterpolator
import android.widget.FrameLayout
import android.widget.ImageView

/** Material's emphasized curves: things arriving decelerate into place, things leaving accelerate away. */
val EASE_ARRIVE = PathInterpolator(0.05f, 0.7f, 0.1f, 1f)
val EASE_LEAVE = PathInterpolator(0.3f, 0f, 0.8f, 0.15f)

/** One photo's details: where, when, what took it, how (focal length, aperture, shutter, ISO), and its map if it has GPS. */
data class Detail(val place: String, val whenText: String, val camera: String, val map: String?, val more: List<String> = emptyList())

/**
 * Photo details, opened with OK while the frame is up. Every photo on screen gets a card placed by where that photo
 * sits, and the card comes out of its photo:
 *  - a photo that fills the screen: one wide card rises from the bottom edge, map on the left;
 *  - a tall photo on its blurred backdrop: the card slides out of the photo's left edge into the side band, map on top;
 *  - two or three across: a card drops out of the bottom of each photo, left to right; a column of two stacked photos
 *    gets one card listing both, top first.
 * The frame's own text (clock, tiles, caption) steps back while the cards are up. Cards are drawn once into hardware
 * layers and only translate and fade, so the motion holds 60 fps on the Streamer. Sizes are 4K stage pixels.
 */
class DetailsOverlay(ctx: Context, private val loadMap: (String, (Bitmap?) -> Unit) -> Unit) : FrameLayout(ctx) {
    private enum class Kind { WIDE, BAND, COLUMN }

    private class Spec(val x: Float, val y: Float, val w: Float, val kind: Kind, val entries: List<Detail>,
                       val fromDx: Float, val fromDy: Float, val bottomAligned: Boolean = false)

    var isOpen = false; private set
    private val s get() = (width.takeIf { it > 0 } ?: 1920) / 3840f
    private val shade = Shade(ctx)
    private val hint = HintView(ctx)
    private val cards = ArrayList<Pair<View, Spec>>()
    private var gen = 0

    init {
        clipChildren = false
        addView(shade, LayoutParams(-1, -1))
        addView(hint, LayoutParams(-1, -1))
        shade.alpha = 0f; hint.alpha = 0f
        visibility = GONE
    }

    /**
     * Open for the slide on screen. [rects] are where each photo sits (4K x, y, w, h), in the same order as
     * [details]; one full-screen rect means a single photo that fills the screen.
     */
    fun open(details: List<Detail>, rects: List<FloatArray>, behind: View) {
        gen++
        clearCards()
        isOpen = true
        visibility = VISIBLE
        val specs = layout(details, rects)
        val band = specs.firstOrNull()?.kind == Kind.BAND
        shade.set(when { specs.firstOrNull()?.kind == Kind.WIDE -> Shade.Mode.BOTTOM_TALL; band -> Shade.Mode.LEFT_BAND; else -> Shade.Mode.BOTTOM_LOW },
            rects.firstOrNull()?.get(0) ?: 0f)
        for ((i, sp) in specs.withIndex()) {
            val c = Card(context, sp, s, loadMap)
            val top = when {
                sp.bottomAligned -> sp.y * s - c.hPx
                band -> rects[0].let { (it[1] + it[3] / 2) * s - c.hPx / 2f }
                else -> sp.y * s
            }
            addView(c, LayoutParams(Math.round(sp.w * s), Math.round(c.hPx)).apply { leftMargin = Math.round(sp.x * s); topMargin = Math.round(top) })
            c.setLayerType(LAYER_TYPE_HARDWARE, null)
            c.alpha = 0f; c.translationX = sp.fromDx * s; c.translationY = sp.fromDy * s
            cards += c to sp
            c.animate().alpha(1f).translationX(0f).translationY(0f).setStartDelay(90L + i * 70L).setDuration(460)
                .setInterpolator(EASE_ARRIVE).withLayer().start()
        }
        behind.animate().cancel()
        behind.animate().alpha(0f).translationY(14f * s).setStartDelay(0).setDuration(220).setInterpolator(EASE_LEAVE).withLayer().start()
        shade.animate().cancel(); hint.animate().cancel()
        shade.animate().alpha(1f).setStartDelay(0).setDuration(380).setInterpolator(EASE_ARRIVE).withLayer().start()
        hint.animate().alpha(1f).setStartDelay(320).setDuration(320).setInterpolator(EASE_ARRIVE).start()
    }

    fun close(behind: View, instant: Boolean = false) {
        if (!isOpen) return
        isOpen = false
        val g = ++gen
        if (instant) {
            clearCards(); shade.alpha = 0f; hint.alpha = 0f; visibility = GONE
            behind.animate().cancel(); behind.alpha = 1f; behind.translationY = 0f
            return
        }
        val n = cards.size
        for ((i, p) in cards.withIndex()) {
            val (c, sp) = p
            c.animate().cancel()
            c.animate().alpha(0f).translationX(sp.fromDx * .6f * s).translationY(sp.fromDy * .6f * s)
                .setStartDelay((n - 1 - i) * 40L).setDuration(210).setInterpolator(EASE_LEAVE).withLayer().start()
        }
        hint.animate().cancel(); shade.animate().cancel()
        hint.animate().alpha(0f).setStartDelay(0).setDuration(160).setInterpolator(EASE_LEAVE).start()
        shade.animate().alpha(0f).setStartDelay(90).setDuration(300).setInterpolator(EASE_LEAVE).withLayer().start()
        behind.animate().cancel()
        behind.animate().alpha(1f).translationY(0f).setStartDelay(170).setDuration(380).setInterpolator(EASE_ARRIVE).withLayer()
            .withEndAction { if (gen == g) { clearCards(); visibility = GONE } }.start()
    }

    private fun clearCards() {
        for ((c, _) in cards) { c.animate().cancel(); removeView(c) }
        cards.clear()
    }

    /** Where each card goes and where it comes from, by how the slide is laid out. */
    private fun layout(details: List<Detail>, rects: List<FloatArray>): List<Spec> {
        val r0 = rects.firstOrNull() ?: floatArrayOf(0f, 0f, 3840f, 2160f)
        if (details.size == 1) {
            val fills = r0[2] >= 3800f && r0[3] >= 2100f
            val band = r0[0] - 2 * 130f
            return if (!fills && band >= 640f) {
                listOf(Spec(130f, 0f, band, Kind.BAND, details, fromDx = 56f, fromDy = 0f))
            } else {
                listOf(Spec(200f, 2160f - 150f, 2600f, Kind.WIDE, details, fromDx = 0f, fromDy = 72f, bottomAligned = true))
            }
        }
        // spreads: one card per column, under it; stacked photos in a column share one card, top first
        val cols = LinkedHashMap<Float, MutableList<Pair<FloatArray, Detail>>>()
        for ((i, d) in details.withIndex()) {
            val r = rects.getOrNull(i) ?: continue
            cols.getOrPut(r[0]) { ArrayList() } += r to d
        }
        // cards sit on the bottom edge, just under the photos (whose band ends at 1615), and grow upward when the
        // text needs it; they drop out of their photo as before
        return cols.toSortedMap().map { (x, list) ->
            val sorted = list.sortedBy { it.first[1] }
            Spec(x, 2160f - 64f, sorted[0].first[2], Kind.COLUMN, sorted.map { it.second }, fromDx = 0f, fromDy = -44f, bottomAligned = true)
        }
    }

    /** One card: text drawn once, maps as rounded images that fade in when they arrive. */
    private class Card(ctx: Context, sp: Spec, val s: Float, loadMap: (String, (Bitmap?) -> Unit) -> Unit) : FrameLayout(ctx) {
        private val glow = Glow(.6f, 40f)
        private val blocks = ArrayList<Pair<Float, Float>>()   // text block x, y in px
        private val layouts = ArrayList<List<Pair<StaticLayout, Float>>>()  // per entry: layouts with their y offset
        private var h = 0f
        val hPx get() = h * s

        init {
            setWillNotDraw(false)
            clipChildren = false
            val stacked = sp.entries.size > 1
            val (map, place, whenSz, cam) = when {
                sp.kind == Kind.WIDE -> listOf(820f, 96f, 54f, 46f)
                sp.kind == Kind.BAND -> listOf(minOf(sp.w, 1000f), 76f, 48f, 42f)
                stacked -> listOf(260f, 52f, 38f, 34f)
                else -> listOf(400f, 62f, 42f, 38f)
            }
            val gap = if (sp.kind == Kind.WIDE) 56f else 36f
            var y = 0f
            for ((i, d) in sp.entries.withIndex()) {
                if (i > 0) y += 32f
                val hasMap = d.map != null
                val vertical = sp.kind == Kind.BAND
                val tx = if (hasMap && !vertical) map + gap else 0f
                val tw = (if (vertical) sp.w else sp.w - tx).coerceAtLeast(300f)
                val head = d.place.ifBlank { d.whenText }.ifBlank { "No details for this photo" }
                val lines = ArrayList<Pair<String, TStyle>>()
                lines += head to TStyle(Fonts.Face.SANS, place, INK, lineHeight = 1.12f)
                if (d.place.isNotBlank() && d.whenText.isNotBlank()) lines += d.whenText to TStyle(Fonts.Face.TEXT_MEDIUM, whenSz, withAlpha(INK, .86f), lineHeight = 1.3f)
                if (d.camera.isNotBlank()) lines += d.camera to TStyle(Fonts.Face.TEXT, cam, withAlpha(INK, .68f), lineHeight = 1.3f)
                // the settings it was taken at, a step quieter than the camera
                for (m in d.more) lines += m to TStyle(Fonts.Face.TEXT, cam * .94f, withAlpha(INK, .56f), lineHeight = 1.3f, tracking = .01f)
                var ty = 0f
                val ls = ArrayList<Pair<StaticLayout, Float>>()
                for ((j, l) in lines.withIndex()) {
                    val p = paintFor(l.second, s, glow)
                    val sl = StaticLayout.Builder.obtain(l.first, 0, l.first.length, p, Math.round(tw * s))
                        .setMaxLines(if (j == 0) 3 else 2).setAlignment(Layout.Alignment.ALIGN_NORMAL)
                        .setLineSpacing(0f, l.second.lineHeight.takeIf { it > 0 } ?: 1f).setIncludePad(false).build()
                    if (j > 0) ty += (if (j == 1) 18f else 8f) * s
                    ls += sl to ty
                    ty += sl.height
                }
                val textH = ty / s
                val mapTop: Float; val textTop: Float; val entryH: Float
                if (hasMap && vertical) {
                    mapTop = y; textTop = y + map + 40f; entryH = map + 40f + textH
                } else if (hasMap) {
                    entryH = maxOf(map, textH); mapTop = y + (entryH - map) / 2; textTop = y + (entryH - textH) / 2
                } else {
                    mapTop = y; textTop = y; entryH = textH
                }
                if (hasMap) addMap(d.map!!, 0f, mapTop, map, loadMap)
                blocks += tx * s to textTop * s
                layouts += ls
                y += entryH
            }
            h = y
        }

        private fun addMap(url: String, x: Float, y: Float, size: Float, loadMap: (String, (Bitmap?) -> Unit) -> Unit) {
            val r = 28f * s
            val holder = View(context).apply {
                background = android.graphics.drawable.GradientDrawable().apply { cornerRadius = r; setColor(0x24FFFFFF) }
            }
            val img = ImageView(context).apply {
                scaleType = ImageView.ScaleType.CENTER_CROP
                outlineProvider = object : ViewOutlineProvider() { override fun getOutline(v: View, o: Outline) { o.setRoundRect(0, 0, v.width, v.height, r) } }
                clipToOutline = true
                alpha = 0f
            }
            val lp = { LayoutParams(Math.round(size * s), Math.round(size * s)).apply { leftMargin = Math.round(x * s); topMargin = Math.round(y * s) } }
            addView(holder, lp()); addView(img, lp())
            loadMap(url) { b -> if (b != null) { img.setImageBitmap(b); img.animate().alpha(1f).setDuration(240).setInterpolator(EASE_ARRIVE).start(); holder.animate().alpha(0f).setDuration(240).start() } }
        }

        override fun onDraw(c: Canvas) {
            for ((i, ls) in layouts.withIndex()) {
                val (x, y) = blocks[i]
                for ((sl, dy) in ls) { c.save(); c.translate(x, y + dy); sl.draw(c); c.restore() }
            }
        }
    }

    /** The shade that keeps the cards readable: a gradient up from the bottom, or across the left band. */
    private class Shade(ctx: Context) : View(ctx) {
        enum class Mode { BOTTOM_TALL, BOTTOM_LOW, LEFT_BAND }
        private var mode = Mode.BOTTOM_TALL
        private var bandEdge = 0f
        private val p = Paint()
        init { setLayerType(LAYER_TYPE_HARDWARE, null) }
        fun set(m: Mode, edge4k: Float) { mode = m; bandEdge = edge4k; invalidate() }
        override fun onDraw(c: Canvas) {
            val w = width.toFloat(); val h = height.toFloat(); val k = w / 3840f
            when (mode) {
                Mode.BOTTOM_TALL -> {
                    p.shader = LinearGradient(0f, h, 0f, h - 1420f * k, intArrayOf(0xD9000000.toInt(), 0x8C000000.toInt(), 0x00000000),
                        floatArrayOf(0f, .55f, 1f), Shader.TileMode.CLAMP)
                    c.drawRect(0f, h - 1420f * k, w, h, p)
                }
                Mode.BOTTOM_LOW -> {
                    p.shader = LinearGradient(0f, h, 0f, h - 820f * k, intArrayOf(0xBF000000.toInt(), 0x73000000, 0x00000000),
                        floatArrayOf(0f, .55f, 1f), Shader.TileMode.CLAMP)
                    c.drawRect(0f, h - 820f * k, w, h, p)
                }
                Mode.LEFT_BAND -> {
                    p.shader = LinearGradient(0f, 0f, bandEdge * k, 0f, intArrayOf(0x99000000.toInt(), 0x59000000, 0x14000000),
                        floatArrayOf(0f, .7f, 1f), Shader.TileMode.CLAMP)
                    c.drawRect(0f, 0f, bandEdge * k, h, p)
                }
            }
            // a soft corner for the hint, top right
            p.shader = android.graphics.RadialGradient(w, 0f, 900f * k, intArrayOf(0x73000000, 0x00000000), null, Shader.TileMode.CLAMP)
            c.drawRect(w - 900f * k, 0f, w, 900f * k, p)
        }
    }

    /** "OK  Close" in the top right corner, like the stock ambient hints. */
    private class HintView(ctx: Context) : View(ctx) {
        private val glow = Glow(.6f, 40f)
        init { setLayerType(LAYER_TYPE_HARDWARE, null) }
        override fun onDraw(c: Canvas) {
            val s = width / 3840f
            val key = paintFor(TStyle(Fonts.Face.TEXT_MEDIUM, 34f, withAlpha(INK, .9f)), s, glow)
            val lab = paintFor(TStyle(Fonts.Face.TEXT, 40f, withAlpha(INK, .72f)), s, glow)
            val label = "Close"
            val right = (3840f - 200f) * s
            val base = 170f * s
            val lw = lab.measureText(label)
            val kw = key.measureText("OK") + 40f * s
            val kx = right - lw - 24f * s - kw
            // the key cap: a rounded outline around "OK"
            val cap = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 3f * s; color = withAlpha(INK, .55f) }
            c.drawRoundRect(kx, base - 46f * s, kx + kw, base + 16f * s, 31f * s, 31f * s, cap)
            c.drawText("OK", kx + 20f * s, base, key)
            c.drawText(label, right - lw, base, lab)
        }
    }
}
