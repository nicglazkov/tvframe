package app.tvframe

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaPlayer
import android.os.Handler
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import java.io.File

/** Which decoder plays the one-frame stills in mode b. Picked once per process. */
object StillCodec {
    data class Choice(val mime: String, val codecName: String, val hardware: Boolean, val label: String)

    @Volatile private var cached: Map<String, Choice?>? = null

    private fun find(mime: String, profile: Int): Choice? {
        val list = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
        val ok = list.filter { ci ->
            !ci.isEncoder && ci.supportedTypes.any { it.equals(mime, true) } && runCatching {
                val caps = ci.getCapabilitiesForType(mime)
                caps.videoCapabilities.isSizeSupported(3840, 2160) &&
                    caps.profileLevels.any { it.profile == profile }
            }.getOrDefault(false)
        }
        val best = ok.firstOrNull { it.isHardwareAccelerated } ?: ok.firstOrNull() ?: return null
        val kind = when {
            !mime.endsWith("hevc") -> "AV1 10-bit"
            profile == MediaCodecInfo.CodecProfileLevel.HEVCProfileMain -> "HEVC 8-bit"
            else -> "HEVC 10-bit"
        }
        return Choice(mime, best.name, best.isHardwareAccelerated,
            "$kind ${best.name}${if (best.isHardwareAccelerated) " (hw)" else " (sw)"}")
    }

    fun all(): Map<String, Choice?> = cached ?: mapOf(
        "hevc" to find(MediaFormat.MIMETYPE_VIDEO_HEVC, MediaCodecInfo.CodecProfileLevel.HEVCProfileMain10),
        "av1" to find(MediaFormat.MIMETYPE_VIDEO_AV1, MediaCodecInfo.CodecProfileLevel.AV1ProfileMain10),
        "hevc8" to find(MediaFormat.MIMETYPE_VIDEO_HEVC, MediaCodecInfo.CodecProfileLevel.HEVCProfileMain),
    ).also { cached = it }

    /** True when 10-bit HEVC clips can be decoded at 4K; otherwise use the 8-bit copies. */
    val hevc10: Boolean get() = all()["hevc"] != null

    fun clipUrl(item: Item): String = if (!hevc10 && item.url8 != null) item.url8 else item.url

    /** pref: auto | hevc | av1. Auto prefers hardware HEVC, then hardware AV1, then software. */
    fun pick(pref: String, item: Item): Pair<String, Choice>? {
        val c = all()
        val order = when (pref) {
            "av1" -> listOf("av1", "hevc", "hevc8")
            "hevc" -> listOf("hevc", "av1", "hevc8")
            else -> listOf("hevc", "av1", "hevc8").sortedBy { if (c[it]?.hardware == true) 0 else 1 }
        }
        for (k in order) {
            val url = when (k) { "hevc" -> item.hevc; "av1" -> item.av1; else -> item.hevc8 }
            val ch = c[k]
            if (url != null && ch != null) return url to ch
        }
        return null
    }
}

/** Decodes the single frame of a still stream straight onto a Surface (the video plane). */
class StillDecoder(private val main: Handler) {
    private var codec: MediaCodec? = null

    data class Stats(val extractMs: Long, val startMs: Long, val frameMs: Long)

    /** Blocking; call on a worker thread. [onScreen] fires on main once SurfaceFlinger shows it. */
    fun decode(file: File, surface: Surface, choice: StillCodec.Choice, onScreen: () -> Unit): Stats {
        release()
        val t0 = SystemClock.elapsedRealtime()
        val ex = MediaExtractor()
        try {
            ex.setDataSource(file.path)
            val ti = (0 until ex.trackCount).first {
                ex.getTrackFormat(it).getString(MediaFormat.KEY_MIME)!!.startsWith("video/")
            }
            ex.selectTrack(ti)
            val fmt = ex.getTrackFormat(ti)
            val t1 = SystemClock.elapsedRealtime()
            val c = MediaCodec.createByCodecName(choice.codecName)
            codec = c
            var fired = false
            c.setOnFrameRenderedListener({ _, _, _ -> if (!fired) { fired = true; onScreen() } }, main)
            c.configure(fmt, surface, null, 0)
            c.setVideoScalingMode(MediaCodec.VIDEO_SCALING_MODE_SCALE_TO_FIT)
            c.start()
            val t2 = SystemClock.elapsedRealtime()
            val info = MediaCodec.BufferInfo()
            var inputDone = false
            val deadline = t2 + 6000
            while (SystemClock.elapsedRealtime() < deadline) {
                if (!inputDone) {
                    val ii = c.dequeueInputBuffer(5000)
                    if (ii >= 0) {
                        val n = ex.readSampleData(c.getInputBuffer(ii)!!, 0)
                        if (n < 0) {
                            c.queueInputBuffer(ii, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            inputDone = true
                        } else {
                            c.queueInputBuffer(ii, 0, n, ex.sampleTime, 0)
                            ex.advance()
                        }
                    }
                }
                val oi = c.dequeueOutputBuffer(info, 5000)
                if (oi >= 0) {
                    val eos = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                    if (info.size > 0 || !eos) {
                        c.releaseOutputBuffer(oi, true)
                        val t3 = SystemClock.elapsedRealtime()
                        // Some emulator codecs never send the rendered callback; do not hang on it.
                        main.postDelayed({ if (!fired) { fired = true; onScreen() } }, 250)
                        return Stats(t1 - t0, t2 - t1, t3 - t2)
                    }
                    c.releaseOutputBuffer(oi, false)
                    throw IllegalStateException("stream ended without a frame")
                }
            }
            throw IllegalStateException("decode timed out")
        } finally {
            ex.release()
        }
    }

    /** Frees the decoder. The last frame stays on the surface until something else is queued. */
    fun release() {
        codec?.let { runCatching { it.stop() }; runCatching { it.release() } }
        codec = null
    }
}

/** A muted clip on a Surface, using the platform player (no ExoPlayer needed for local MP4). */
class ClipPlayer(private val loop: Boolean = false) {
    private var mp: MediaPlayer? = null
    var videoWidth = 0; private set
    var videoHeight = 0; private set
    /** Length in ms once prepared (0 before). */
    val positionMs: Int get() = runCatching { mp?.currentPosition ?: 0 }.getOrDefault(0).coerceAtLeast(0)
    val durationMs: Int get() = runCatching { mp?.duration ?: 0 }.getOrDefault(0).coerceAtLeast(0)

    fun play(surface: Surface, url: String, onFirstFrame: () -> Unit, onEnd: () -> Unit,
             onError: (Throwable) -> Unit, onSize: (Int, Int) -> Unit = { _, _ -> }) {
        stop()
        val p = MediaPlayer()
        mp = p
        try {
            p.setSurface(surface)
            p.setDataSource(url)
            p.setVolume(0f, 0f)
            p.isLooping = loop
            p.setVideoScalingMode(MediaPlayer.VIDEO_SCALING_MODE_SCALE_TO_FIT)
            p.setOnVideoSizeChangedListener { _, w, h -> if (w > 0 && h > 0) { videoWidth = w; videoHeight = h; onSize(w, h) } }
            p.setOnPreparedListener { it.start() }
            p.setOnInfoListener { _, what, _ ->
                if (what == MediaPlayer.MEDIA_INFO_VIDEO_RENDERING_START) onFirstFrame()
                false
            }
            p.setOnCompletionListener { if (!loop) onEnd() else Log.i(TAG, "clip stopped on its last frame at ${runCatching { it.currentPosition }.getOrDefault(-1)} of ${durationMs} ms") }
            p.setOnErrorListener { _, what, extra -> onError(IllegalStateException("MediaPlayer error $what/$extra")); true }
            p.prepareAsync()
        } catch (e: Exception) {
            onError(e)
        }
    }

    fun pause() { runCatching { mp?.pause() } }
    fun resume() { runCatching { mp?.start() } }

    /** Makes the play under way the last one: looping goes off, so the player stops on the clip's real last frame and
     *  holds it (pausing near the end instead was too late on the Streamer: its decoder is already into the next play
     *  while the position it reports is still at the end). [done] runs [leadMs] before the end, so the next slide gets
     *  ready and its fade starts over the last moments of motion; also when the player is gone or never gets there. */
    fun finishPlay(main: android.os.Handler, leadMs: Int, done: () -> Unit) {
        runCatching { mp?.isLooping = false }
        val dur = durationMs
        var last = -1
        val t0 = android.os.SystemClock.elapsedRealtime()
        val from = runCatching { mp?.currentPosition ?: -1 }.getOrDefault(-1)
        val give = android.os.SystemClock.elapsedRealtime() + dur + 2_000L
        val tick = object : Runnable {
            override fun run() {
                val p = mp
                val pos = runCatching { p?.currentPosition ?: -1 }.getOrDefault(-1)
                if (p == null || pos < 0 || dur <= 0 || pos >= dur - leadMs || !p.isPlaying || (last >= 0 && pos < last - 200) ||
                    android.os.SystemClock.elapsedRealtime() > give) {
                    Log.i(TAG, "clip finishing its play: from ${from} ms, moving on at ${pos} of ${dur} ms after ${android.os.SystemClock.elapsedRealtime() - t0} ms")
                    done(); return
                }
                last = pos
                main.postDelayed(this, 15)
            }
        }
        main.post(tick)
    }

    /** frames shown / dropped as counted by the platform player. */
    fun metrics(): String = runCatching {
        val b = mp?.metrics ?: return ""
        "frames=${b.getInt(MediaPlayer.MetricsConstants.FRAMES)} dropped=${b.getInt(MediaPlayer.MetricsConstants.FRAMES_DROPPED)} " +
            "codec=${b.getString(MediaPlayer.MetricsConstants.CODEC_VIDEO)} " +
            "size=${b.getInt(MediaPlayer.MetricsConstants.WIDTH)}x${b.getInt(MediaPlayer.MetricsConstants.HEIGHT)}"
    }.getOrDefault("")

    fun stop() {
        mp?.let { p ->
            runCatching { Log.i(TAG, "clip stop ${metrics()}") }
            runCatching { p.stop() }
            p.release()
        }
        mp = null
    }
}
