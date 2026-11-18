package app.tvframe

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.os.Debug
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.util.Log

class TvFrameApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Fonts.load(this)
        MemoryValve.watch(this)
    }
}

/**
 * Native memory in this process grows over days of screensaver (0.4.1 reached 1.2 GB in 6 days, almost all of it
 * swapped out, which leaves the Streamer short of memory). FrameView logs the size with every slide so the cause can
 * be found. Until then: past [LIMIT_MB], the process quietly starts over at a moment when none of its screens is up
 * (the screensaver has stopped and no activity of ours is showing). Android brings the Home key service back by itself.
 */
object MemoryValve {
    const val LIMIT_MB = 600L
    private const val GRACE_MS = 5_000L
    private val main = Handler(Looper.getMainLooper())
    private var started = 0

    fun nativeMb(): Long = Debug.getNativeHeapAllocatedSize() / 1_048_576

    fun watch(app: Application) {
        app.registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
            override fun onActivityStarted(a: Activity) { started++ }
            override fun onActivityStopped(a: Activity) { started = (started - 1).coerceAtLeast(0); check() }
            override fun onActivityCreated(a: Activity, b: Bundle?) {}
            override fun onActivityResumed(a: Activity) {}
            override fun onActivityPaused(a: Activity) {}
            override fun onActivitySaveInstanceState(a: Activity, b: Bundle) {}
            override fun onActivityDestroyed(a: Activity) {}
        })
    }

    /** Call when something of ours leaves the screen (the screensaver stopping counts: it runs as an activity). */
    fun check() {
        if (started > 0 || nativeMb() < LIMIT_MB) return
        main.postDelayed({
            val mb = nativeMb()
            if (started == 0 && mb >= LIMIT_MB) {
                Log.w(TAG, "native memory at $mb MB with nothing of ours on screen: starting the process over")
                Process.killProcess(Process.myPid())
            }
        }, GRACE_MS)
    }
}
