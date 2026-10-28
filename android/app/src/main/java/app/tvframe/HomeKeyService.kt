package app.tvframe

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Intent
import android.os.SystemClock
import android.util.Log
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent

/**
 * Makes Quiet Home the Streamer's home screen while Google TV's own home (launcherx) stays installed and
 * enabled, so casting, voice, updates and the remote's app buttons keep working. A tap on Home opens Quiet
 * Home and a long press opens Google's settings panel, as it did before. When Google's home comes up by
 * itself (boot, wake, leaving an app) it is swapped for Quiet Home, unless it was picked from the Google TV
 * home tile. Sees only the Home key and which app is in front; no window content.
 */
class HomeKeyService : AccessibilityService() {
    companion object {
        private const val GOOGLE_HOME = "com.google.android.apps.tv.launcherx.home.HomeActivity"
        private const val LONG_MS = 500L
        /** Set by the Google TV home tile: let Google's home stay until another app comes to the front. */
        @Volatile var allowGoogleHome = false
    }

    private var downAt = 0L

    override fun onServiceConnected() { Log.i(TAG, "home key service connected") }

    override fun onKeyEvent(e: KeyEvent): Boolean {
        if (e.keyCode != KeyEvent.KEYCODE_HOME) return false
        if (e.action == KeyEvent.ACTION_DOWN) {
            if (e.repeatCount == 0) downAt = SystemClock.uptimeMillis()
            return true
        }
        if (e.action == KeyEvent.ACTION_UP && !e.isCanceled) {
            if (SystemClock.uptimeMillis() - downAt >= LONG_MS) {
                performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)       // Google TV's settings panel
            } else {
                allowGoogleHome = false
                openQuietHome("home key")
            }
        }
        return true
    }

    override fun onAccessibilityEvent(ev: AccessibilityEvent?) {
        if (ev?.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        val pkg = ev.packageName?.toString() ?: return
        when {
            ev.className == GOOGLE_HOME && !allowGoogleHome -> openQuietHome("Google home came up")
            pkg != QuietHomeActivity.LAUNCHERX && pkg != "com.android.systemui" && pkg != packageName -> allowGoogleHome = false
        }
    }

    private fun openQuietHome(why: String) {
        Log.i(TAG, "opening Quiet Home ($why)")
        startActivity(Intent(this, QuietHomeActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
    }

    override fun onInterrupt() {}
}
