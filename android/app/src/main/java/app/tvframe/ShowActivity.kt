package app.tvframe

import android.app.Activity
import android.os.Bundle
import android.util.Log
import android.view.KeyEvent

/**
 * The frame on demand: what the star button opens when no slideshow is up. It does not hold the screen
 * on, so after the usual 10 minutes the screensaver takes over. Back closes it.
 */
class ShowActivity : Activity() {
    private var show: FrameView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.decorView.systemUiVisibility = android.view.View.SYSTEM_UI_FLAG_FULLSCREEN or
            android.view.View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or android.view.View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
    }

    override fun onStart() {
        super.onStart()
        val v = FrameView(this, FrameView.Role.SHOW, label = "show")
        show = v
        setContentView(v)
        SlideshowView.preferHighestMode(window, window?.decorView?.display)
        v.start()
        Log.i(TAG, "show activity started")
    }

    override fun onStop() {
        show?.stop()
        show = null
        super.onStop()
        // a show you leave is over; coming back is one star press
        finish()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean =
        if (show?.handleKey(event) == true) true else super.dispatchKeyEvent(event)

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean = when (keyCode) {
        KeyEvent.KEYCODE_BACK, KeyEvent.KEYCODE_ESCAPE -> { finish(); true }
        KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT,
        KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN -> true
        else -> super.onKeyDown(keyCode, event)
    }
}
