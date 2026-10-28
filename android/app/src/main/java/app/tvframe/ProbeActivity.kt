package app.tvframe

import android.app.Activity
import android.app.ActivityManager
import android.graphics.BitmapFactory
import android.graphics.Rect
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.KeyEvent
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URL
import java.util.concurrent.Executors

/**
 * Read-only device check. Shows and saves (to /sdcard/Android/data/app.tvframe/files/probe.json)
 * the display modes, UI plane size, HDR support, memory and every decoder that matters.
 * Press OK (or start with --ez chart true) for the resolution chart: left half drawn on the UI
 * plane, right half on the video plane. If the left half's 1 px lines turn gray and the right
 * half's stay sharp, the UI plane is below 4K and the video plane is real 4K.
 */
class ProbeActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private val bg = Executors.newSingleThreadExecutor()
    private lateinit var root: FrameLayout
    private var decoder: StillDecoder? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        root = FrameLayout(this).apply { setBackgroundColor(Ui.INK) }
        setContentView(root)
        if (intent.getBooleanExtra("chart", false)) showChart() else showInfo()
    }

    private fun showInfo() {
        val j = collect()
        val text = j.toString(2)
        runCatching { File(getExternalFilesDir(null), "probe.json").writeText(text) }
        text.chunked(3000).forEach { Log.i("$TAG-probe", it) }
        root.removeAllViews()
        root.addView(ScrollView(this).apply {
            isFocusable = true
            addView(TextView(this@ProbeActivity).apply {
                this.text = "Device check (read only). Press OK for the 4K resolution chart.\n\n$text"
                typeface = android.graphics.Typeface.MONOSPACE
                textSize = 12f
                setTextColor(Ui.TEXT_DIM)
                setPadding(Ui.dpi(this@ProbeActivity, 48), Ui.dpi(this@ProbeActivity, 32), Ui.dpi(this@ProbeActivity, 48), Ui.dpi(this@ProbeActivity, 32))
            })
        })
    }

    private fun collect(): JSONObject {
        val j = JSONObject()
        j.put("build", JSONObject().put("fingerprint", Build.FINGERPRINT).put("model", Build.MODEL)
            .put("device", Build.DEVICE).put("sdk", Build.VERSION.SDK_INT).put("soc", Build.SOC_MODEL))
        val d = display!!
        val m = d.mode
        val dm = resources.displayMetrics
        val wb = windowManager.currentWindowMetrics.bounds
        j.put("display", JSONObject()
            .put("mode", "${m.physicalWidth}x${m.physicalHeight}@${m.refreshRate}")
            .put("modes", JSONArray(d.supportedModes.map { "${it.modeId}: ${it.physicalWidth}x${it.physicalHeight}@${it.refreshRate}" }))
            .put("ui_plane_px", "${dm.widthPixels}x${dm.heightPixels}")
            .put("window_bounds", "${wb.width()}x${wb.height()}")
            .put("density_dpi", dm.densityDpi)
            .put("hdr_types", JSONArray(d.hdrCapabilities?.supportedHdrTypes?.toList() ?: emptyList<Int>()))
            .put("is_hdr", d.isHdr)
            .put("wide_color", d.isWideColorGamut)
            .put("ui_plane_is_4k", dm.widthPixels >= 3840))
        val am = getSystemService(ActivityManager::class.java)
        val mi = ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }
        j.put("memory", JSONObject().put("total_mb", mi.totalMem shr 20).put("avail_mb", mi.availMem shr 20)
            .put("low_ram", am.isLowRamDevice).put("app_heap_mb", am.memoryClass).put("app_large_heap_mb", am.largeMemoryClass))
        val dec = JSONArray()
        val mimes = listOf("video/hevc", "video/av01", "video/x-vnd.on2.vp9", "video/avc", "video/dolby-vision")
        for (ci in MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos) {
            if (ci.isEncoder) continue
            for (t in ci.supportedTypes) {
                if (t.lowercase() !in mimes) continue
                val caps = runCatching { ci.getCapabilitiesForType(t) }.getOrNull() ?: continue
                val vc = caps.videoCapabilities
                dec.put(JSONObject().put("name", ci.name).put("type", t).put("hw", ci.isHardwareAccelerated)
                    .put("max", "${vc.supportedWidths.upper}x${vc.supportedHeights.upper}")
                    .put("4k30", runCatching { vc.areSizeAndRateSupported(3840, 2160, 30.0) }.getOrDefault(false))
                    .put("4k60", runCatching { vc.areSizeAndRateSupported(3840, 2160, 60.0) }.getOrDefault(false))
                    .put("main10", caps.profileLevels.any {
                        it.profile == MediaCodecInfo.CodecProfileLevel.HEVCProfileMain10 && t == "video/hevc" ||
                            it.profile == MediaCodecInfo.CodecProfileLevel.AV1ProfileMain10 && t == "video/av01" ||
                            it.profile == MediaCodecInfo.CodecProfileLevel.VP9Profile2 && t == "video/x-vnd.on2.vp9"
                    }))
            }
        }
        j.put("decoders", dec)
        j.put("still_codec_auto", StillCodec.pick("auto", Item("x", "photo", "", "h", "a", 1.0, null, .5f, .5f, "cover", 0.0, null, hevc8 = "h8"))?.second?.label ?: "none")
        return j
    }

    private fun chartUrl(ext: String) = URL(URL(Prefs(this).playlistUrl), "/media/test/chart.$ext").toString()

    private fun showChart() {
        root.removeAllViews()
        val sv = SurfaceView(this)
        root.addView(sv, FrameLayout.LayoutParams(-1, -1))
        val img = ImageView(this).apply { scaleType = ImageView.ScaleType.FIT_XY }
        root.addView(img, FrameLayout.LayoutParams(-1, -1))
        fun label(s: String, g: Int) = Ui.text(this, s, 14f, Ui.TEXT, "sans-serif-medium").apply {
            setBackgroundColor(0xCC000000.toInt()); setPadding(16, 8, 16, 8)
        }.also { root.addView(it, FrameLayout.LayoutParams(-2, -2, Gravity.TOP or g).apply { topMargin = 24; marginStart = 24; marginEnd = 24 }) }
        label("UI plane (bitmap)", Gravity.START)
        label("Video plane (${if (StillCodec.hevc10) "HEVC 10-bit" else "HEVC 8-bit"})", Gravity.END)
        root.addOnLayoutChangeListener { v, _, _, _, _, _, _, _, _ -> img.clipBounds = Rect(0, 0, v.width / 2, v.height) }
        var surface: android.view.Surface? = null
        var file: File? = null
        fun tryDecode() {
            val sf = surface ?: return
            val f = file ?: return
            val choice = StillCodec.all()["hevc"] ?: StillCodec.all()["hevc8"] ?: return
            bg.execute {
                val d = StillDecoder(main).also { decoder = it }
                runCatching { d.decode(f, sf, choice) { Log.i(TAG, "chart on video plane") } }
                    .onFailure { Log.w(TAG, "chart decode failed: $it") }
            }
        }
        sv.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(h: SurfaceHolder) { surface = h.surface; tryDecode() }
            override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, hh: Int) {}
            override fun surfaceDestroyed(h: SurfaceHolder) { surface = null; bg.execute { decoder?.release() } }
        })
        bg.execute {
            try {
                val cache = MediaCache(this)
                val png = cache.get(chartUrl("png"))
                val bmp = BitmapFactory.decodeFile(png.path)
                main.post { img.setImageBitmap(bmp); Log.i(TAG, "chart bitmap ${bmp.width}x${bmp.height} in view ${img.width}x${img.height}") }
                val mp4 = if (StillCodec.hevc10) cache.get(chartUrl("mp4")) else File(cache.get(chartUrl("mp4").replace("chart.mp4", "chart8.mp4")).path)
                main.post { file = mp4; tryDecode() }
            } catch (e: Exception) {
                Log.w(TAG, "chart failed: $e")
            }
        }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_ENTER) { showChart(); return true }
        return super.onKeyDown(keyCode, event)
    }

    override fun onDestroy() {
        decoder?.release()
        bg.shutdownNow()
        super.onDestroy()
    }
}
