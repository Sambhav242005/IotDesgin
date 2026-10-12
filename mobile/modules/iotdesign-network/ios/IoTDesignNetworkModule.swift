import CryptoKit
import ExpoModulesCore
import Foundation
import Security

private final class PinnedURLSessionDelegate: NSObject, URLSessionDelegate {
  let expectedFingerprint: String
  private(set) var peerFingerprint = ""

  init(expectedFingerprint: String) {
    self.expectedFingerprint = expectedFingerprint.lowercased()
  }

  func urlSession(
    _ session: URLSession,
    didReceive challenge: URLAuthenticationChallenge,
    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
  ) {
    guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
          let trust = challenge.protectionSpace.serverTrust,
          let certificate = SecTrustGetCertificateAtIndex(trust, 0) else {
      completionHandler(.cancelAuthenticationChallenge, nil)
      return
    }
    let data = SecCertificateCopyData(certificate) as Data
    let fingerprint = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    peerFingerprint = fingerprint
    if expectedFingerprint.isEmpty || fingerprint == expectedFingerprint {
      completionHandler(.useCredential, URLCredential(trust: trust))
    } else {
      completionHandler(.cancelAuthenticationChallenge, nil)
    }
  }
}

public final class IoTDesignNetworkModule: Module, NetServiceBrowserDelegate, NetServiceDelegate {
  private var browser: NetServiceBrowser?
  private var services: [String: NetService] = [:]

  public func definition() -> ModuleDefinition {
    Name("IoTDesignNetwork")
    Events("onServiceFound", "onDiscoveryError")

    Function("startDiscovery") {
      guard self.browser == nil else { return }
      let browser = NetServiceBrowser()
      browser.delegate = self
      self.browser = browser
      browser.searchForServices(ofType: "_iotdesign._tcp.", inDomain: "local.")
    }

    Function("stopDiscovery") {
      self.browser?.stop()
      self.browser = nil
      self.services.values.forEach { $0.stop() }
      self.services.removeAll()
    }

    AsyncFunction("request") { (
      address: String,
      method: String,
      fingerprint: String,
      headers: [String: String],
      body: String?,
      filePath: String?,
      fields: [String: String]
    ) async throws -> [String: Any] in
      guard address.lowercased().hasPrefix("https://"), let url = URL(string: address) else {
        throw NSError(domain: "IoTDesignNetwork", code: 1, userInfo: [NSLocalizedDescriptionKey: "HTTPS is required"])
      }
      let delegate = PinnedURLSessionDelegate(expectedFingerprint: fingerprint)
      let session = URLSession(configuration: .ephemeral, delegate: delegate, delegateQueue: nil)
      defer { session.invalidateAndCancel() }
      var request = URLRequest(url: url, timeoutInterval: 15)
      request.httpMethod = method.uppercased()
      headers.forEach { request.setValue($1, forHTTPHeaderField: $0) }
      if let filePath {
        let boundary = "iotdesign-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var data = Data()
        for (name, value) in fields {
          data.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".utf8))
        }
        let fileURL = URL(string: filePath) ?? URL(fileURLWithPath: filePath)
        let filename = fileURL.lastPathComponent
        data.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"image\"; filename=\"\(filename)\"\r\nContent-Type: image/jpeg\r\n\r\n".utf8))
        data.append(try Data(contentsOf: fileURL))
        data.append(Data("\r\n--\(boundary)--\r\n".utf8))
        request.httpBody = data
      } else if let body {
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data(body.utf8)
      }
      let (responseData, response) = try await session.data(for: request)
      guard let http = response as? HTTPURLResponse else {
        throw NSError(domain: "IoTDesignNetwork", code: 2, userInfo: [NSLocalizedDescriptionKey: "Invalid server response"])
      }
      return [
        "status": http.statusCode,
        "body": String(data: responseData, encoding: .utf8) ?? "",
        "peerFingerprint": delegate.peerFingerprint
      ]
    }
  }

  public func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
    let key = "\(service.name).\(service.type)"
    guard services[key] == nil else { return }
    services[key] = service
    service.delegate = self
    service.resolve(withTimeout: 5)
  }

  public func netServiceBrowser(_ browser: NetServiceBrowser, didNotSearch errorDict: [String: NSNumber]) {
    sendEvent("onDiscoveryError", ["message": "Bonjour discovery failed: \(errorDict)"])
  }

  public func netServiceDidResolveAddress(_ sender: NetService) {
    let txt = NetService.dictionary(fromTXTRecord: sender.txtRecordData() ?? Data())
    func value(_ key: String) -> String {
      guard let data = txt[key]?.first else { return "" }
      return String(data: data, encoding: .utf8) ?? ""
    }
    guard !sender.hostName.isEmpty, !value("server_id").isEmpty else { return }
    sendEvent("onServiceFound", [
      "name": value("name").isEmpty ? sender.name : value("name"),
      "serverId": value("server_id"), "apiVersion": value("api_version"),
      "host": sender.hostName.trimmingCharacters(in: CharacterSet(charactersIn: ".")),
      "port": sender.port, "fingerprint": value("fingerprint")
    ])
  }

  public func netService(_ sender: NetService, didNotResolve errorDict: [String: NSNumber]) {
    sendEvent("onDiscoveryError", ["message": "Could not resolve PC server: \(errorDict)"])
  }
}
