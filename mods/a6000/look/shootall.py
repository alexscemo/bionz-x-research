#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 voxivoid
# From voxivoid/a6000-qemu (commit 62cf3bc) mods/look/shootall.py.
"""shootall.py - apply each ISP look in turn on a real A6000 and fire the shutter, so no manual shooting.

EXPERIMENTAL: no recorded run on hardware.

    CAM=<ip> shootall.py probe                      # find out how this camera can be triggered (run first, see below)
    CAM=<ip> shootall.py list                       # looks that would be shot
    CAM=<ip> shootall.py run [--looks a,b,c] [--trigger T] [--settle 2] [--wait 4] [--shots 1] [--pull DIR]
    CAM=<ip> shootall.py shoot [--trigger T]        # just fire the shutter once (no look change)

Looks = every presets/*.json + one generated preset per gamma curve (stock matrix/sat; `g_<name>`):
slog2 slog3 logc3 arri709 rec709 hlg scinetone movie still cine1..cine4.  Stock (no look) is shot first as `stock`.
Per look: ispctl apply -> settle -> trigger -> wait for write -> note newest file (-> optional pull).  Ends with ispctl revert.
Results go to ./shootall_<timestamp>/manifest.csv (look, time, newest file on card).

Triggers (--trigger, default $SHOOT_TRIGGER or `keys`); NONE VERIFIED ON HARDWARE, use `probe` to find which works:
  keys    adb `input keyevent` 80 (FOCUS) then 27 (CAMERA)       (env KEY_FOCUS / KEY_SHUTTER to change)
  sendev  adb sendevent on $INPUT_DEV with $EV_SHUTTER="type code" (e.g. "1 212"); half-press first if $EV_HALF set
  api     Sony Camera Remote API actTakePicture on http://CAM:8080/sony/camera (only if camera is in remote-control mode)
  cmd     any shell command on the PC in $SHOOT_CMD (e.g. a script that calls your own mechanism)
Camera must be in a still shooting mode with live view up (not menu/playback), MF/AF as you like, focus achievable.
"""
import argparse, csv, glob, json, os, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
CAM = os.environ.get('CAM') or sys.exit('set CAM=<camera ip>')
GAMMAS = ['slog2', 'slog3', 'logc3', 'arri709', 'rec709', 'hlg', 'scinetone', 'movie', 'still', 'cine1', 'cine2', 'cine3', 'cine4']
CARD_GLOB = os.environ.get('CARD_DIRS', '/mnt/sdcard')   # searched for DCIM by probe


def adb(*a, check=False, timeout=60):
    subprocess.run(['adb', 'connect', CAM + ':5555'], capture_output=True)
    r = subprocess.run(['adb', '-s', CAM + ':5555', *a], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode: sys.exit('adb %s failed: %s' % (a, r.stderr))
    return r.stdout + r.stderr


def ashell(cmd, **k): return adb('shell', cmd, **k)


# ---- triggers -------------------------------------------------------------------------------------------------
def t_keys():
    ashell('input keyevent %s' % os.environ.get('KEY_FOCUS', '80')); time.sleep(1.0)
    ashell('input keyevent %s' % os.environ.get('KEY_SHUTTER', '27'))


def t_sendev():
    dev = os.environ.get('INPUT_DEV') or sys.exit('set INPUT_DEV=/dev/input/eventN (see `probe`)')
    shut = os.environ.get('EV_SHUTTER') or sys.exit('set EV_SHUTTER="type code" e.g. "1 212"')
    def press(tc):
        t, c = tc.split()
        ashell('sendevent %s %s %s 1; sendevent %s 0 0 0; sleep 0.3; sendevent %s %s %s 0; sendevent %s 0 0 0' % (dev, t, c, dev, dev, t, c, dev))
    if os.environ.get('EV_HALF'): press(os.environ['EV_HALF']); time.sleep(1.0)
    press(shut)


def t_api():
    def call(m, p=None):
        req = urllib.request.Request('http://%s:8080/sony/camera' % CAM, json.dumps({'method': m, 'params': p or [], 'id': 1, 'version': '1.0'}).encode(),
                                     {'Content-Type': 'application/json'})
        return json.load(urllib.request.urlopen(req, timeout=30))
    r = call('actTakePicture')
    if 'error' in r: sys.exit('api error %s (camera not in remote mode? try another trigger)' % r['error'])


def t_cmd():
    c = os.environ.get('SHOOT_CMD') or sys.exit('set SHOOT_CMD')
    if subprocess.run(c, shell=True).returncode: sys.exit('SHOOT_CMD failed')


TRIGGERS = {'keys': t_keys, 'sendev': t_sendev, 'api': t_api, 'cmd': t_cmd}


def probe():
    print('== adb:', adb('devices').strip().replace('\n', ' | '))
    print('== input devices (name + key caps):'); print(ashell('cat /proc/bus/input/devices')[:3000])
    print('== /dev/input:', ashell('ls /dev/input').strip())
    print('== DCIM dirs:', ashell('for d in %s; do find $d -maxdepth 4 -name DCIM 2>/dev/null; done' % CARD_GLOB).strip())
    try:
        urllib.request.urlopen('http://%s:8080/sony/camera' % CAM, timeout=3); print('== remote API port 8080: answers')
    except Exception as e:
        print('== remote API port 8080: no (%s)' % e)
    print('\nTo find the shutter key: run `adb shell getevent -l` and press half/full shutter by hand, then use\n'
          '  --trigger sendev with INPUT_DEV=/dev/input/eventN EV_HALF="1 <code>" EV_SHUTTER="1 <code>" (decimal codes, `getevent` w/o -l)\n'
          'or try --trigger keys first (shoot once) and see whether a picture is taken.')


# ---- card -----------------------------------------------------------------------------------------------------
def newest_file():
    """Newest still on the card. adb's toolbox has no `ls -t`/head/find; Sony names rise monotonically, so take the last by name."""
    root = os.environ.get('DCIM', '/mnt/sdcard/DCIM')
    dirs = sorted(d.strip() for d in ashell('ls %s' % root).splitlines() if d.strip().endswith('MSDCF'))
    if not dirs: return ''
    files = sorted(f.strip() for f in ashell('ls %s/%s' % (root, dirs[-1])).splitlines() if f.strip().upper().endswith(('.JPG', '.ARW')))
    return '%s/%s/%s' % (root, dirs[-1], files[-1]) if files else ''


# ---- looks ----------------------------------------------------------------------------------------------------
def all_looks(tmp):
    looks = {'stock': None}
    for f in sorted(glob.glob(os.path.join(HERE, 'presets', '*.json'))): looks[os.path.basename(f)[:-5]] = f
    for g in GAMMAS:
        binf = os.path.join(tmp, g + '.bin')
        subprocess.run([sys.executable, os.path.join(HERE, 'make_hwgamma.py'), binf, g], check=True, capture_output=True)
        pf = os.path.join(tmp, 'g_%s.json' % g)
        json.dump({'name': 'gamma ' + g, 'gamma': {'file': binf}}, open(pf, 'w')); looks['g_' + g] = pf
    return looks


def ispctl(*a):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'ispctl.py'), *a], env=dict(os.environ, CAM=CAM), capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['probe', 'list', 'run', 'shoot'])
    ap.add_argument('--looks'); ap.add_argument('--trigger', default=os.environ.get('SHOOT_TRIGGER', 'keys'), choices=list(TRIGGERS))
    ap.add_argument('--settle', type=float, default=2.0, help='s after apply before shooting')
    ap.add_argument('--wait', type=float, default=4.0, help='s after shutter for the card write')
    ap.add_argument('--shots', type=int, default=1); ap.add_argument('--pull', metavar='DIR')
    a = ap.parse_args()
    if a.cmd == 'probe': return probe()
    if a.cmd == 'shoot': TRIGGERS[a.trigger](); return
    import tempfile
    tmp = tempfile.mkdtemp(prefix='shootall_'); looks = all_looks(tmp)
    names = a.looks.split(',') if a.looks else list(looks)
    bad = [n for n in names if n not in looks]
    if bad: sys.exit('unknown looks %s; have %s' % (bad, list(looks)))
    if a.cmd == 'list': print('\n'.join(names)); return
    outdir = os.path.join(os.getcwd(), 'shootall_' + time.strftime('%Y%m%d_%H%M%S')); os.makedirs(outdir)
    pulldir = a.pull or None
    if pulldir: os.makedirs(pulldir, exist_ok=True)
    man = csv.writer(open(os.path.join(outdir, 'manifest.csv'), 'w', newline='')); man.writerow(['look', 'shot', 'time', 'file_on_card'])
    try:
        for n in names:
            if n == 'stock': ispctl('revert'); print('[stock] reverted')
            else:
                rc, out = ispctl('apply', looks[n]); print('[%s] %s' % (n, out.strip().splitlines()[-1] if out.strip() else rc))
                if rc or 'WARNING' in out: print('  apply problem, skipping shot:\n' + out); continue
            for s in range(a.shots):
                time.sleep(a.settle); before = newest_file(); TRIGGERS[a.trigger](); time.sleep(a.wait); f = newest_file()
                ok = f and f != before
                print('  shot %d -> %s' % (s + 1, f if ok else 'NO NEW FILE (trigger not working / still writing / AF fail?)'))
                man.writerow([n, s + 1, time.strftime('%H:%M:%S'), f if ok else ''])
                if ok and pulldir:
                    dst = os.path.join(pulldir, '%s_%d_%s' % (n, s + 1, os.path.basename(f))); adb('pull', f, dst); print('  pulled', dst)
    finally:
        print('reverting'); print(ispctl('revert')[1].strip())
    print('manifest:', os.path.join(outdir, 'manifest.csv'))


if __name__ == '__main__':
    main()
