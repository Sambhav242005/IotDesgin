package expo.modules.iotdesignnetwork

import android.content.Context
import android.graphics.SurfaceTexture
import android.hardware.*
import android.opengl.*
import android.os.SystemClock
import android.os.Handler
import android.os.HandlerThread
import android.view.Surface
import com.google.ar.core.Anchor
import com.google.ar.core.Plane
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraCharacteristics
import com.google.ar.core.Config
import com.google.ar.core.Session
import com.google.ar.core.TrackingState
import org.json.JSONArray
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.EnumSet
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Shared-camera visual-inertial tracking. All ARCore/GL work stays on one GL thread. */
internal class ArCameraTracking(
  context: Context, private val previewTexture: SurfaceTexture,
  private val width: Int, private val height: Int, private val rotation: Int
) : SensorEventListener {
  val session = Session(context, EnumSet.of(Session.Feature.SHARED_CAMERA))
  private val worker = HandlerThread("IoTDesignARCoreGL").apply { start() }
  private val handler = Handler(worker.looper)
  private val sensors = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
  private val samples = ArrayDeque<JSONObject>()
  private val imu = ArrayDeque<JSONObject>()
  @Volatile private var running = false
  @Volatile var state: String = "INITIALIZING"; private set
  private var lastTimestamp = 0L
  private var floorAnchor: Anchor? = null
  private var floorTap: Pair<Float, Float>? = null
  @Volatile var choosingFloor = false; private set
  @Volatile var floorStatus = "Floor not set"; private set
  private var floorProgram = 0
  private val sensorUsesRealtime: Boolean = run {
    val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    manager.getCameraCharacteristics(session.cameraConfig.cameraId)
      .get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE) == CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE_REALTIME
  }
  val imageRotationDegrees: Int = run {
    val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    val sensor = manager.getCameraCharacteristics(session.cameraConfig.cameraId)
      .get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
    (sensor - rotation * 90 + 360) % 360
  }

  fun chooseFloor() { choosingFloor = true; floorStatus = "Tap the floor in the camera view" }
  fun tapFloor(x: Float, y: Float) {
    if (choosingFloor) handler.post { floorTap = x to y }
  }
  private var display = EGL14.EGL_NO_DISPLAY
  private var eglContext = EGL14.EGL_NO_CONTEXT
  private var surface = EGL14.EGL_NO_SURFACE
  private var previewSurface: Surface? = null
  private var textureId = 0
  private var program = 0
  private val vertices = floats(floatArrayOf(-1f, -1f, 1f, -1f, -1f, 1f, 1f, 1f))
  private val uv = floats(FloatArray(8))

  init {
    session.configure(Config(session).apply {
      planeFindingMode = Config.PlaneFindingMode.HORIZONTAL
      focusMode = Config.FocusMode.AUTO
      updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
    })
    val ready = CountDownLatch(1)
    var failure: Throwable? = null
    handler.post {
      try { initializeGl() } catch (error: Throwable) { failure = error }
      finally { ready.countDown() }
    }
    check(ready.await(8, TimeUnit.SECONDS)) { "ARCore GL initialization timed out" }
    failure?.let {
      try { close() } catch (_: Exception) { }
      throw IllegalStateException("ARCore preview initialization failed", it)
    }
    for (type in listOf(Sensor.TYPE_ROTATION_VECTOR, Sensor.TYPE_GYROSCOPE,
        Sensor.TYPE_ACCELEROMETER, Sensor.TYPE_LINEAR_ACCELERATION)) {
      sensors.getDefaultSensor(type)?.let { sensors.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME, handler) }
    }
  }

  fun resume() {
    handler.post {
      if (running) return@post
      try { session.resume(); running = true; update() }
      catch (error: Exception) { state = "ERROR: ${error.message}" }
    }
  }

  private fun update() {
    if (!running) return
    try {
      EGL14.eglMakeCurrent(display, surface, surface, eglContext)
      session.setDisplayGeometry(rotation, width.coerceAtLeast(1), height.coerceAtLeast(1))
      val frame = session.update()
      if (frame.timestamp != 0L && frame.timestamp != lastTimestamp) {
        lastTimestamp = frame.timestamp
        val camera = frame.camera
        state = camera.trackingState.name
        floorTap?.let { tap ->
          floorTap = null
          if (camera.trackingState == TrackingState.TRACKING) {
            val hit = frame.hitTest(tap.first, tap.second).firstOrNull { result ->
              val plane = result.trackable as? Plane
              plane != null && plane.trackingState == TrackingState.TRACKING &&
                plane.type == Plane.Type.HORIZONTAL_UPWARD_FACING && plane.isPoseInPolygon(result.hitPose)
            }
            if (hit != null) {
              floorAnchor?.detach(); floorAnchor = hit.createAnchor(); choosingFloor = false
              floorStatus = "Floor set on phone ? estimated grid"
            } else floorStatus = "No tracked floor here. Move slowly over the floor, then tap again"
          } else floorStatus = "Wait for ARCore TRACKING, then tap the floor"
        }
        val pose = camera.pose
        val matrix = FloatArray(16); pose.toMatrix(matrix, 0)
        val sample = JSONObject().put("ar_timestamp_ns", frame.timestamp).put("tracking_state", state)
          .put("camera_timestamp_ns", frame.androidCameraTimestamp)
          .put("tracking_failure", camera.trackingFailureReason.name)
        if (camera.trackingState == TrackingState.TRACKING) {
          sample.put("camera_to_world", JSONArray(matrix.map { it.toDouble() }))
          sample.put("translation", JSONArray(pose.translation.map { it.toDouble() }))
          sample.put("quaternion_xyzw", JSONArray(pose.rotationQuaternion.map { it.toDouble() }))
        }
        floorAnchor?.let { anchor ->
          if (anchor.trackingState == TrackingState.TRACKING) {
            sample.put("floor", JSONObject()
              .put("origin", JSONArray(anchor.pose.translation.map { it.toDouble() }))
              .put("normal", JSONArray(anchor.pose.getTransformedAxis(1, 1f).map { it.toDouble() })))
          }
        }
        synchronized(samples) {
          samples.addLast(sample)
          while (samples.size > 120) samples.removeFirst()
        }
        vertices.position(0); uv.position(0)
        frame.transformCoordinates2d(com.google.ar.core.Coordinates2d.OPENGL_NORMALIZED_DEVICE_COORDINATES,
          vertices, com.google.ar.core.Coordinates2d.TEXTURE_NORMALIZED, uv)
        draw()
        drawFloor(camera)
        EGL14.eglSwapBuffers(display, surface)
      }
    } catch (error: Exception) { state = "ERROR: ${error.message}" }
    if (running) handler.postDelayed({ update() }, 16)
  }

  fun measurement(timestampNs: Long, index: Long): JSONObject {
    // Android's default video-encoder output uses uptime, while REALTIME
    // sensor timestamps use elapsedRealtime. Preserve both; never use arrival
    // wall time or an arbitrary fitted offset to manufacture a match.
    val before = SystemClock.elapsedRealtimeNanos()
    val uptime = SystemClock.uptimeMillis() * 1_000_000L
    val after = SystemClock.elapsedRealtimeNanos()
    val uptimeOffset = (before + (after - before) / 2) - uptime
    val converted = if (sensorUsesRealtime) timestampNs + uptimeOffset else timestampNs
    val latest = synchronized(samples) { samples.lastOrNull()?.optLong("camera_timestamp_ns") }
    val normalize = sensorUsesRealtime && latest != null &&
      kotlin.math.abs(converted - latest) < kotlin.math.abs(timestampNs - latest)
    val matchedTime = if (normalize) converted else timestampNs
    val closest = synchronized(samples) {
      samples.minByOrNull { kotlin.math.abs(it.getLong("camera_timestamp_ns") - matchedTime) }
        ?.let { JSONObject(it.toString()) }
    }
    val result = closest ?: JSONObject().put("tracking_state", "INITIALIZING")
    result.put("video_frame", index).put("timestamp_ns", matchedTime)
      .put("encoder_timestamp_ns", timestampNs)
      .put("encoder_uptime_offset_ns", uptimeOffset)
      .put("sensor_timestamp_source", if (sensorUsesRealtime) "REALTIME" else "UNKNOWN")
      .put("timestamp_clock", if (normalize) "camera_realtime_from_encoder_uptime" else "android_camera")
    val events = JSONArray()
    synchronized(imu) { while (imu.isNotEmpty()) events.put(imu.removeFirst()) }
    result.put("imu", events)
    return result
  }

  override fun onSensorChanged(event: SensorEvent) {
    val sample = JSONObject().put("sensor_type", event.sensor.type).put("timestamp_ns", event.timestamp)
      .put("accuracy", event.accuracy).put("values", JSONArray(event.values.take(5).map { it.toDouble() }))
    synchronized(imu) { imu.addLast(sample); while (imu.size > 512) imu.removeFirst() }
  }
  override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

  fun close() {
    sensors.unregisterListener(this)
    val done = CountDownLatch(1)
    handler.post {
      try {
        running = false
        try { session.pause() } catch (_: Exception) { }
        floorAnchor?.detach(); floorAnchor = null
        session.close()
        if (display != EGL14.EGL_NO_DISPLAY) {
          EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT)
          EGL14.eglDestroySurface(display, surface); EGL14.eglDestroyContext(display, eglContext)
          EGL14.eglTerminate(display)
        }
        previewSurface?.release()
      } finally { done.countDown(); worker.quitSafely() }
    }
    check(done.await(8, TimeUnit.SECONDS)) { "ARCore shutdown timed out" }
  }

  private fun initializeGl() {
    display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
    check(EGL14.eglInitialize(display, IntArray(2), 0, IntArray(2), 1))
    val configs = arrayOfNulls<android.opengl.EGLConfig>(1)
    val attrs = intArrayOf(EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
      EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8,
      EGL14.EGL_SURFACE_TYPE, EGL14.EGL_WINDOW_BIT, EGL14.EGL_NONE)
    check(EGL14.eglChooseConfig(display, attrs, 0, configs, 0, 1, IntArray(1), 0))
    eglContext = EGL14.eglCreateContext(display, configs[0], EGL14.EGL_NO_CONTEXT,
      intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE), 0)
    previewSurface = Surface(previewTexture)
    surface = EGL14.eglCreateWindowSurface(display, configs[0], previewSurface,
      intArrayOf(EGL14.EGL_NONE), 0)
    check(EGL14.eglMakeCurrent(display, surface, surface, eglContext))
    val textures = IntArray(1); GLES20.glGenTextures(1, textures, 0); textureId = textures[0]
    GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, textureId)
    GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
    GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
    GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
    GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
    session.setCameraTextureName(textureId)
    fun shader(type: Int, source: String): Int = GLES20.glCreateShader(type).also {
      GLES20.glShaderSource(it, source); GLES20.glCompileShader(it)
    }
    program = GLES20.glCreateProgram()
    GLES20.glAttachShader(program, shader(GLES20.GL_VERTEX_SHADER,
      "attribute vec2 p; attribute vec2 t; varying vec2 uv; void main(){gl_Position=vec4(p,0.,1.);uv=t;}"))
    GLES20.glAttachShader(program, shader(GLES20.GL_FRAGMENT_SHADER,
      "#extension GL_OES_EGL_image_external : require\nprecision mediump float; uniform samplerExternalOES tex; varying vec2 uv; void main(){gl_FragColor=texture2D(tex,uv);}"))
    GLES20.glLinkProgram(program)
    floorProgram = GLES20.glCreateProgram()
    GLES20.glAttachShader(floorProgram, shader(GLES20.GL_VERTEX_SHADER,
      "attribute vec3 p; uniform mat4 mvp; void main(){gl_Position=mvp*vec4(p,1.);}"))
    GLES20.glAttachShader(floorProgram, shader(GLES20.GL_FRAGMENT_SHADER,
      "precision mediump float; void main(){gl_FragColor=vec4(.82,.93,.58,1.);}"))
    GLES20.glLinkProgram(floorProgram)
  }

  private fun drawFloor(camera: com.google.ar.core.Camera) {
    val anchor = floorAnchor ?: return
    if (anchor.trackingState != TrackingState.TRACKING || camera.trackingState != TrackingState.TRACKING) return
    val projection = FloatArray(16); val view = FloatArray(16); val model = FloatArray(16)
    val modelView = FloatArray(16); val mvp = FloatArray(16)
    camera.getProjectionMatrix(projection, 0, .05f, 100f); camera.getViewMatrix(view, 0)
    anchor.pose.toMatrix(model, 0)
    Matrix.multiplyMM(modelView, 0, view, 0, model, 0); Matrix.multiplyMM(mvp, 0, projection, 0, modelView, 0)
    val lines = ArrayList<Float>()
    for (i in -4..4) {
      val d = i * .25f
      lines.addAll(listOf(-1f, .002f, d, 1f, .002f, d, d, .002f, -1f, d, .002f, 1f))
    }
    val positions = floats(lines.toFloatArray())
    GLES20.glUseProgram(floorProgram); GLES20.glDisable(GLES20.GL_DEPTH_TEST)
    val attribute = GLES20.glGetAttribLocation(floorProgram, "p")
    GLES20.glVertexAttribPointer(attribute, 3, GLES20.GL_FLOAT, false, 0, positions)
    GLES20.glEnableVertexAttribArray(attribute)
    GLES20.glUniformMatrix4fv(GLES20.glGetUniformLocation(floorProgram, "mvp"), 1, false, mvp, 0)
    GLES20.glLineWidth(2f); GLES20.glDrawArrays(GLES20.GL_LINES, 0, lines.size / 3)
  }

  private fun draw() {
    GLES20.glViewport(0, 0, width, height); GLES20.glUseProgram(program)
    val p = GLES20.glGetAttribLocation(program, "p"); val t = GLES20.glGetAttribLocation(program, "t")
    vertices.position(0); uv.position(0)
    GLES20.glVertexAttribPointer(p, 2, GLES20.GL_FLOAT, false, 0, vertices)
    GLES20.glVertexAttribPointer(t, 2, GLES20.GL_FLOAT, false, 0, uv)
    GLES20.glEnableVertexAttribArray(p); GLES20.glEnableVertexAttribArray(t)
    GLES20.glActiveTexture(GLES20.GL_TEXTURE0); GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, textureId)
    GLES20.glUniform1i(GLES20.glGetUniformLocation(program, "tex"), 0)
    GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
  }

  companion object {
    private fun floats(values: FloatArray) = ByteBuffer.allocateDirect(values.size * 4)
      .order(ByteOrder.nativeOrder()).asFloatBuffer().apply { put(values); position(0) }
  }
}
