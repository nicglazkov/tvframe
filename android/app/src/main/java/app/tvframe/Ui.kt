package app.tvframe

import android.content.Context
import android.content.pm.PackageManager
import android.content.pm.ResolveInfo
import android.content.Intent
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView

/** Shared look: warm off-white on near-black, one amber accent, rounded panels. */
object Ui {
    const val INK = 0xFF0B0C0E.toInt()
    const val PANEL = 0xFF16181C.toInt()
    const val PANEL_FOCUS = 0xFF2A2E35.toInt()
    const val TEXT = 0xFFF3F1EC.toInt()
    const val TEXT_DIM = 0xB3F3F1EC.toInt()
    const val TEXT_FAINT = 0x80F3F1EC.toInt()
    const val ACCENT = 0xFFE8B04A.toInt()

    fun dp(ctx: Context, v: Float) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, ctx.resources.displayMetrics)
    fun dpi(ctx: Context, v: Int) = dp(ctx, v.toFloat()).toInt()

    fun rounded(ctx: Context, color: Int, radiusDp: Float, strokeColor: Int = 0, strokeDp: Float = 0f) =
        GradientDrawable().apply {
            setColor(color)
            cornerRadius = dp(ctx, radiusDp)
            if (strokeDp > 0) setStroke(dp(ctx, strokeDp).toInt(), strokeColor)
        }

    /** Background that lights up under D-pad focus. */
    fun focusable(ctx: Context, normal: Int = 0x00000000, focused: Int = PANEL_FOCUS, radiusDp: Float = 12f): Drawable =
        StateListDrawable().apply {
            addState(intArrayOf(android.R.attr.state_focused), rounded(ctx, focused, radiusDp, ACCENT, 2f))
            addState(intArrayOf(android.R.attr.state_pressed), rounded(ctx, focused, radiusDp))
            addState(intArrayOf(), rounded(ctx, normal, radiusDp))
        }

    fun text(ctx: Context, s: String, sp: Float, color: Int = TEXT, family: String = "sans-serif", style: Int = Typeface.NORMAL) =
        TextView(ctx).apply {
            text = s
            setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
            setTextColor(color)
            typeface = Typeface.create(family, style)
            includeFontPadding = false
        }

    fun eyebrow(ctx: Context, s: String) = text(ctx, s.uppercase(), 12f, TEXT_FAINT, "sans-serif-medium").apply {
        letterSpacing = 0.14f
    }

    /** A selectable pill for settings choices. */
    fun chip(ctx: Context, label: String, selected: Boolean, onClick: () -> Unit) =
        text(ctx, label, 15f, if (selected) INK else TEXT, "sans-serif-medium").apply {
            gravity = Gravity.CENTER
            val p = dpi(ctx, 16)
            setPadding(p, dpi(ctx, 10), p, dpi(ctx, 10))
            isFocusable = true
            isClickable = true
            background = StateListDrawable().apply {
                val base = if (selected) ACCENT else PANEL
                addState(intArrayOf(android.R.attr.state_focused), rounded(ctx, if (selected) ACCENT else PANEL_FOCUS, 22f, TEXT, 2f))
                addState(intArrayOf(), rounded(ctx, base, 22f))
            }
            setOnClickListener { onClick() }
        }

    fun row(ctx: Context, gapDp: Int, vararg views: View) = LinearLayout(ctx).apply {
        orientation = LinearLayout.HORIZONTAL
        views.forEachIndexed { i, v ->
            addView(v, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { if (i > 0) marginStart = dpi(ctx, gapDp) })
        }
    }
}

/** Installed apps that a TV launcher should offer (leanback first, phone-style launcher entries as fallback). */
object Apps {
    data class App(val pkg: String, val label: String, val ri: ResolveInfo)

    fun list(ctx: Context): List<App> {
        val pm = ctx.packageManager
        val seen = LinkedHashMap<String, App>()
        for (cat in listOf(Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER)) {
            val q = pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(cat), PackageManager.ResolveInfoFlags.of(0))
            for (ri in q) {
                val pkg = ri.activityInfo.packageName
                if (pkg == ctx.packageName || pkg in seen) continue
                seen[pkg] = App(pkg, ri.loadLabel(pm).toString(), ri)
            }
        }
        return seen.values.sortedBy { it.label.lowercase() }
    }

    fun launch(ctx: Context, pkg: String) {
        val pm = ctx.packageManager
        val i = pm.getLeanbackLaunchIntentForPackage(pkg) ?: pm.getLaunchIntentForPackage(pkg) ?: return
        ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
