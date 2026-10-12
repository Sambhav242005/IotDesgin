Pod::Spec.new do |s|
  s.name           = 'IoTDesignNetwork'
  s.version        = '1.0.0'
  s.summary        = 'Local IoTDesign server discovery and pinned HTTPS transport'
  s.description    = s.summary
  s.license        = { :type => 'MIT' }
  s.author         = { 'IoTDesign' => 'local' }
  s.homepage       = 'https://localhost/iotdesign-network'
  s.platforms      = { :ios => '16.4' }
  s.swift_version  = '5.9'
  s.source         = { :git => 'https://localhost/iotdesign-network.git' }
  s.static_framework = true
  s.dependency 'ExpoModulesCore'
  s.source_files = '**/*.{h,m,swift}'
  s.pod_target_xcconfig = { 'DEFINES_MODULE' => 'YES' }
end
