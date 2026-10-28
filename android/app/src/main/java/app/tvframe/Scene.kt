package app.tvframe

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Shader
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.view.View
import android.view.animation.PathInterpolator

/**
 * A tiny retained scene for the text that sits on the photos. Everything is placed in 4K stage
 * pixels (3840 x 2160, the same numbers as the site's CSS and the tune page) and scaled to the
 * UI plane when drawn, so the layout matches guests.html and glance.html.
 */
val EASE_SOFT = PathInterpolator(0.4f, 0f, 0.2f, 1f)
val EASE_CALM = PathInterpolator(0.22f, 1f, 0.36f, 1f)
/** Photo crossfades and everything that changes with them: slow in, slow out, no visible start or end. */
val EASE_XFADE = PathInterpolator(0.45f, 0f, 0.25f, 1f)

const val INK = 0xFFE8EAED.toInt()          // Google grey 200, the stock ambient text color

fun withAlpha(color: Int, a: Float): Int = (((color ushr 24) * a).toInt().coerceIn(0, 255) shl 24) or (color and 0xFFFFFF)

/** Text look: face, size in 4K px, color, CSS-style line height (0 = the font's normal). */
data class TStyle(
    val face: Fonts.Face, val size: Float, val color: Int = INK, val lineHeight: Float = 0f,
    val tracking: Float = 0f, val glow: Boolean = true,
)

abstract class Node {
    var alpha = 1f
    var dx = 0f
    var dy = 0f
    var visible = true
    abstract fun draw(c: Canvas, s: Float, a: Float, glow: Glow)
}

/** The stock ambient halo: no offset, soft black. alpha and size come from the tune page. */
data class Glow(val alpha: Float, val size4k: Float)

class Group : Node() {
    val kids = ArrayList<Node>()
    fun add(n: Node): Node { kids += n; return n }
    fun clear() = kids.clear()
    override fun draw(c: Canvas, s: Float, a: Float, glow: Glow) {
        if (!visible || alpha * a <= 0.003f) return
        c.save(); c.translate(dx * s, dy * s)
        for (k in kids) if (k.visible) k.draw(c, s, a * alpha, glow)
        c.restore()
    }
}

fun paintFor(st: TStyle, s: Float, glow: Glow, a: Float = 1f): TextPaint {
    val p = TextPaint(Paint.ANTI_ALIAS_FLAG or Paint.SUBPIXEL_TEXT_FLAG)
    p.typeface = Fonts.get(st.face)
    p.textSize = st.size * s
    p.letterSpacing = st.tracking
    applyAlpha(p, st, s, glow, a)
    return p
}

/**
 * Color and glow at an overall opacity. The glow color is translucent, and Android then ignores the
 * paint's alpha for the shadow, so both are scaled here; otherwise the halo would linger during fades.
 */
fun applyAlpha(p: TextPaint, st: TStyle, s: Float, glow: Glow, a: Float) {
    p.color = withAlpha(st.color, a)
    if (st.glow && glow.alpha > 0f && a > 0.003f) {
        // CSS blur b is about two sigma; Skia's radius r gives sigma = 0.57735 r + 0.5
        val sigma = glow.size4k * s / 2f
        p.setShadowLayer(((sigma - 0.5f) / 0.57735f).coerceIn(1f, 25f), 0f, 0f, withAlpha(0xFF000000.toInt(), glow.alpha * a))
    } else p.clearShadowLayer()
}

/** Line box like CSS: ascent and descent centered in the line height. Returns (baselineOffset, boxHeight). */
fun lineBox(p: TextPaint, st: TStyle, s: Float): Pair<Float, Float> {
    val fm = p.fontMetrics
    val content = fm.descent - fm.ascent
    val lh = if (st.lineHeight > 0f) st.lineHeight * st.size * s else content + fm.leading
    return ((lh - content) / 2f - fm.ascent) to lh
}

/** One run of text. y is the baseline (anchorBaseline) or the top of the CSS line box. */
class TextNode(var text: String, var style: TStyle, var x: Float, var y: Float, var anchorBaseline: Boolean = true,
               var alignRight: Boolean = false, var maxW: Float = 0f) : Node() {
    fun width(s: Float, glow: Glow) = paintFor(style, s, glow).measureText(text) / s
    override fun draw(c: Canvas, s: Float, a: Float, glow: Glow) {
        if (text.isEmpty()) return
        val p = paintFor(style, s, glow, a)
        var t: CharSequence = text
        if (maxW > 0f) t = android.text.TextUtils.ellipsize(text, p, maxW * s, android.text.TextUtils.TruncateAt.END)
        val w = p.measureText(t, 0, t.length)
        val bx = if (alignRight) x * s - w else x * s
        val by = if (anchorBaseline) y * s else y * s + lineBox(p, style, s).first
        c.drawText(t, 0, t.length, bx, by, p)
    }
}

/** A paragraph that wraps within w (4K px), top of the first line box at y. */
class ParaNode(var text: String, var style: TStyle, var x: Float, var y: Float, var w: Float, var maxLines: Int = 3,
               var balance: Boolean = true) : Node() {
    private var cacheKey: Any? = null
    private var layout: StaticLayout? = null

    fun layout(s: Float, glow: Glow): StaticLayout {
        val key = listOf(text, style, w, s, Fonts.loaded)
        layout?.let { if (key == cacheKey) return it }
        val p = paintFor(style, s, glow)
        val (_, lh) = lineBox(p, style, s)
        val fm = p.fontMetrics
        val extra = lh - (fm.descent - fm.ascent)
        var width = (w * s).toInt().coerceAtLeast(1)
        var l = StaticLayout.Builder.obtain(text, 0, text.length, p, width).setIncludePad(false)
            .setLineSpacing(extra, 1f).setMaxLines(maxLines).setEllipsize(android.text.TextUtils.TruncateAt.END)
            .setBreakStrategy(Layout.BREAK_STRATEGY_BALANCED).build()
        if (balance && l.lineCount == 2) {
            // text-wrap: balance, roughly: shrink the box until the last line would wrap again
            var lo = width / 2; var hi = width
            while (hi - lo > 4) {
                val mid = (lo + hi) / 2
                val t = StaticLayout.Builder.obtain(text, 0, text.length, p, mid).setIncludePad(false).setLineSpacing(extra, 1f)
                    .setMaxLines(maxLines + 1).build()
                if (t.lineCount <= 2) hi = mid else lo = mid
            }
            width = hi
            l = StaticLayout.Builder.obtain(text, 0, text.length, p, width).setIncludePad(false)
                .setLineSpacing(extra, 1f).setMaxLines(maxLines).setEllipsize(android.text.TextUtils.TruncateAt.END).build()
        }
        layout = l; cacheKey = key
        return l
    }

    /** Height in 4K px (CSS: line count x line height). */
    fun height(s: Float, glow: Glow): Float {
        val p = paintFor(style, s, glow)
        return layout(s, glow).lineCount * lineBox(p, style, s).second / s
    }

    override fun draw(c: Canvas, s: Float, a: Float, glow: Glow) {
        if (text.isEmpty()) return
        val l = layout(s, glow)
        val p = paintFor(style, s, glow)
        val (base, lh) = lineBox(p, style, s)
        applyAlpha(l.paint, style, s, glow, a)
        // StaticLayout puts the first baseline at -ascent; CSS puts it at the half-leading plus ascent
        val shift = base - (-p.fontMetrics.ascent)
        c.save(); c.translate(x * s, y * s + shift)
        l.draw(c)
        c.restore()
    }
}

object Color8 { fun alphaOf(c: Int) = c ushr 24 }

class RectNode(var x: Float, var y: Float, var w: Float, var h: Float, var color: Int, var radius: Float = 0f) : Node() {
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    override fun draw(c: Canvas, s: Float, a: Float, glow: Glow) {
        p.color = color; p.alpha = (Color8.alphaOf(color) * a).toInt()
        if (radius > 0) c.drawRoundRect(x * s, y * s, (x + w) * s, (y + h) * s, radius * s, radius * s, p)
        else c.drawRect(x * s, y * s, (x + w) * s, (y + h) * s, p)
    }
}

/** Stroke icon drawn from SVG-like path data on a 48 grid (the glance icons). */
class IconNode(var paths: List<Path>, var x: Float, var y: Float, var size: Float, var color: Int = INK, var opacity: Float = .9f) : Node() {
    private val p = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND }
    override fun draw(c: Canvas, s: Float, a: Float, glow: Glow) {
        val k = size * s / 48f
        p.color = color; p.alpha = (255 * opacity * a).toInt()
        p.strokeWidth = 2.6f
        c.save(); c.translate(x * s, y * s); c.scale(k, k)
        for (path in paths) c.drawPath(path, p)
        c.restore()
    }
}

/** A photo thumbnail with rounded corners (the "from your photos" tile). */
class ImageNode(var bmp: Bitmap?, var x: Float, var y: Float, var w: Float, var h: Float, var radius: Float) : Node() {
    private val p = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    override fun draw(c: Canvas, s: Float, a: Float, glow: Glow) {
        val b = bmp ?: return
        val r = RectF(x * s, y * s, (x + w) * s, (y + h) * s)
        val sc = maxOf(r.width() / b.width, r.height() / b.height)
        val m = Matrix().apply { setScale(sc, sc); postTranslate(r.centerX() - b.width * sc / 2, r.centerY() - b.height * sc / 2) }
        p.shader = BitmapShader(b, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP).apply { setLocalMatrix(m) }
        p.alpha = (255 * a).toInt()
        c.drawRoundRect(r, radius * s, radius * s, p)
    }
}

/** Draws a scene of groups, scaled from 4K stage pixels to the view. Animations just set alpha and invalidate. */
class SceneView(ctx: Context) : View(ctx) {
    val root = Group()
    var glow = Glow(.6f, 40f)
    private val fontListener = { invalidate() }

    init {
        setWillNotDraw(false)
        setLayerType(LAYER_TYPE_HARDWARE, null)
    }

    override fun onAttachedToWindow() { super.onAttachedToWindow(); Fonts.onChange(fontListener) }
    override fun onDetachedFromWindow() { Fonts.removeListener(fontListener); super.onDetachedFromWindow() }

    val scale get() = (width.takeIf { it > 0 } ?: 1920) / 3840f

    override fun onDraw(canvas: Canvas) { root.draw(canvas, scale, 1f, glow) }

    /** Animate a node's alpha (and optionally its dy) on the UI thread. */
    fun fade(n: Node, to: Float, ms: Long, delay: Long = 0, ease: android.animation.TimeInterpolator = EASE_SOFT,
             dyFrom: Float? = null, dyTo: Float = 0f, end: (() -> Unit)? = null): ValueAnimator {
        val from = n.alpha
        val dy0 = dyFrom ?: n.dy
        return ValueAnimator.ofFloat(0f, 1f).apply {
            duration = ms; startDelay = delay; interpolator = ease
            addUpdateListener { v ->
                val t = v.animatedValue as Float
                n.alpha = from + (to - from) * t
                if (dyFrom != null) n.dy = dy0 + (dyTo - dy0) * t
                invalidate()
            }
            addListener(object : android.animation.AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: android.animation.Animator) { end?.invoke() }
            })
            start()
        }
    }
}

/** Typeface for a CSS weight on the Google faces we load. */
fun face(text: Boolean, weight: Int): Fonts.Face = when {
    text && weight >= 500 -> Fonts.Face.TEXT_MEDIUM
    text -> Fonts.Face.TEXT
    weight >= 500 -> Fonts.Face.SANS_MEDIUM
    else -> Fonts.Face.SANS
}

