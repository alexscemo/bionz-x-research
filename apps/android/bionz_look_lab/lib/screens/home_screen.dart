import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models/look_models.dart';
import '../models/sync_models.dart';
import '../services/sync_bridge.dart';
import '../widgets/settings_sheet.dart';
import '../widgets/sync_details_sheet.dart';
import 'snapseed_editor_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.bridge});

  final SyncBridge bridge;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  static const _accent = Color(0xFFFF6D00);
  static const _surface = Color(0xFF151921);
  static const _chipBorder = Color(0xFF2B3242);

  SyncSnapshot _snapshot = const SyncSnapshot();
  SyncConfig _config = const SyncConfig();
  LookCatalog _catalog = LookCatalog.fallbackDefaults();
  Set<String> _gradedNames = const {};
  bool _batteryExempt = true;
  bool _busyPreset = false;

  final Map<String, Uint8List> _thumbCache = {};
  final Map<String, String> _thumbSig = {};
  final Set<String> _thumbLoading = {};

  StreamSubscription<SyncSnapshot>? _sub;

  @override
  void initState() {
    super.initState();
    _loadInitial();
    _sub = widget.bridge.events.listen((snap) {
      if (!mounted) return;
      final prevCount = _snapshot.syncedCount;
      setState(() => _snapshot = snap);
      _prefetchThumbnails(snap.recentFiles);
      if (snap.syncedCount != prevCount) {
        _refreshGradedPhotos();
      }
    });
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  Future<void> _loadInitial() async {
    final results = await Future.wait([
      widget.bridge.getSnapshot(),
      widget.bridge.getConfig(),
      widget.bridge.isIgnoringBatteryOptimizations(),
      widget.bridge.getLookCatalog(),
      widget.bridge.getGradedPhotos(),
    ]);
    if (!mounted) return;
    final snap = results[0] as SyncSnapshot;
    setState(() {
      _snapshot = snap;
      _config = results[1] as SyncConfig;
      _batteryExempt = results[2] as bool;
      _catalog = results[3] as LookCatalog;
      _gradedNames = results[4] as Set<String>;
    });
    _prefetchThumbnails(snap.recentFiles);
  }

  Future<void> _refreshGradedPhotos() async {
    final graded = await widget.bridge.getGradedPhotos();
    if (!mounted) return;
    setState(() => _gradedNames = graded);
  }

  Future<void> _refreshBattery() async {
    final exempt = await widget.bridge.isIgnoringBatteryOptimizations();
    if (!mounted) return;
    setState(() => _batteryExempt = exempt);
  }

  void _prefetchThumbnails(List<SyncedFile> files) {
    for (final f in files.take(36)) {
      final sig = '${f.crc32Hex}:${f.sizeBytes}';
      if (_thumbSig[f.dcfName] != sig) {
        _thumbCache.remove(f.dcfName);
        _thumbSig[f.dcfName] = sig;
      }
      if (_thumbCache.containsKey(f.dcfName) || _thumbLoading.contains(f.dcfName)) {
        continue;
      }
      _thumbLoading.add(f.dcfName);
      widget.bridge.loadPhotoThumbnail(f.dcfName).then((bytes) {
        _thumbLoading.remove(f.dcfName);
        if (!mounted || bytes == null) return;
        setState(() => _thumbCache[f.dcfName] = bytes);
      });
    }
  }

  Future<void> _selectAutoApply(String? presetId) async {
    if (_busyPreset) return;
    setState(() => _busyPreset = true);
    try {
      final updated = await widget.bridge.setAutoApplyPreset(presetId);
      if (!mounted) return;
      setState(() => _catalog = updated);
    } finally {
      if (mounted) setState(() => _busyPreset = false);
    }
  }

  Future<void> _openEditor(SyncedFile file) async {
    final activePreset = _catalog.findPreset(_catalog.autoApplyPresetId);
    final exported = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => SnapseedEditorScreen(
          bridge: widget.bridge,
          file: file,
          initialCatalog: _catalog,
          initialPreset: activePreset,
          onCatalogChanged: (cat) => setState(() => _catalog = cat),
        ),
      ),
    );
    if (!mounted) return;
    final cat = await widget.bridge.getLookCatalog();
    final graded = await widget.bridge.getGradedPhotos();
    if (!mounted) return;
    setState(() {
      _catalog = cat;
      _gradedNames = graded;
    });
    if (exported == true) {
      _prefetchThumbnails(_snapshot.recentFiles);
    }
  }

  Future<void> _showPhotoActions(SyncedFile file) async {
    final active = _catalog.findPreset(_catalog.autoApplyPresetId);
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: const Color(0xFF141820),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              title: Text(
                file.dcfName,
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              subtitle: Text(
                '${file.sizeMb.toStringAsFixed(1)} MB • ${file.isRaw ? "14-bit RAW" : "JPEG"}',
                style: const TextStyle(color: Colors.white54, fontSize: 12),
              ),
            ),
            const Divider(height: 1, color: Color(0xFF232936)),
            ListTile(
              leading: const Icon(Icons.tune, color: _accent),
              title: const Text('Edit in Look Lab'),
              onTap: () {
                Navigator.of(ctx).pop();
                _openEditor(file);
              },
            ),
            if (active != null)
              ListTile(
                leading: const Icon(Icons.auto_awesome, color: _accent),
                title: Text('Export with ${active.name}'),
                onTap: () async {
                  Navigator.of(ctx).pop();
                  final res = await widget.bridge.exportGradedPhoto(file.dcfName, active);
                  await _refreshGradedPhotos();
                  if (!mounted) return;
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      content: Text(
                        res.localPath.isNotEmpty
                            ? 'Exported ${res.localPath} (${res.elapsedMs} ms)'
                            : 'Export complete.',
                      ),
                    ),
                  );
                },
              ),
          ],
        ),
      ),
    );
  }

  void _openSyncDetailsSheet() {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => StreamBuilder<SyncSnapshot>(
        stream: widget.bridge.events,
        initialData: _snapshot,
        builder: (ctx, snap) => SyncDetailsSheet(
          bridge: widget.bridge,
          snapshot: snap.data ?? _snapshot,
          batteryExempt: _batteryExempt,
          onBatteryRefresh: _refreshBattery,
        ),
      ),
    );
  }

  void _openSettingsSheet() {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => SettingsSheet(
        bridge: widget.bridge,
        config: _config,
        batteryExempt: _batteryExempt,
        onConfigSaved: (cfg) => setState(() => _config = cfg),
        onBatteryRefresh: _refreshBattery,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final files = _snapshot.recentFiles;
    final totalCount =
        _snapshot.syncedCount > 0 ? _snapshot.syncedCount : files.length;

    return Scaffold(
      backgroundColor: const Color(0xFF0E1116),
      body: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _buildLiveSyncHeader(totalCount),
            const Divider(height: 1, color: Color(0xFF1F2532)),
            _buildPresetStrip(),
            const SizedBox(height: 4),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 10),
              child: Row(
                children: [
                  Text(
                    'Synced Photos ($totalCount)',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ],
              ),
            ),
            Expanded(
              child: files.isEmpty
                  ? _buildEmptyPhotosState()
                  : GridView.builder(
                      padding: const EdgeInsets.fromLTRB(14, 0, 14, 16),
                      itemCount: files.length,
                      gridDelegate:
                          const SliverGridDelegateWithFixedCrossAxisCount(
                        crossAxisCount: 3,
                        crossAxisSpacing: 8,
                        mainAxisSpacing: 12,
                        childAspectRatio: 0.78,
                      ),
                      itemBuilder: (ctx, idx) => _buildPhotoTile(files[idx]),
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildLiveSyncHeader(int totalCount) {
    final isConnected = _snapshot.connectionState == 'Connected';
    final isConnecting = _snapshot.serviceRunning && !isConnected;
    final dotColor = isConnected
        ? const Color(0xFF22C55E)
        : (isConnecting ? const Color(0xFFF59E0B) : const Color(0xFF6B7280));
    final stateLabel = isConnected
        ? 'Connected to a6400'
        : (_snapshot.serviceRunning ? '${_snapshot.connectionState} a6400' : 'Sync Stopped');
    final speedText = '${_snapshot.speedMbps.toStringAsFixed(1)} MB/s';

    return Material(
      color: _surface,
      child: InkWell(
        onTap: () {
          if (_snapshot.serviceRunning) {
            _openSyncDetailsSheet();
          } else {
            widget.bridge.startService();
          }
        },
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'Live Sync',
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 17,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 0.2,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Row(
                          children: [
                            Container(
                              width: 7.5,
                              height: 7.5,
                              decoration: BoxDecoration(
                                color: dotColor,
                                shape: BoxShape.circle,
                              ),
                            ),
                            const SizedBox(width: 6),
                            Flexible(
                              child: RichText(
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                text: TextSpan(
                                  style: const TextStyle(
                                    color: Colors.white70,
                                    fontSize: 12.5,
                                  ),
                                  children: [
                                    TextSpan(text: stateLabel),
                                    const TextSpan(
                                      text: '  |  ',
                                      style: TextStyle(color: Colors.white24),
                                    ),
                                    TextSpan(text: speedText),
                                    const TextSpan(
                                      text: '  |  ',
                                      style: TextStyle(color: Colors.white24),
                                    ),
                                    TextSpan(
                                      text: '$totalCount synced',
                                      style: const TextStyle(
                                        color: _accent,
                                        fontWeight: FontWeight.w600,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: 'Sync Details & Logs',
                    onPressed: _openSyncDetailsSheet,
                    icon: const Icon(
                      Icons.folder_copy_rounded,
                      color: _accent,
                      size: 24,
                    ),
                  ),
                ],
              ),
              if (_snapshot.activeFile.isNotEmpty) ...[
                const SizedBox(height: 8),
                ClipRRect(
                  borderRadius: BorderRadius.circular(3),
                  child: LinearProgressIndicator(
                    value: _snapshot.progress.clamp(0.0, 1.0),
                    minHeight: 3,
                    backgroundColor: Colors.white12,
                    color: _accent,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildPresetStrip() {
    final activeId = _catalog.autoApplyPresetId;
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 12, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(
                child: Text(
                  'Auto-Apply Film Preset',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 15.5,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              IconButton(
                tooltip: 'Settings',
                visualDensity: VisualDensity.compact,
                onPressed: _openSettingsSheet,
                icon: const Icon(
                  Icons.settings_outlined,
                  color: Colors.white60,
                  size: 21,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          SizedBox(
            height: 44,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              clipBehavior: Clip.none,
              itemCount: 1 + _catalog.presets.length,
              separatorBuilder: (_, _) => const SizedBox(width: 8),
              itemBuilder: (ctx, idx) {
                if (idx == 0) {
                  return _buildPresetPill(
                    label: 'OFF (Raw only)',
                    selected: activeId == null,
                    onTap: () => _selectAutoApply(null),
                  );
                }
                final p = _catalog.presets[idx - 1];
                return _buildPresetPill(
                  label: p.name,
                  selected: activeId == p.id,
                  onTap: () => _selectAutoApply(p.id),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPresetPill({
    required String label,
    required bool selected,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Container(
            height: 38,
            margin: const EdgeInsets.only(top: 5),
            padding: const EdgeInsets.symmetric(horizontal: 14),
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: selected
                  ? _accent.withValues(alpha: 0.14)
                  : const Color(0xFF171C24),
              borderRadius: BorderRadius.circular(11),
              border: Border.all(
                color: selected ? _accent : _chipBorder,
                width: selected ? 1.4 : 1.0,
              ),
            ),
            child: Text(
              label,
              style: TextStyle(
                color: selected ? Colors.white : Colors.white70,
                fontSize: 12.5,
                fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
              ),
            ),
          ),
          if (selected)
            Positioned(
              top: -1,
              right: -3,
              child: Container(
                width: 16,
                height: 16,
                decoration: const BoxDecoration(
                  color: _accent,
                  shape: BoxShape.circle,
                ),
                child: const Icon(Icons.check, size: 11, color: Colors.white),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildPhotoTile(SyncedFile f) {
    final thumb = _thumbCache[f.dcfName];
    final stem = _fileStem(f.dcfName);
    final isGraded = _gradedNames.contains(f.dcfName);
    final badgeText = f.isPreview
        ? 'PREVIEW'
        : (f.isRaw ? (isGraded ? 'RAW + FILM' : 'RAW') : (isGraded ? 'JPG + FILM' : 'JPG'));
    final badgeColor = f.isPreview
        ? const Color(0xFFF59E0B)
        : (isGraded ? _accent : Colors.white);

    return GestureDetector(
      onTap: () => _openEditor(f),
      onLongPress: () => _showPhotoActions(f),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AspectRatio(
            aspectRatio: 1.0,
            child: Container(
              decoration: BoxDecoration(
                color: const Color(0xFF171C24),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: Colors.white10),
              ),
              clipBehavior: Clip.antiAlias,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  if (thumb != null)
                    Image.memory(
                      thumb,
                      fit: BoxFit.cover,
                      gaplessPlayback: true,
                    )
                  else
                    const Center(
                      child: Icon(
                        Icons.photo_outlined,
                        color: Colors.white24,
                        size: 28,
                      ),
                    ),
                  Positioned(
                    top: 6,
                    right: 6,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 6,
                        vertical: 2,
                      ),
                      decoration: BoxDecoration(
                        color: Colors.black.withValues(alpha: 0.72),
                        borderRadius: BorderRadius.circular(5),
                      ),
                      child: Text(
                        badgeText,
                        style: TextStyle(
                          color: badgeColor,
                          fontSize: 9.5,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 0.3,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 5),
          Text(
            stem,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              color: Colors.white60,
              fontSize: 11,
              fontWeight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildEmptyPhotosState() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(
              Icons.camera_outlined,
              size: 48,
              color: Colors.white24,
            ),
            const SizedBox(height: 12),
            const Text(
              'No synced photos yet',
              style: TextStyle(
                color: Colors.white70,
                fontSize: 15,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 6),
            const Text(
              'Tap Live Sync above to connect to your Sony a6400 5 GHz hotspot.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.white38, fontSize: 12.5),
            ),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: _snapshot.serviceRunning
                  ? widget.bridge.syncNow
                  : widget.bridge.startService,
              style: FilledButton.styleFrom(
                backgroundColor: _accent,
                foregroundColor: Colors.white,
              ),
              icon: Icon(
                _snapshot.serviceRunning ? Icons.refresh : Icons.play_arrow,
              ),
              label: Text(
                _snapshot.serviceRunning
                    ? 'Sync Now'
                    : 'Start Background Sync',
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _fileStem(String dcfName) {
    final dot = dcfName.lastIndexOf('.');
    return dot > 0 ? dcfName.substring(0, dot) : dcfName;
  }
}
