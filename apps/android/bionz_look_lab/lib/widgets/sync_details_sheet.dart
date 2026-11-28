import 'package:flutter/material.dart';

import '../models/sync_models.dart';
import '../services/sync_bridge.dart';

class SyncDetailsSheet extends StatelessWidget {
  const SyncDetailsSheet({
    super.key,
    required this.bridge,
    required this.snapshot,
    required this.batteryExempt,
    required this.onBatteryRefresh,
  });

  final SyncBridge bridge;
  final SyncSnapshot snapshot;
  final bool batteryExempt;
  final VoidCallback onBatteryRefresh;

  static const _accent = Color(0xFFFF6D00);

  @override
  Widget build(BuildContext context) {
    final bool isConnected = snapshot.connectionState == 'Connected';
    final statusColor = isConnected
        ? const Color(0xFF22C55E)
        : (snapshot.serviceRunning ? const Color(0xFFF59E0B) : Colors.white54);

    return DraggableScrollableSheet(
      initialChildSize: 0.72,
      minChildSize: 0.45,
      maxChildSize: 0.92,
      expand: false,
      builder: (ctx, scrollCtrl) => Container(
        decoration: const BoxDecoration(
          color: Color(0xFF141820),
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        child: Column(
          children: [
            const SizedBox(height: 10),
            Container(
              width: 38,
              height: 4,
              decoration: BoxDecoration(
                color: Colors.white24,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 10),
              child: Row(
                children: [
                  Container(
                    width: 10,
                    height: 10,
                    decoration: BoxDecoration(
                      color: statusColor,
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'Live Sync Details & Logs',
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 16,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        Text(
                          '${snapshot.ssid} • ${snapshot.host}:${snapshot.port} • ${snapshot.chanspec}',
                          style: const TextStyle(color: Colors.white54, fontSize: 11.5),
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: 'Close',
                    onPressed: () => Navigator.of(ctx).pop(),
                    icon: const Icon(Icons.close, color: Colors.white60),
                  ),
                ],
              ),
            ),
            const Divider(height: 1, color: Color(0xFF232936)),
            Expanded(
              child: ListView(
                controller: scrollCtrl,
                padding: const EdgeInsets.all(16),
                children: [
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      FilledButton.icon(
                        onPressed: snapshot.serviceRunning
                            ? bridge.stopService
                            : bridge.startService,
                        style: FilledButton.styleFrom(
                          backgroundColor: snapshot.serviceRunning
                              ? const Color(0xFF334155)
                              : _accent,
                          foregroundColor: Colors.white,
                        ),
                        icon: Icon(
                          snapshot.serviceRunning ? Icons.stop : Icons.play_arrow,
                          size: 18,
                        ),
                        label: Text(
                          snapshot.serviceRunning
                              ? 'Stop Background Sync'
                              : 'Start Background Sync',
                        ),
                      ),
                      OutlinedButton.icon(
                        onPressed: bridge.syncNow,
                        icon: const Icon(Icons.refresh, size: 18),
                        label: const Text('Sync Now'),
                      ),
                      OutlinedButton.icon(
                        onPressed: bridge.openWifiSettings,
                        icon: const Icon(Icons.wifi, size: 18),
                        label: const Text('Wi-Fi'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: const Color(0xFF1A202B),
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: Colors.white10),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _kv('State', snapshot.connectionState),
                        _kv(
                          'Camera',
                          snapshot.cameraId.isEmpty
                              ? 'Waiting for camera...'
                              : '${snapshot.cameraId} (${snapshot.powerState})',
                        ),
                        _kv('Queue', '${snapshot.queueLen} pending'),
                        _kv(
                          'Speed',
                          '${snapshot.speedMbps.toStringAsFixed(2)} MB/s',
                        ),
                        _kv('Pipeline', snapshot.hookName),
                        if (snapshot.lastError.isNotEmpty)
                          Padding(
                            padding: const EdgeInsets.only(top: 6),
                            child: Text(
                              'Last error: ${snapshot.lastError}',
                              style: const TextStyle(
                                color: Color(0xFFFCA5A5),
                                fontSize: 12,
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                  if (!batteryExempt) ...[
                    const SizedBox(height: 10),
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: const Color(0xFF451A03),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.battery_alert, color: Color(0xFFFBBF24)),
                          const SizedBox(width: 10),
                          const Expanded(
                            child: Text(
                              'Allow Unrestricted battery usage for locked-screen transfers.',
                              style: TextStyle(fontSize: 12),
                            ),
                          ),
                          TextButton(
                            onPressed: () async {
                              await bridge.requestBatteryOptimizationExemption();
                              onBatteryRefresh();
                            },
                            child: const Text('Allow'),
                          ),
                        ],
                      ),
                    ),
                  ],
                  const SizedBox(height: 16),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text(
                        'Transfer Logs',
                        style: TextStyle(
                          color: Colors.white70,
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      TextButton(
                        onPressed: bridge.clearLogs,
                        child: const Text('Clear'),
                      ),
                    ],
                  ),
                  if (snapshot.logs.isEmpty)
                    const Padding(
                      padding: EdgeInsets.symmetric(vertical: 18),
                      child: Text(
                        'No transfer logs yet.',
                        style: TextStyle(color: Colors.white38, fontSize: 12),
                      ),
                    )
                  else
                    for (final line in snapshot.logs)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 3),
                        child: Text(
                          line,
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            fontSize: 11,
                            color: Colors.white70,
                          ),
                        ),
                      ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _kv(String k, String v) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          SizedBox(
            width: 78,
            child: Text(
              k,
              style: const TextStyle(color: Colors.white54, fontSize: 12),
            ),
          ),
          Expanded(
            child: Text(
              v,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                color: Colors.white,
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
