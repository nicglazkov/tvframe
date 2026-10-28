package app.tvframe

import android.app.Activity
import android.os.Bundle
import android.view.WindowManager

/**
 * The same slideshow as the screensaver, as a plain activity. Used for previews and for
 * measuring: am start -W -n app.tvframe/.DreamPreviewActivity --es mode b [--es item 110]
 */
class DreamPreviewActivity : Activity() {
    private var show: SlideshowView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val mode = intent.getStringExtra("mode")?.takeIf { it in setOf("a", "b", "c") }
        val v = SlideshowView(this, forceMode = mode, label = "preview", pinItem = intent.getStringExtra("item"))
        show = v
        setContentView(v)
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        SlideshowView.preferHighestMode(window, display)
    }

    override fun onStart() { super.onStart(); show?.start() }

    override fun onStop() {
        show?.stop()
        // a stopped SlideshowView cannot restart; build a fresh one if we come back
        val mode = intent.getStringExtra("mode")?.takeIf { it in setOf("a", "b", "c") }
        show = SlideshowView(this, forceMode = mode, label = "preview", pinItem = intent.getStringExtra("item")).also { setContentView(it) }
        super.onStop()
    }
}
