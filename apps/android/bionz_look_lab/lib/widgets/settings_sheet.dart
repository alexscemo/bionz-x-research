import 'package:flutter/material.dart';

import '../models/sync_models.dart';
import '../services/sync_bridge.dart';

class SettingsSheet extends StatefulWidget {
  const SettingsSheet({
    super.key,
    required this.bridge,
    required this.config,
    required this.batteryExempt,
    required this.onConfigSaved,
    required this.onBatteryRefresh,
  });

  final SyncBridge bridge;
  final SyncConfig config;
  final bool batteryExempt;
  final ValueChanged<SyncConfig> onConfigSaved;
  final VoidCallback onBatteryRefresh;

  @override
  State<SettingsSheet> createState() => _SettingsSheetState();
}

class _SettingsSheetState extends State<SettingsSheet> {
  late SyncConfig _cfg;
  late TextEditingController _ssidCtrl;
  late TextEditingController _pskCtrl;
  late TextEditingController _hostCtrl;
  late TextEditingController _portCtrl;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _cfg = widget.config;
    _ssidCtrl = TextEditingController(text: _cfg.ssid);
    _pskCtrl = TextEditingController(text: _cfg.psk);
    _hostCtrl = TextEditingController(text: _cfg.host);
    _portCtrl = TextEditingController(text: '${_cfg.port}');
  }

  @override
  void dispose() {
    _ssidCtrl.dispose();
    _pskCtrl.dispose();
    _hostCtrl.dispose();
    _portCtrl.dispose();
    super.dispose();
  }

  Future<void> _saveToggles(SyncConfig updated) async {
    setState(() => _cfg = updated);
    final saved = await widget.bridge.saveConfig(updated);
    widget.onConfigSaved(saved);
  }

  Future<void> _saveNetworkFields() async {
    if (_saving) return;
    setState(() => _saving = true);
    final port = int.tryParse(_portCtrl.text.trim()) ?? _cfg.port;
    final next = _cfg.copyWith(
      ssid: _ssidCtrl.text.trim().isEmpty ? _cfg.ssid : _ssidCtrl.text.trim(),
      psk: _pskCtrl.text.trim().isEmpty ? _cfg.psk : _pskCtrl.text.trim(),
      host: _hostCtrl.text.trim().isEmpty ? _cfg.host : _hostCtrl.text.trim(),
      port: port.clamp(1024, 65535),
    );
    final saved = await widget.bridge.saveConfig(next);
    if (!mounted) return;
    setState(() {
      _cfg = saved;
      _saving = false;
    });
    widget.onConfigSaved(saved);
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
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
              padding: const EdgeInsets.fromLTRB(16, 12, 8, 8),
              child: Row(
                children: [
                  const Expanded(
                    child: Text(
                      'Camera & Look Lab Settings',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                      ),
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
                  Card(
                    child: SwitchListTile(
                      secondary: const Icon(Icons.bolt, color: Color(0xFFFF6D00)),
                      title: const Text('ARW Instant Preview + Background RAW'),
                      subtitle: const Text(
                        'Show the embedded JPEG preview immediately while the 14-bit .ARW finishes transferring.',
                      ),
                      value: _cfg.arwInstantPreview,
                      onChanged: (v) =>
                          _saveToggles(_cfg.copyWith(arwInstantPreview: v)),
                    ),
                  ),
                  const SizedBox(height: 10),
                  Card(
                    child: SwitchListTile(
                      secondary: const Icon(Icons.wifi_find),
                      title: const Text('Auto-Suggest 5 GHz Camera Wi-Fi'),
                      subtitle: const Text(
                        'Register Android Wi-Fi suggestion for the A6400 5 GHz AP.',
                      ),
                      value: _cfg.autoSuggestWifi,
                      onChanged: (v) =>
                          _saveToggles(_cfg.copyWith(autoSuggestWifi: v)),
                    ),
                  ),
                  const SizedBox(height: 10),
                  Card(
                    child: Column(
                      children: [
                        ListTile(
                          leading: Icon(
                            widget.batteryExempt
                                ? Icons.check_circle
                                : Icons.warning_amber,
                            color: widget.batteryExempt
                                ? const Color(0xFF34D399)
                                : const Color(0xFFFBBF24),
                          ),
                          title: const Text('Unrestricted Background Battery'),
                          subtitle: Text(
                            widget.batteryExempt
                                ? 'Enabled for background transfers.'
                                : 'Tap Configure to disable battery throttling.',
                          ),
                          trailing: OutlinedButton(
                            onPressed: () async {
                              await widget.bridge
                                  .requestBatteryOptimizationExemption();
                              widget.onBatteryRefresh();
                            },
                            child: const Text('Configure'),
                          ),
                        ),
                        const Divider(height: 1),
                        ListTile(
                          leading: const Icon(Icons.folder_outlined),
                          title: const Text('Destination Storage'),
                          subtitle: const Text('DCIM/BionzLookLab (14-bit RAW + JPEG)'),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(14),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text(
                            'Camera AP Endpoint',
                            style: TextStyle(
                              fontSize: 13,
                              fontWeight: FontWeight.w600,
                              color: Colors.white70,
                            ),
                          ),
                          const SizedBox(height: 10),
                          TextField(
                            controller: _ssidCtrl,
                            decoration: const InputDecoration(
                              labelText: 'SSID',
                              isDense: true,
                              border: OutlineInputBorder(),
                            ),
                          ),
                          const SizedBox(height: 10),
                          TextField(
                            controller: _pskCtrl,
                            decoration: const InputDecoration(
                              labelText: 'Passphrase',
                              isDense: true,
                              border: OutlineInputBorder(),
                            ),
                          ),
                          const SizedBox(height: 10),
                          Row(
                            children: [
                              Expanded(
                                flex: 2,
                                child: TextField(
                                  controller: _hostCtrl,
                                  decoration: const InputDecoration(
                                    labelText: 'Camera IP',
                                    isDense: true,
                                    border: OutlineInputBorder(),
                                  ),
                                ),
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: TextField(
                                  controller: _portCtrl,
                                  keyboardType: TextInputType.number,
                                  decoration: const InputDecoration(
                                    labelText: 'Port',
                                    isDense: true,
                                    border: OutlineInputBorder(),
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 12),
                          Align(
                            alignment: Alignment.centerRight,
                            child: FilledButton(
                              onPressed: _saving ? null : _saveNetworkFields,
                              style: FilledButton.styleFrom(
                                backgroundColor: const Color(0xFFFF6D00),
                                foregroundColor: Colors.white,
                              ),
                              child: const Text('Save Endpoint'),
                            ),
                          ),
                        ],
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
}
