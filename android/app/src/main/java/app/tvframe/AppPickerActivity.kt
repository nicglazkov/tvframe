package app.tvframe

import android.app.Activity
import android.os.Bundle
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** Pick which apps the home screen shows, in the order you pick them. */
class AppPickerActivity : Activity() {
    private lateinit var list: LinearLayout
    private lateinit var prefs: Prefs
    private fun dp(v: Int) = Ui.dpi(this, v)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val page = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(96), dp(48), dp(96), 0)
            setBackgroundColor(Ui.INK)
        }
        page.addView(Ui.eyebrow(this, "Home screen"))
        page.addView(Ui.text(this, "Choose apps", 32f, Ui.TEXT, "sans-serif-light"),
            LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(8) })
        page.addView(Ui.text(this, "Selected apps appear on the home screen in the order you select them. With none selected, every TV app shows.", 15f, Ui.TEXT_DIM).apply {
            setLineSpacing(0f, 1.25f)
        }, LinearLayout.LayoutParams(dp(560), -2).apply { topMargin = dp(10); bottomMargin = dp(20) })
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(0, 0, 0, dp(48)) }
        page.addView(ScrollView(this).apply { addView(list); isVerticalScrollBarEnabled = false; clipToPadding = false },
            LinearLayout.LayoutParams(dp(640), 0, 1f))
        setContentView(page)
        build(null)
    }

    private fun build(focusPkg: String?) {
        list.removeAllViews()
        val pinned = prefs.pinned
        val apps = Apps.list(this)
        val ordered = pinned.mapNotNull { p -> apps.firstOrNull { it.pkg == p } } + apps.filter { it.pkg !in pinned }
        var focusView: android.view.View? = null
        for (a in ordered) {
            val idx = pinned.indexOf(a.pkg)
            val on = idx >= 0
            val rowView = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(14), dp(10), dp(18), dp(10))
                isFocusable = true
                isClickable = true
                background = Ui.focusable(this@AppPickerActivity)
                addView(ImageView(this@AppPickerActivity).apply { setImageDrawable(a.ri.loadIcon(packageManager)) },
                    LinearLayout.LayoutParams(dp(36), dp(36)))
                addView(Ui.text(this@AppPickerActivity, a.label, 18f, if (on) Ui.TEXT else Ui.TEXT_DIM).apply { isSingleLine = true },
                    LinearLayout.LayoutParams(0, -2, 1f).apply { marginStart = dp(16) })
                addView(TextView(this@AppPickerActivity).apply {
                    text = if (on) "${idx + 1}" else ""
                    gravity = Gravity.CENTER
                    textSize = 14f
                    setTextColor(Ui.INK)
                    typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
                    background = if (on) Ui.rounded(this@AppPickerActivity, Ui.ACCENT, 13f)
                        else Ui.rounded(this@AppPickerActivity, 0, 13f, Ui.TEXT_FAINT, 1.5f)
                }, LinearLayout.LayoutParams(dp(26), dp(26)))
                setOnClickListener {
                    val p = prefs.pinned.toMutableList()
                    if (!p.remove(a.pkg)) p += a.pkg
                    prefs.pinned = p
                    build(a.pkg)
                }
            }
            list.addView(rowView, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(4) })
            if (a.pkg == focusPkg) focusView = rowView
        }
        (focusView ?: list.getChildAt(0))?.requestFocus()
    }
}
