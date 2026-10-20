package expo.modules.iotdesignnetwork

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.SurfaceTexture
import android.hardware.camera2.*
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.os.Handler
import android.os.HandlerThread
import android.util.Range
import android.view.Surface
import android.view.TextureView
import android.view.MotionEvent
import expo.modules.kotlin.AppContext
import expo.modules.kotlin.viewevent.EventDispatcher
import expo.modules.kotlin.views.ExpoView
import org.json.JSONObject
import org.json.JSONArray
import java.io.ByteArrayOutputStream
import java.util.UUID
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.*

/** Camera2 sends the same continuous capture to preview and hardware H.264 encoder surfaces. */
class VideoCameraView(context: Context, appContext: AppContext) : ExpoView(context, appContext) {
  private val onVideoStatus by EventDispatcher()
  private val texture = TextureView(context)
  private var config: Map<String, String>? = null
  @Volatile private var sender: VideoSender? = null
  companion object { @Volatile var active: VideoCameraView? = null }

  init {
    addView(texture, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
    texture.setOnTouchListener { _, event ->
      if (sender?.isChoosingFloor() != true) false else {
        if (event.action == MotionEvent.ACTION_UP) sender?.tapFloor(event.x, event.y)
        true
      }
    }
    texture.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
      override fun onSurfaceTextureAvailable(surface: SurfaceTexture, width: Int, height: Int) = launch()
      override fun onSurfaceTextureSizeChanged(surface: SurfaceTexture, width: Int, height: Int) = Unit
      override fun onSurfaceTextureUpdated(surface: SurfaceTexture) = Unit
      override fun onSurfaceTextureDestroyed(surface: SurfaceTexture): Boolean { stop(); return true }
    }
  }

  private fun launch() {
    val value = config ?: return
    if (!texture.isAvailable || sender != null) return
    active = this
    val next = VideoSender(context, texture.surfaceTexture!!, texture.width, texture.height, texture.display?.rotation ?: 0, value) { state, detail ->
      post { onVideoStatus(mapOf("state" to state, "detail" to detail)) }
    }
    sender = next
    next.start()
  }

  fun chooseFloor() { sender?.chooseFloor() ?: throw IllegalStateException("Start video before setting the floor") }
  fun stop() { sender?.stop() }
  fun stopAndWait() {
    val old = sender ?: return
    old.stop()
    old.await()
    if (sender === old) sender = null
  }

  override fun onDetachedFromWindow() {
    config = null
    stop()
    if (active === this) active = null
    super.onDetachedFromWindow()
  }

  // Release before restarting on pause/resume; a camera cannot be opened twice.
  fun reconfigure(value: Map<String, String>?) {
    if (config == value) return
    val old = sender
    config = value
    old?.stop()
    Thread {
      try { old?.await() } catch (_: Exception) { }
      post {
        if (sender === old) sender = null
        launch()
      }
    }.start()
  }
}

private class VideoSender(
  private val context: Context, private val texture: SurfaceTexture,
  private val previewWidth: Int, private val previewHeight: Int, private val displayRotation: Int,
  private val config: Map<String, String>, private val status: (String, String) -> Unit
) {
  private val stopping = AtomicBoolean(false)
  private val cameraThread = HandlerThread("IoTDesignVideoCamera").apply { start() }
  private val handler = Handler(cameraThread.looper)
  @Volatile private var camera: CameraDevice? = null
  @Volatile private var session: CameraCaptureSession? = null
  @Volatile private var connection: HttpsURLConnection? = null
  @Volatile private var codec: MediaCodec? = null
  private var input: Surface? = null
  private var tracking: ArCameraTracking? = null
  private val videoSessionId = UUID.randomUUID().toString().replace("-", "")
  private var thread: Thread? = null
  @Volatile private var failure: String? = null

  fun chooseFloor() { tracking?.chooseFloor(); status("streaming", "Tap the floor in the camera view") }
  fun isChoosingFloor() = tracking?.choosingFloor == true
  fun tapFloor(x: Float, y: Float) { tracking?.tapFloor(x, y) }

  fun start() {
    thread = Thread({ transmit() }, "IoTDesignH264Upload").also { it.start() }
  }

  fun stop() {
    if (!stopping.compareAndSet(false, true)) return
    handler.post {
      try { session?.stopRepeating() } catch (_: Exception) { }
      session?.close(); camera?.close()
      try { codec?.signalEndOfInputStream() } catch (_: Exception) { }
    }
  }

  fun await() {
    thread?.join(12000)
    if (thread?.isAlive == true) {
      connection?.disconnect()
      thread?.join(3000)
    }
    check(thread?.isAlive != true) { "Video upload did not stop; keep the scan open and retry" }
    failure?.let { throw IllegalStateException(it) }
  }

  private fun fail(message: String) {
    failure = message
    status("error", message)
    stop()
  }

  @SuppressLint("MissingPermission")
  private fun openCamera() {
    if (stopping.get()) return
    try {
      val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
      val ar = tracking ?: throw IllegalStateException("ARCore unavailable")
      val shared = ar.session.sharedCamera
      val id = ar.session.cameraConfig.cameraId
      shared.setAppSurfaces(id, listOf(input!!))
      val callback = object : CameraDevice.StateCallback() {
        override fun onOpened(device: CameraDevice) {
          if (stopping.get()) { device.close(); return }
          camera = device
          // ARCore's internal reader surfaces require an opened camera device.
          val surfaces = shared.arCoreSurfaces.toMutableList().apply { add(input!!) }
          @Suppress("DEPRECATION")
          device.createCaptureSession(surfaces, shared.createARSessionStateCallback(object : CameraCaptureSession.StateCallback() {
            override fun onConfigured(value: CameraCaptureSession) {
              if (stopping.get()) { value.close(); return }
              session = value
              try {
                val request = device.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                  surfaces.forEach { addTarget(it) }
                  set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO)
                  set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, Range(30, 30))
                }.build()
                value.setRepeatingRequest(request, null, handler)
              } catch (e: Exception) { fail(e.message ?: "Video capture failed") }
            }
            override fun onActive(value: CameraCaptureSession) { if (!stopping.get()) ar.resume() }
            override fun onConfigureFailed(value: CameraCaptureSession) = fail("ARCore shared video session failed")
          }, handler), handler)
        }
        override fun onDisconnected(device: CameraDevice) { device.close(); fail("Camera disconnected") }
        override fun onError(device: CameraDevice, error: Int) { device.close(); fail("Camera error $error") }
      }
      manager.openCamera(id, shared.createARDeviceStateCallback(callback, handler), handler)
    } catch (e: Exception) { fail(e.message ?: "Cannot open camera") }
  }

  private fun sendTracking(video: HttpsURLConnection, record: JSONObject) {
    val address = config.getValue("address").removeSuffix("/video") + "/tracking"
    val conn = (URL(address).openConnection() as HttpsURLConnection).apply {
      sslSocketFactory = video.sslSocketFactory; hostnameVerifier = video.hostnameVerifier
      instanceFollowRedirects = false; connectTimeout = 5000; readTimeout = 5000
      requestMethod = "POST"; doOutput = true
      setRequestProperty("Content-Type", "application/json")
      setRequestProperty("Authorization", "Bearer ${config.getValue("token")}")
    }
    try {
      conn.outputStream.use { it.write(record.toString().toByteArray(Charsets.UTF_8)) }
      check(conn.responseCode in 200..299) { "PC rejected tracking metadata (${conn.responseCode})" }
      conn.inputStream.use { it.readBytes() }
    } finally { conn.disconnect() }
  }

  private fun transmit() {
    var bytes = 0L
    try {
      tracking = ArCameraTracking(context, texture, previewWidth, previewHeight, displayRotation)
      val address = config.getValue("address")
      val pin = config.getValue("fingerprint")
      require(address.startsWith("https://") && pin.matches(Regex("[a-fA-F0-9]{64}"))) {
        "A paired HTTPS server is required"
      }
      val trust = object : X509TrustManager {
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        override fun checkClientTrusted(chain: Array<X509Certificate>, auth: String) = Unit
        override fun checkServerTrusted(chain: Array<X509Certificate>, auth: String) {
          val actual = MessageDigest.getInstance("SHA-256").digest(chain.first().encoded)
            .joinToString("") { "%02x".format(it) }
          if (!actual.equals(pin, true)) throw CertificateException("Server certificate fingerprint changed")
        }
      }
      val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf<TrustManager>(trust), SecureRandom()) }
      val conn = (URL(address).openConnection() as HttpsURLConnection).apply {
        sslSocketFactory = ssl.socketFactory
        hostnameVerifier = HostnameVerifier { _, _ -> true }
        instanceFollowRedirects = false
        connectTimeout = 5000; readTimeout = 15000
        requestMethod = "POST"; doOutput = true
        setChunkedStreamingMode(16384)
        setRequestProperty("Content-Type", "video/h264")
        setRequestProperty("X-Video-Session", videoSessionId)
        setRequestProperty("X-Video-Rotation", tracking!!.imageRotationDegrees.toString())
        setRequestProperty("Authorization", "Bearer ${config.getValue("token")}")
      }
      connection = conn
      conn.outputStream.use { out ->
        val encoder = MediaCodec.createEncoderByType("video/avc")
        codec = encoder
        val format = MediaFormat.createVideoFormat("video/avc", 1280, 720).apply {
          setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
          setInteger(MediaFormat.KEY_BIT_RATE, 2500000)
          setInteger(MediaFormat.KEY_FRAME_RATE, 30)
          setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
          setInteger(MediaFormat.KEY_PROFILE, MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline)
        }
        encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        input = encoder.createInputSurface()
        encoder.start()
        handler.post { openCamera() }
        val info = MediaCodec.BufferInfo()
        var videoFrame = 0L
        var trackingBatch = JSONArray()
        val pendingVideo = ByteArrayOutputStream()
        fun flushBatch() {
          if (pendingVideo.size() == 0) return
          // A bounded six-frame bundle (about 200 ms at 30 fps) puts every pose
          // on the PC before its encoded picture can start a GPU job.
          if (trackingBatch.length() > 0) sendTracking(conn, JSONObject().put("records", trackingBatch))
          pendingVideo.writeTo(out); out.flush(); bytes += pendingVideo.size()
          pendingVideo.reset(); trackingBatch = JSONArray()
        }
        var lastStatus = System.currentTimeMillis()
        var lastStatusFrame = 0L
        var lastOutput = System.currentTimeMillis()
        while (true) {
          val index = encoder.dequeueOutputBuffer(info, 10000)
          if (index >= 0) {
            val buffer = encoder.getOutputBuffer(index)!!
            if (info.size > 0) {
              buffer.position(info.offset); buffer.limit(info.offset + info.size)
              val payload = ByteArray(info.size); buffer.get(payload)
              // MediaCodec AVC output includes Annex-B SPS/PPS codec-config and access units.
              if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0) {
                videoFrame++
                val record = tracking!!.measurement(info.presentationTimeUs * 1000, videoFrame)
                record.put("session_id", videoSessionId)
                trackingBatch.put(record)
              }
              pendingVideo.write(payload)
              if (trackingBatch.length() >= 6) flushBatch()
              lastOutput = System.currentTimeMillis()
              if (lastOutput - lastStatus >= 1000) {
                val fps = (videoFrame - lastStatusFrame) * 1000.0 / (lastOutput - lastStatus)
                lastStatusFrame = videoFrame
                status("streaming", "VIDEO to PC | ${"%.1f".format(fps)} fps | ${bytes / 1024} KiB | ARCore ${tracking?.state} | ${tracking?.floorStatus}")
                lastStatus = lastOutput
              }
            }
            val eos = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
            encoder.releaseOutputBuffer(index, false)
            if (eos) break
          }
          if (System.currentTimeMillis() - lastOutput > 10000) throw IllegalStateException("No encoded camera video")
          if (stopping.get() && System.currentTimeMillis() - lastOutput > 3000) break
        }
        flushBatch() // Preserve the final partial bundle before receiving EOF acknowledgment.
      }
      val code = conn.responseCode
      if (code !in 200..299) throw IllegalStateException("PC video receiver returned HTTP $code")
      status("stopped", "Video received by PC · ${bytes / 1024} KiB")
    } catch (e: Exception) {
      failure = e.message ?: "Video stream failed"
      status("error", failure!!)
    } finally {
      stopping.set(true)
      session?.close(); camera?.close()
      try { codec?.stop() } catch (_: Exception) { }
      codec?.release(); input?.release()
      try { tracking?.close() } catch (_: Exception) { }
      connection?.disconnect(); cameraThread.quitSafely()
    }
  }
}
