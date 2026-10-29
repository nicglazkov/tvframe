package app.tvframe

import android.app.Application

class TvFrameApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Fonts.load(this)
    }
}
