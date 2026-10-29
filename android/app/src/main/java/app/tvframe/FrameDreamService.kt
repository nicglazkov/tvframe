package app.tvframe

import android.service.dreams.DreamService
import android.util.Log
import android.view.KeyEvent

/** The screensaver: Glance. */
class FrameDreamService : DreamService() {
    private var show: FrameView? = null

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        // OK shows the photo's details (and closes them); Back closes them. Every other key wakes the TV, like the
        // stock ambient mode. The Home key never gets here: the accessibility service takes it first.
        isInteractive = true
        isFullscreen = true
        isScreenBright = true
        val v = FrameView(this, FrameView.Role.SHOW, label = "dream")
        show = v
        setContentView(v)
        SlideshowView.preferHighestMode(window, window?.decorView?.display)
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (show?.handleKey(event) == true) return true
        if (event.action == KeyEvent.ACTION_UP) wakeUp()
        return true
    }

    override fun onDreamingStarted() {
        super.onDreamingStarted()
        Log.i(TAG, "dream started")
        show?.start()
    }

    override fun onDreamingStopped() {
        Log.i(TAG, "dream stopped")
        show?.stop()
        show = null
        super.onDreamingStopped()
    }

    override fun onDetachedFromWindow() {
        show?.stop()
        show = null
        super.onDetachedFromWindow()
    }
}
