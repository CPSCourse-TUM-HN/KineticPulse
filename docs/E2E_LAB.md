# Lab end-to-end checklist

Goal: one green path — **Jetson + wristband TCP + webcam + Tailscale + Galaxy APK + dashboard real mode**.

## 0. Prerequisites

- [ ] Jetson on network; SSH works (no monitor needed)
- [ ] Repo cloned; `./bootstrap.sh` already run once
- [ ] Tailscale on Jetson + caregiver phone/laptop (same tailnet)
- [ ] ESP32 flashed with contract-compatible firmware ([`src/main.cpp`](../src/main.cpp))
- [ ] Galaxy: KineticPulse **APK** (not Expo Go)

## 1. Jetson runtime

```bash
sudo systemctl status kineticpulse-signaling kineticpulse
# or foreground smoke:
./kineticpulse --mock-stt
```

- [ ] Log: `TCP: listening on ...:5555`
- [ ] Log: `Monitoring HTTP listening on ...:8790`
- [ ] `curl -s http://127.0.0.1:8790/monitoring | head` returns JSON

## 2. Wristband

```bash
cp src/wifi_secrets.h.example src/wifi_secrets.h   # set SSID + Jetson IP
pio run -e seeed_xiao_esp32s3 -t upload
```

- [ ] Serial: `WiFi OK` + `TCP connect ... ok`
- [ ] Jetson log: `TCP: wristband connected` + `hello`
- [ ] `curl` monitoring → `sensor.connection` = `"connected"`, `latest_hr_bpm` updates

If IMU not on board yet, leave `has_accelerometer: false` in `config.yaml`. Synthetic `accel` lines still parse; Scenario B full bypass needs a real IMU later.

## 3. Camera

- [ ] Webcam/CSI attached; not using `--no-camera`
- [ ] Log shows detector/pose activity (or expected weight load messages)

## 4. Dashboard (real vitals)

On laptop (same tailnet), from `deploy/handoff/caregiver.env`:

```bash
cp deploy/handoff/caregiver.env dashboard/.env.local
# ensure MONITORING_DATA_MODE=real and KINETICPULSE_MONITORING_HTTP_URL=http://<ts-ip>:8790/monitoring
cd dashboard && npm run dev
```

- [ ] http://localhost:3000 shows live HR / sensor connected
- [ ] Tier/vision fields update (not stuck on mock scenarios)

## 5. Mobile (Galaxy)

- [ ] Tailscale connected
- [ ] APK installed; **Scan setup QR** (`/handoff?token=...` or `caregiver-qr.png`)
- [ ] Session list loads from signaling `:8787`
- [ ] Trigger Tier 1/2 (mock scenario or real fall) → join → video < ~5s

Trigger without falling on camera:

```bash
./kineticpulse --mock-ble --mock-ble-scenario fall_b_seizure --mock-stt
```

(Use real TCP wristband instead of `--mock-ble` once step 2 is green.)

## 3b. Live preview

On the Jetson's own desktop:

```bash
python -m kineticpulse.main --config config.yaml --preview --preview-scale 0.75
```

- [ ] Log: `Preview window open: 'KineticPulse - motion detection'`
- [ ] Window shows the bbox, skeleton, MOTION and FUSION panels
- [ ] Header FPS matches what the pipeline is really achieving (see below)
- [ ] `q` in the window stops the pipeline cleanly

Over SSH a window cannot open, so write the overlay to a file:

```bash
python -m kineticpulse.main --config config.yaml \
  --preview-snapshot /tmp/kp-live.png --preview-snapshot-every 15
```

- [ ] Log: `Preview snapshots -> /tmp/kp-live.png (every 15 frames)`
- [ ] The file refreshes; `scp` or open it to check the overlay

**Check the FPS in the header.** Inference is the bottleneck, not capture:
capture reaches 28.7 FPS on MJPG while the two YOLO models do not. Measured
on an Orin Nano Super at 1280x720:

| Setup | CUDA | Vision FPS |
|---|---|---|
| system `/usr/bin/python` | unavailable (generic PyPI wheel) | 0.65 |
| `.venv`, PyTorch weights | Orin, CUDA 12.6 | 14.3 |
| `.venv`, TensorRT FP16 engines | same | 17.4 |
| `.venv`, engines + `--preview` | same | 13.0 |

- [ ] Started through `./deploy/jetson/run` or with `.venv` activated —
      **not** the system interpreter
- [ ] Startup log says `Accelerator: Orin (torch ..., CUDA 12.6)`, not
      `CUDA IS UNAVAILABLE`
- [ ] `.venv/bin/python -c "import torch; print(torch.cuda.is_available())"` → `True`
- [ ] `nvpmodel -q` reports `MAXN_SUPER`
- [ ] `tegrastats` shows `GR3D_FREQ` high (GPU-bound, as expected)
- [ ] `Vision: N FPS over 10s ...` appears in the log every 10 s and N is
      double-digit
- [ ] Dashboard shows an "Edge rate" badge with that same number, and **no**
      "Edge runtime" banner
- [ ] `curl -s http://127.0.0.1:8790/monitoring | jq .runtime` → `health: "ok"`,
      `accelerator: "cuda"`, `detector_backend: "tensorrt"`
- [ ] With `--mock-ble`, the dashboard shows a "Synthetic sensors" banner even
      before any drill is started
- [ ] `detector.weights` / `pose.weights` point at `.engine` files (see the
      README for the export commands; rebuild after a JetPack upgrade)
- [ ] The same log line's "TSSTG sees the last 30 frames = ~N s" is noted —
      the checkpoint was trained on ~1.0 s clips, so a much larger span is a
      known accuracy gap, not a config error

## 5b. Scenario control panel (bench only)

Skip on a real-hardware pass — the panel needs `--mock-ble` and is disabled by
default. Use it to walk every tier without four restarts.

```yaml
monitoring:
  control_enabled: true
alerts:
  webhooks: []          # or a test endpoint. A Tier-2 button really dispatches.
```

```bash
python -m kineticpulse.main --config config.yaml --mock-ble --mock-stt --no-camera
```

- [ ] Log: `Scenario control surface is ENABLED ... Bench use only`
- [ ] `curl -s http://127.0.0.1:8790/control | jq .available` → `true`
- [ ] Dashboard `/control`: nine buttons, "Active scenario: Resting"
- [ ] Press **Trip fall** → tier reaches `tier_1_verify` within ~11 s
- [ ] Caregiver dashboard shows the amber **Drill in progress** banner
- [ ] Event feed carries a warning-severity `Drill: Trip fall` entry
- [ ] A Tier-2 button needs two presses (first arms, second dispatches)
- [ ] **Stop (back to resting)** clears the drill banner
- [ ] With `control_enabled: false`, `POST /control/scenario` → `403`

## 6. Pass criteria

| Check | Pass |
|-------|------|
| Wristband → Jetson TCP | hello + hr lines |
| Monitoring → dashboard | BPM changes live |
| Signaling → phone | sessions list |
| WebRTC | video on Galaxy |
| Tailscale | works off home Wi‑Fi |

Anything red above = still hypothesis, not a demo.
