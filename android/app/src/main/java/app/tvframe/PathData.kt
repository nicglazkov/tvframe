package app.tvframe

import android.graphics.Path
import android.graphics.RectF
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.acos
import kotlin.math.sqrt

/** Minimal SVG path-data parser (M L H V C S Q A Z, absolute and relative). No AndroidX here. */
object PathData {
    fun parse(d: String): Path {
        val path = Path()
        val tokens = Regex("[MmLlHhVvCcSsQqAaZz]|-?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][-+]?\\d+)?").findAll(d).map { it.value }.toList()
        var i = 0
        var cmd = 'M'
        var x = 0f; var y = 0f; var sx = 0f; var sy = 0f
        var lcx = 0f; var lcy = 0f
        fun num(): Float = tokens[i++].toFloat()
        fun hasNum() = i < tokens.size && !tokens[i][0].isLetter()
        while (i < tokens.size) {
            if (tokens[i][0].isLetter()) cmd = tokens[i++][0]
            val rel = cmd.isLowerCase()
            val ox = if (rel) x else 0f; val oy = if (rel) y else 0f
            when (cmd.uppercaseChar()) {
                'M' -> { x = ox + num(); y = oy + num(); path.moveTo(x, y); sx = x; sy = y; cmd = if (rel) 'l' else 'L'; lcx = x; lcy = y }
                'L' -> { x = ox + num(); y = oy + num(); path.lineTo(x, y); lcx = x; lcy = y }
                'H' -> { x = ox + num(); path.lineTo(x, y); lcx = x; lcy = y }
                'V' -> { y = oy + num(); path.lineTo(x, y); lcx = x; lcy = y }
                'C' -> {
                    val x1 = ox + num(); val y1 = oy + num(); val x2 = ox + num(); val y2 = oy + num()
                    x = ox + num(); y = oy + num(); path.cubicTo(x1, y1, x2, y2, x, y); lcx = x2; lcy = y2
                }
                'S' -> {
                    val x1 = 2 * x - lcx; val y1 = 2 * y - lcy; val x2 = ox + num(); val y2 = oy + num()
                    x = ox + num(); y = oy + num(); path.cubicTo(x1, y1, x2, y2, x, y); lcx = x2; lcy = y2
                }
                'Q' -> { val x1 = ox + num(); val y1 = oy + num(); x = ox + num(); y = oy + num(); path.quadTo(x1, y1, x, y); lcx = x1; lcy = y1 }
                'A' -> {
                    val rx = num(); val ry = num(); val rot = num(); val large = num() != 0f; val sweep = num() != 0f
                    val nx = ox + num(); val ny = oy + num()
                    arc(path, x, y, nx, ny, rx, ry, rot, large, sweep)
                    x = nx; y = ny; lcx = x; lcy = y
                }
                'Z' -> { path.close(); x = sx; y = sy; lcx = x; lcy = y; if (hasNum()) cmd = 'L' else continue }
            }
        }
        return path
    }

    /** SVG endpoint arc to center form (rotation ignored: the icons only use circles). */
    private fun arc(p: Path, x1: Float, y1: Float, x2: Float, y2: Float, rx0: Float, ry0: Float, @Suppress("UNUSED_PARAMETER") rot: Float, large: Boolean, sweep: Boolean) {
        if (rx0 == 0f || ry0 == 0f) { p.lineTo(x2, y2); return }
        var rx = abs(rx0).toDouble(); var ry = abs(ry0).toDouble()
        val dx = (x1 - x2) / 2.0; val dy = (y1 - y2) / 2.0
        val lam = dx * dx / (rx * rx) + dy * dy / (ry * ry)
        if (lam > 1) { rx *= sqrt(lam); ry *= sqrt(lam) }
        val num = rx * rx * ry * ry - rx * rx * dy * dy - ry * ry * dx * dx
        val den = rx * rx * dy * dy + ry * ry * dx * dx
        var co = sqrt((num / den).coerceAtLeast(0.0))
        if (large == sweep) co = -co
        val cxp = co * rx * dy / ry; val cyp = -co * ry * dx / rx
        val cx = cxp + (x1 + x2) / 2.0; val cy = cyp + (y1 + y2) / 2.0
        fun ang(ux: Double, uy: Double, vx: Double, vy: Double): Double {
            val dot = ux * vx + uy * vy; val len = sqrt(ux * ux + uy * uy) * sqrt(vx * vx + vy * vy)
            var a = acos((dot / len).coerceIn(-1.0, 1.0)); if (ux * vy - uy * vx < 0) a = -a; return a
        }
        val t1 = ang(1.0, 0.0, (dx - cxp) / rx, (dy - cyp) / ry)
        var dt = ang((dx - cxp) / rx, (dy - cyp) / ry, (-dx - cxp) / rx, (-dy - cyp) / ry)
        if (!sweep && dt > 0) dt -= 2 * PI else if (sweep && dt < 0) dt += 2 * PI
        p.arcTo(RectF((cx - rx).toFloat(), (cy - ry).toFloat(), (cx + rx).toFloat(), (cy + ry).toFloat()),
            Math.toDegrees(t1).toFloat(), Math.toDegrees(dt).toFloat(), false)
    }
}
