package com.ycj.aixdrpro

import android.content.Context
import android.graphics.*
import android.hardware.DataSpace
import android.hardware.HardwareBuffer
import android.media.*
import android.media.MediaCodecInfo
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import androidx.annotation.RequiresApi
import java.io.File
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * 将一系列 Ultra HDR 图像（HLG/BT.2020）编码成 HLG 视频（HEVC）。
 *
 * 说明：
 * - 需要设备支持 HardwareBuffer 与 HEVC 硬件编码（Main10）。
 * - 该类把渲染到 encoder input surface 的工作封装起来，提供 suspend 方法 encodeImagesToVideo
 *
 * 使用示例：
 * lifecycleScope.launch(Dispatchers.IO) {
 *     val encoder = UltraHDREncoder(requireContext())
 *     val out = encoder.encodeImagesToVideo(imagePaths, File(requireContext().cacheDir, "out.mp4"))
 * }
 */

@RequiresApi(Build.VERSION_CODES.UPSIDE_DOWN_CAKE)
class UltraHDREncoder(private val context: Context) {
    companion object {
        private val TAG = "UltraHDRToHDRVideo"

        private const val HARDWARE_BUFFER_USAGES = HardwareBuffer.USAGE_GPU_COLOR_OUTPUT or HardwareBuffer.USAGE_GPU_SAMPLED_IMAGE
        private const val HARDWARE_BUFFER_COLOR = HardwareBuffer.RGBA_1010102
        private const val FORMAT_MIMETYPE = MediaFormat.MIMETYPE_VIDEO_HEVC
        private const val FORMAT_COLOR_FORMAT = MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface
        private const val FORMAT_PROFILE = MediaCodecInfo.CodecProfileLevel.HEVCProfileMain10
        private const val FORMAT_BITRATE = 30 * 1000_000
        private const val FORMAT_FRAME_RATE = 30
        private const val FORMAT_I_FRAME_INTERVAL = -1
        private const val FORMAT_COLOR_STANDARD = MediaFormat.COLOR_STANDARD_BT2020
        private const val FORMAT_COLOR_RANGE = MediaFormat.COLOR_RANGE_LIMITED//COLOR_RANGE_FULL //COLOR_RANGE_LIMITED
        private const val FORMAT_COLOR_TRANSFER = MediaFormat.COLOR_TRANSFER_HLG
        
        // 默认尺寸，实际会根据输入图像动态调整
        private const val DEFAULT_WIDTH = 1080
        private const val DEFAULT_HEIGHT = 1920
    }
    
    // 动态配置的视频尺寸
    private var videoWidth = DEFAULT_WIDTH
    private var videoHeight = DEFAULT_HEIGHT
    private lateinit var imageWriter: ImageWriter
    private lateinit var imageWriterHandler: Handler
    private val imageWriterThread = HandlerThread("imageWriterThread")

    private lateinit var muxer: MediaMuxer
    private var muxerTrackId = -1

    private lateinit var encoder: MediaCodec
    private lateinit var encoderHandler: Handler
    private val encoderThread = HandlerThread("encoderThread")
    private lateinit var encoderSurface: Surface

    private var activeImageBuffers = AtomicInteger(0)
    private var isFrameBufferProcessed = AtomicBoolean(true)
    private var framesProcessed = AtomicInteger(0)

    fun encodeImagesToVideo(inputDir: File, outputFile: File) {
        require(inputDir.exists() && inputDir.isDirectory) {
            "输入路径必须是有效的文件夹"
        }
        Log.i(TAG, "Selected dir: ${inputDir.absolutePath}")
        
        // 按文件名排序读取 JPG/JPEG/PNG 图像
        val imageFiles = inputDir.listFiles { f ->
            f.isFile && (
                f.name.lowercase().endsWith(".jpg") ||
                f.name.lowercase().endsWith(".jpeg") ||
                f.name.lowercase().endsWith(".png")
            )
        }?.sortedBy { it.name }
            ?: throw IllegalArgumentException("文件夹内没有支持的图像文件")

        if (imageFiles.isEmpty()) {
            throw IllegalArgumentException("文件夹内没有支持的图像文件")
        }
        
        Log.i(TAG, "找到 ${imageFiles.size} 个图像文件")
        imageFiles.forEach { Log.i(TAG, "  - ${it.name}") }
        
        // 检测图像尺寸 - 从第一张有效图像获取
        detectVideoDimensions(imageFiles)
        Log.i(TAG, "检测到图像尺寸: ${videoWidth}x${videoHeight}")

        if (!isHardwareAccelerationSupported()) {
            throw RuntimeException("当前设备不支持 HardwareBuffer HDR 编码")
        }

        setUpMediaCodec(outputFile)
        setUpImageWriter()

        // 依次把所有图片渲染到编码器
        for (imageFile in imageFiles) {
            try {
                val bitmap = BitmapFactory.decodeFile(imageFile.absolutePath)
                if (bitmap == null) {
                    Log.e(TAG, "无法解码图像: ${imageFile.name}")
                    continue
                }
                
                // 确保图像尺寸与检测到的尺寸匹配
                val resizedBitmap = if (bitmap.width == videoWidth && bitmap.height == videoHeight) {
                    bitmap
                } else {
                    Bitmap.createScaledBitmap(bitmap, videoWidth, videoHeight, true)
                }
                
                // 每张图像只生成1帧，这样77张图像在30fps下会生成约2.57秒的视频
                // 等待帧缓冲区可用
                while (true) {
                    val isFrameProcessed = isFrameBufferProcessed.get()
                    val isOverMaxImagesLimit = activeImageBuffers.get() >= imageWriter.maxImages
                    if (isFrameProcessed && !isOverMaxImagesLimit) {
                        break
                    }
                    Thread.sleep(10) // 短暂等待，避免忙循环占用过多CPU
                }
                // 3. 获取缓冲区：从 ImageWriter 申请一个空的硬件缓冲区 (HardwareBuffer)
                val image = imageWriter.dequeueInputImage()
                updateActiveImageBuffers(false)
                // 4. 硬件渲染：重点！
                renderBitmapToImageViaHardware(resizedBitmap, image, ColorSpace.get(ColorSpace.Named.BT2020_HLG))

                // 等待渲染完成
                while (!image.fence.isValid) {
                    Thread.sleep(1)
                }
                image.fence.awaitForever()
                
                // 将图像提交到编码器
                imageWriter.queueInputImage(image)
                
                // 释放Bitmap内存
                if (resizedBitmap != bitmap) {
                    bitmap.recycle()
                }
                resizedBitmap.recycle()
            } catch (e: Exception) {
                Log.e(TAG, "处理图像时出错: ${imageFile.name}", e)
            }
        }

        encoder.signalEndOfInputStream()

        // 等待编码结束
        synchronized(this) {
            (this as java.lang.Object).wait()
        }
    }
    
    /**
     * 从第一张有效图像检测视频尺寸
     */
    private fun detectVideoDimensions(imageFiles: List<File>) {
        for (imageFile in imageFiles) {
            try {
                val options = BitmapFactory.Options()
                options.inJustDecodeBounds = true // 只解码尺寸信息，不加载像素
                BitmapFactory.decodeFile(imageFile.absolutePath, options)
                
                if (options.outWidth > 0 && options.outHeight > 0) {
                    videoWidth = options.outWidth
                    videoHeight = options.outHeight
                    Log.i(TAG, "从图像 ${imageFile.name} 检测到尺寸: ${videoWidth}x${videoHeight}")
                    return
                }
            } catch (e: Exception) {
                Log.e(TAG, "检测图像尺寸时出错: ${imageFile.name}", e)
            }
        }
        Log.w(TAG, "无法检测图像尺寸，使用默认值: ${DEFAULT_WIDTH}x${DEFAULT_HEIGHT}")
        videoWidth = DEFAULT_WIDTH
        videoHeight = DEFAULT_HEIGHT
    }

    private fun isHardwareAccelerationSupported(): Boolean = HardwareBuffer.isSupported(
        videoWidth,
        videoHeight,
        HARDWARE_BUFFER_COLOR,
        1,
        HARDWARE_BUFFER_USAGES
    )

    private fun setUpImageWriter() {
        imageWriter = ImageWriter.Builder(encoderSurface)
            .setHardwareBufferFormat(HARDWARE_BUFFER_COLOR)
            .setDataSpace(DataSpace.DATASPACE_BT2020_HLG)
            .setMaxImages(32)
            .setUsage(HARDWARE_BUFFER_USAGES)
            .build()

        imageWriterThread.start()
        imageWriterHandler = Handler(imageWriterThread.looper)
        imageWriter.setOnImageReleasedListener(
            { updateActiveImageBuffers(decrement = true) },
            Handler.createAsync(imageWriterThread.looper)
        )
    }

    private fun setUpMediaCodec(outputFile: File) {
        val format = createHdrMediaFormat()
        val codecList = MediaCodecList(MediaCodecList.ALL_CODECS)
        val encoderName = codecList.findEncoderForFormat(format)
            ?: throw RuntimeException("找不到支持的 HDR 编码器")

        encoderThread.start()
        encoderHandler = Handler(encoderThread.looper)

        encoder = MediaCodec.createByCodecName(encoderName)
        encoder.setCallback(setUpMediaCodecCallback(outputFile), encoderHandler)
        encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        encoderSurface = encoder.createInputSurface()
        encoder.start()
    }

    private fun setUpMediaCodecCallback(outputFile: File) = object : MediaCodec.Callback() {
        override fun onInputBufferAvailable(c: MediaCodec, i: Int) { }

        override fun onOutputBufferAvailable(c: MediaCodec, i: Int, info: MediaCodec.BufferInfo) {
            if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) {
                handleCodecConfig(c.getOutputFormat(i), outputFile)
                return
            }
            if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) {
                handleEndOfStream()
                return
            }
            handleFrame(c, i, info)
        }

        override fun onError(c: MediaCodec, e: MediaCodec.CodecException) {
            Log.e(TAG, "Codec error: ${e.message}")
            handleEndOfStream()
        }

        override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) { }
    }

    private fun handleCodecConfig(mediaFormat: MediaFormat, outputFile: File) {
        if (outputFile.exists()) outputFile.delete()
        outputFile.parentFile?.mkdirs()

        muxer = MediaMuxer(outputFile.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        muxerTrackId = muxer.addTrack(mediaFormat)
        muxer.start()
    }

    private fun handleFrame(codec: MediaCodec, index: Int, info: MediaCodec.BufferInfo) {
        try {
            isFrameBufferProcessed.set(false)
            codec.getOutputBuffer(index)?.let { buf ->
                muxer.writeSampleData(muxerTrackId, buf, info)
            }
            codec.releaseOutputBuffer(index, false)
        } catch (e: Exception) {
            Log.e(TAG, "Error processing frame: ${e.message}")
        } finally {
            isFrameBufferProcessed.set(true)
            updateFramesProcessed()
        }
    }

    private fun handleEndOfStream() {
        try { muxer.stop() } catch (_: Exception) { }
        try { muxer.release() } catch (_: Exception) { }
        try { encoder.release() } catch (_: Exception) { }
        try { imageWriter.close() } catch (_: Exception) { }

        synchronized(this) {
            (this as java.lang.Object).notifyAll()
        }
    }

    private fun createHdrMediaFormat(): MediaFormat = MediaFormat.createVideoFormat(FORMAT_MIMETYPE, videoWidth, videoHeight).apply {
        setInteger(MediaFormat.KEY_PROFILE, FORMAT_PROFILE)
        setInteger(MediaFormat.KEY_BIT_RATE, FORMAT_BITRATE)
        setInteger(MediaFormat.KEY_FRAME_RATE, FORMAT_FRAME_RATE)
        setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, FORMAT_I_FRAME_INTERVAL)
        setInteger(MediaFormat.KEY_MAX_FPS_TO_ENCODER, FORMAT_FRAME_RATE)
        setInteger(MediaFormat.KEY_COLOR_FORMAT, FORMAT_COLOR_FORMAT)
        setInteger(MediaFormat.KEY_COLOR_STANDARD, FORMAT_COLOR_STANDARD)
        setInteger(MediaFormat.KEY_COLOR_RANGE, FORMAT_COLOR_RANGE)
        setInteger(MediaFormat.KEY_COLOR_TRANSFER, FORMAT_COLOR_TRANSFER)
    }

    private fun renderBitmapToImageViaHardware(bitmap: Bitmap, image: Image, dest: ColorSpace) {
        image.hardwareBuffer?.let { buffer ->
            val renderer = HardwareBufferRenderer(buffer)
            val node = RenderNode("ultra-hdr-to-video")
            node.setPosition(0, 0, image.width, image.height)
            val canvas = node.beginRecording()
            //防止对比度过高、暗部死黑问题，64-940
            val paint = Paint().apply {
                // 缩放系数：保持在 0.856 左右，将 [0, 1] 压缩到 [0.06, 0.92]
                val scale = 0.856f
                // 偏移量：在 255 体系下，64/1024 * 255 ≈ 16f
                val offset = 16f

                val matrix = ColorMatrix(floatArrayOf(
                    scale, 0f,    0f,    0f, offset,
                    0f,    scale, 0f,    0f, offset,
                    0f,    0f,    scale, 0f, offset,
                    0f,    0f,    0f,    1f, 0f
                ))
                colorFilter = ColorMatrixColorFilter(matrix)
                isFilterBitmap = true
            }
            canvas.drawBitmap(bitmap, 0f, 0f, paint)
            node.endRecording()
            renderer.setContentRoot(node)
            renderer.obtainRenderRequest().setColorSpace(dest).draw(
                { exec -> exec.run() },
                { result -> image.fence = result.fence }
            )
        }
    }

    private fun updateFramesProcessed() {
        val processed = framesProcessed.incrementAndGet()
        Log.i(TAG, "Frames Processed: $processed")
    }

    private fun updateActiveImageBuffers(decrement: Boolean = false) {
        if (decrement) activeImageBuffers.decrementAndGet()
        else activeImageBuffers.incrementAndGet()
    }


}
