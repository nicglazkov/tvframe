package app.tvframe

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.util.Log
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView

/**
 * Settings for the screensaver and home screen. Also takes adb extras, so nothing has to be
 * typed on the remote:
 *   am start -n app.tvframe/.SettingsActivity --es url http://host:8151/playlist.json --es mode b --es codec auto
 *   am start -n app.tvframe/.SettingsActivity --es pin com.netflix.ninja,com.google.android.youtube.tv
 *   am start -n app.tvframe/.SettingsActivity --es server http://host --ez close true   (the installer: every
 *       address at once, and no settings page left open)
 */
class SettingsActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var page: LinearLayout
    private fun dp(v: Int) = Ui.dpi(this, v)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        applyExtras(intent)
        if (intent.getBooleanExtra("close", false)) { finish(); return }
        page = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(96), dp(36), dp(96), dp(36))
        }
        setContentView(ScrollView(this).apply { setBackgroundColor(Ui.INK); addView(page); isVerticalScrollBarEnabled = false })
        build()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        applyExtras(intent)
        if (intent.getBooleanExtra("close", false)) { finish(); return }
        build()
    }

    private fun applyExtras(i: Intent) {
        i.getStringExtra("server")?.trim()?.trimEnd('/')?.takeIf { Regex("https?://[A-Za-z0-9.-]+").matches(it) }?.let {
            prefs.playlistUrl = "$it:8151/playlist.json"; prefs.prodUrl = "$it:8151/prod.json"; prefs.bridgeUrl = "$it:8154"
        }
        i.getStringExtra("url")?.let { prefs.playlistUrl = it }
        i.getStringExtra("prod_url")?.let { prefs.prodUrl = it }
        i.getStringExtra("bridge_url")?.let { prefs.bridgeUrl = it }
        i.getStringExtra("mode")?.takeIf { it in setOf("auto", "a", "b", "c") }?.let { prefs.mode = it }
        i.getStringExtra("codec")?.takeIf { it in setOf("auto", "hevc", "av1") }?.let { prefs.codec = it }
        i.getStringExtra("pin")?.let { prefs.pinned = it.split(',').map(String::trim).filter(String::isNotEmpty) }
        Log.i(TAG, "settings url=${prefs.playlistUrl} mode=${prefs.mode} codec=${prefs.codec} pinned=${prefs.pinned}")
    }

    private fun section(title: String, top: Int = 24) {
        page.addView(Ui.eyebrow(this, title), LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(top); bottomMargin = dp(10) })
    }

    private fun build() {
        page.removeAllViews()
        page.addView(Ui.eyebrow(this, "TV Frame"))
        page.addView(Ui.text(this, "Slideshow settings", 28f, Ui.TEXT, "sans-serif-light"),
            LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(6) })

        section("Photo server")
        val url = EditText(this).apply {
            setText(prefs.playlistUrl)
            setTextColor(Ui.TEXT)
            textSize = 17f
            isSingleLine = true
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            imeOptions = EditorInfo.IME_ACTION_DONE
            background = Ui.focusable(this@SettingsActivity, Ui.PANEL, Ui.PANEL_FOCUS, 10f)
            setPadding(dp(16), dp(12), dp(16), dp(12))
            setOnEditorActionListener { v, _, _ -> prefs.playlistUrl = v.text.toString(); false }
            setOnFocusChangeListener { v, f -> if (!f) prefs.playlistUrl = (v as EditText).text.toString() }
        }
        page.addView(url, LinearLayout.LayoutParams(dp(620), -2))

        section("How photos are drawn")
        val modes = listOf("auto" to "Server decides", "a" to "Standard (HD)", "b" to "True 4K", "c" to "4K loop video")
        page.addView(Ui.row(this, 12, *modes.map { (k, l) -> Ui.chip(this, l, prefs.mode == k) { prefs.mode = k; build() } }.toTypedArray()))
        page.addView(Ui.text(this, when (prefs.mode) {
            "a" -> "Photos are drawn on the TV's interface layer, which most TVs run at 1080p."
            "b" -> "Each photo is sent as a single 4K video frame, so it shows at the screen's full resolution."
            "c" -> "The server renders the whole slideshow as one 4K video and the TV plays it on repeat."
            else -> "Uses the mode set in the server's config.json."
        }, 14f, Ui.TEXT_FAINT), LinearLayout.LayoutParams(dp(620), -2).apply { topMargin = dp(10) })

        section("4K still format")
        val codecs = StillCodec.all()
        val cl = listOf("auto" to "Automatic", "hevc" to "HEVC", "av1" to "AV1")
        page.addView(Ui.row(this, 12, *cl.map { (k, l) -> Ui.chip(this, l, prefs.codec == k) { prefs.codec = k; build() } }.toTypedArray()))
        val found = codecs.values.filterNotNull().map { it.label }
        page.addView(Ui.text(this, if (found.isEmpty()) "No 4K still decoder found on this TV, so True 4K falls back to Standard."
            else "Decoders found: " + found.joinToString(",  "), 14f, Ui.TEXT_FAINT),
            LinearLayout.LayoutParams(dp(760), -2).apply { topMargin = dp(10) })

        section("More")
        page.addView(Ui.row(this, 12,
            Ui.chip(this, "Preview slideshow", false) { startActivity(Intent(this, DreamPreviewActivity::class.java)) },
            Ui.chip(this, "Home screen apps", false) { startActivity(Intent(this, AppPickerActivity::class.java)) },
            Ui.chip(this, "Device check", false) { startActivity(Intent(this, ProbeActivity::class.java)) },
        ))
        page.getChildAt(5)?.requestFocus()   // the mode chips, not the URL field
    }
}
