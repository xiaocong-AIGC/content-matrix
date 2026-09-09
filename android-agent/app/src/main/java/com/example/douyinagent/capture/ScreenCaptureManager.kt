package com.example.douyinagent.capture

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Handler
import android.os.Looper
import android.util.DisplayMetrics
import android.view.WindowManager
import java.io.ByteArrayOutputStream

/**
 * Captures the screen via MediaProjection. Unlike the accessibility
 * takeScreenshot API (Android 11+), this works on Android 10 and below. The
 * user grants a one-time screen-record consent when starting the agent; the
 * projection then stays alive for the whole session.
 */
class ScreenCaptureManager(private val context: Context) {
    private val mainHandler = Handler(Looper.getMainLooper())
    private var projection: MediaProjection? = null
    private var imageReader: ImageReader? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var width = 0
    private var height = 0
    private var densityDpi = 0

    val isReady: Boolean
        get() = projection != null && imageReader != null

    fun start(resultCode: Int, data: Intent): Boolean {
        if (isReady) return true
        val manager = context.getSystemService(Context.MEDIA_PROJECTION_SERVICE)
            as MediaProjectionManager
        val proj = manager.getMediaProjection(resultCode, data) ?: return false

        val metrics = DisplayMetrics()
        val windowManager = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        @Suppress("DEPRECATION")
        windowManager.defaultDisplay.getRealMetrics(metrics)
        width = metrics.widthPixels
        height = metrics.heightPixels
        densityDpi = metrics.densityDpi

        val reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        proj.registerCallback(
            object : MediaProjection.Callback() {
                override fun onStop() = release()
            },
            mainHandler,
        )
        virtualDisplay = proj.createVirtualDisplay(
            "douyin-agent-capture",
            width,
            height,
            densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            reader.surface,
            null,
            null,
        )
        projection = proj
        imageReader = reader
        return true
    }

    fun capture(callback: (Result<ScreenshotData>) -> Unit) {
        val reader = imageReader
        if (reader == null || projection == null) {
            callback(Result.failure(IllegalStateException("录屏未授权或已停止")))
            return
        }
        // Give the virtual display a moment to render the current frame.
        mainHandler.postDelayed({
            try {
                val image = reader.acquireLatestImage()
                    ?: return@postDelayed callback(
                        Result.failure(IllegalStateException("暂无可用画面帧")),
                    )
                image.use { img ->
                    val plane = img.planes[0]
                    val buffer = plane.buffer
                    val pixelStride = plane.pixelStride
                    val rowStride = plane.rowStride
                    val rowPadding = rowStride - pixelStride * width
                    val paddedWidth = width + rowPadding / pixelStride
                    val padded = Bitmap.createBitmap(
                        paddedWidth,
                        height,
                        Bitmap.Config.ARGB_8888,
                    )
                    padded.copyPixelsFromBuffer(buffer)
                    val bitmap = Bitmap.createBitmap(padded, 0, 0, width, height)
                    padded.recycle()
                    val output = ByteArrayOutputStream()
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)
                    bitmap.recycle()
                    callback(
                        Result.success(
                            ScreenshotData(width, height, output.toByteArray()),
                        ),
                    )
                }
            } catch (error: Throwable) {
                callback(Result.failure(error))
            }
        }, FRAME_DELAY_MS)
    }

    fun release() {
        virtualDisplay?.release()
        virtualDisplay = null
        imageReader?.close()
        imageReader = null
        projection?.stop()
        projection = null
    }

    data class ScreenshotData(
        val width: Int,
        val height: Int,
        val png: ByteArray,
    )

    private companion object {
        const val FRAME_DELAY_MS = 350L
    }
}
