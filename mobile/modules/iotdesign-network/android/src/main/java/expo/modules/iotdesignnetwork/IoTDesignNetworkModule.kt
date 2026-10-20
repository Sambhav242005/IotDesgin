package expo.modules.iotdesignnetwork

import android.content.Context
import android.graphics.Bitmap
import android.view.View
import android.view.ViewGroup
import androidx.camera.view.PreviewView
import expo.modules.kotlin.functions.Queues
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSession
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

class IoTDesignNetworkModule : Module() {
  private var nsd: NsdManager? = null
  private var discovery: NsdManager.DiscoveryListener? = null
  private val resolving = mutableSetOf<String>()

  override fun definition() = ModuleDefinition {
    Name("IoTDesignNetwork")
    Events("onServiceFound", "onDiscoveryError")
    View(VideoCameraView::class) {
      Events("onVideoStatus")
      Prop("configuration") { view: VideoCameraView, value: Map<String, String>? ->
        view.reconfigure(value)
      }
    }
    Function("chooseFloor") { VideoCameraView.active?.chooseFloor() }
    AsyncFunction("stopVideo") { VideoCameraView.active?.stopAndWait() }

    Function("startDiscovery") {
      val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
      val manager = context.getSystemService(Context.NSD_SERVICE) as NsdManager
      if (discovery != null) return@Function null
      nsd = manager
      val listener = object : NsdManager.DiscoveryListener {
        override fun onDiscoveryStarted(serviceType: String) = Unit
        override fun onServiceFound(serviceInfo: NsdServiceInfo) {
          if (!serviceInfo.serviceType.contains("_iotdesign._tcp", ignoreCase = true)) return
          val key = "${serviceInfo.serviceName}:${serviceInfo.serviceType}"
          synchronized(resolving) { if (!resolving.add(key)) return }
          @Suppress("DEPRECATION")
          manager.resolveService(serviceInfo, object : NsdManager.ResolveListener {
            override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
              synchronized(resolving) { resolving.remove(key) }
            }
            override fun onServiceResolved(info: NsdServiceInfo) {
              synchronized(resolving) { resolving.remove(key) }
              val attributes = info.attributes
              val host = info.host?.hostAddress ?: return
              val serverId = attributes["server_id"]?.toString(Charsets.UTF_8) ?: return
              val apiVersion = attributes["api_version"]?.toString(Charsets.UTF_8) ?: ""
              val fingerprint = attributes["fingerprint"]?.toString(Charsets.UTF_8) ?: ""
              val name = attributes["name"]?.toString(Charsets.UTF_8) ?: info.serviceName
              sendEvent("onServiceFound", mapOf(
                "name" to name, "serverId" to serverId, "apiVersion" to apiVersion,
                "host" to host, "port" to info.port, "fingerprint" to fingerprint
              ))
            }
          })
        }
        override fun onServiceLost(serviceInfo: NsdServiceInfo) = Unit
        override fun onDiscoveryStopped(serviceType: String) = Unit
        override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
          sendEvent("onDiscoveryError", mapOf("message" to "Android service discovery failed ($errorCode)"))
          try { manager.stopServiceDiscovery(this) } catch (_: Exception) { }
          discovery = null
        }
        override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
          sendEvent("onDiscoveryError", mapOf("message" to "Could not stop service discovery ($errorCode)"))
        }
      }
      discovery = listener
      manager.discoverServices("_iotdesign._tcp.", NsdManager.PROTOCOL_DNS_SD, listener)
    }

    Function("stopDiscovery") {
      val listener = discovery ?: return@Function null
      try { nsd?.stopServiceDiscovery(listener) } catch (_: Exception) { }
      discovery = null
      synchronized(resolving) { resolving.clear() }
    }

    AsyncFunction("request") {
        address: String, method: String, fingerprint: String,
        headers: Map<String, String>, body: String?, filePath: String?, fields: Map<String, String> ->
      pinnedRequest(address, method, fingerprint, headers, body, filePath, fields)
    }

    // Read only the camera surface, never the activity or its pairing UI.
    // A single awaited JS loop owns this cache file until upload completes.
    AsyncFunction("capturePreview") {
      val root = appContext.currentActivity?.window?.decorView
        ?: throw IllegalStateException("Camera activity unavailable")
      val preview = findPreview(root)
        ?: throw IllegalStateException("Camera preview unavailable")
      if (preview.previewStreamState.value != PreviewView.StreamState.STREAMING) {
        throw IllegalStateException("Waiting for camera preview")
      }
      val bitmap = preview.bitmap ?: throw IllegalStateException("Waiting for camera preview")
      val scale = minOf(1.0, 640.0 / maxOf(bitmap.width, bitmap.height))
      val small = Bitmap.createScaledBitmap(bitmap,
        maxOf(1, (bitmap.width * scale).toInt()), maxOf(1, (bitmap.height * scale).toInt()), true)
      try {
        val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
        val file = File(context.cacheDir, "iotdesign-live.jpg")
        file.outputStream().use { small.compress(Bitmap.CompressFormat.JPEG, 60, it) }
        mapOf("uri" to file.toURI().toString(), "width" to small.width, "height" to small.height)
      } finally {
        if (small !== bitmap) small.recycle()
        bitmap.recycle()
      }
    }.runOnQueue(Queues.MAIN)
  }

  private fun findPreview(view: View): PreviewView? {
    if (view is PreviewView && view.isShown) return view
    if (view is ViewGroup) {
      for (index in 0 until view.childCount) {
        findPreview(view.getChildAt(index))?.let { return it }
      }
    }
    return null
  }

  private fun pinnedRequest(
    address: String, method: String, expectedFingerprint: String,
    headers: Map<String, String>, body: String?, filePath: String?, fields: Map<String, String>
  ): Map<String, Any> {
    require(address.startsWith("https://", ignoreCase = true)) { "HTTPS is required" }
    val trustManager = object : X509TrustManager {
      override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
      override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = Unit
      override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
        val actual = sha256(chain.first().encoded)
        if (expectedFingerprint.isNotBlank() && !actual.equals(expectedFingerprint, ignoreCase = true)) {
          throw CertificateException("Server certificate fingerprint changed")
        }
      }
    }
    val ssl = SSLContext.getInstance("TLS")
    ssl.init(null, arrayOf<TrustManager>(trustManager), SecureRandom())
    val connection = URL(address).openConnection() as HttpsURLConnection
    connection.sslSocketFactory = ssl.socketFactory
    connection.hostnameVerifier = HostnameVerifier { _: String, _: SSLSession -> true }
    connection.connectTimeout = 5000
    connection.readTimeout = 15000
    connection.requestMethod = method.uppercase()
    headers.forEach { (key, value) -> connection.setRequestProperty(key, value) }
    var boundary: String? = null
    if (filePath != null) {
      boundary = "iotdesign-${System.currentTimeMillis()}-${SecureRandom().nextInt()}"
      connection.doOutput = true
      connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
      BufferedOutputStream(connection.outputStream).use { out ->
        fields.forEach { (name, value) ->
          out.write("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n".toByteArray())
        }
        val file = File(URL(filePath).toURI())
        out.write("--$boundary\r\nContent-Disposition: form-data; name=\"image\"; filename=\"${file.name}\"\r\nContent-Type: image/jpeg\r\n\r\n".toByteArray())
        BufferedInputStream(file.inputStream()).use { input -> input.copyTo(out, 64 * 1024) }
        out.write("\r\n--$boundary--\r\n".toByteArray())
      }
    } else if (body != null) {
      connection.doOutput = true
      connection.setRequestProperty("Content-Type", "application/json")
      connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
    }
    val status = connection.responseCode
    val response = (if (status >= 400) connection.errorStream else connection.inputStream)
      ?.bufferedReader()?.use { it.readText() } ?: ""
    val peer = connection.serverCertificates.firstOrNull() as? X509Certificate
    val actual = peer?.encoded?.let { sha256(it) } ?: ""
    connection.disconnect()
    return mapOf("status" to status, "body" to response, "peerFingerprint" to actual)
  }

  private fun sha256(bytes: ByteArray) = MessageDigest.getInstance("SHA-256")
    .digest(bytes).joinToString("") { "%02x".format(it) }
}
