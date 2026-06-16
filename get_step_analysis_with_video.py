import sys

# Force MTA threading model on Windows COM to prevent Bleak callback issues with OpenCV/GUI threads
if sys.platform == 'win32':
    sys.coinit_flags = 0
    try:
        from bleak.backends.winrt.util import allow_sta
        allow_sta()
    except ImportError:
        pass

import asyncio
import csv
import os
import math
import threading
import queue
from datetime import datetime
import pymongo
import cv2

# ตั้งค่าการแสดงผลภาษาไทย (UTF-8) บน Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from orphe_core import Orphe

# =============================================================================
# ตั้งค่าอุปกรณ์
# =============================================================================
DEVICE_ADDRESSES = [
    "F4:1B:9B:AF:20:5D",
    "D0:A3:43:7D:01:BD",
]

current_time = datetime.now().strftime("%Y%m%d_%H%M%S")

GAIT_CSV    = f"gait_analysis_{current_time}.csv"
SENSOR_CSV  = f"sensor_raw_{current_time}.csv"
VIDEO_FILE  = f"recording_{current_time}.mp4"

GAIT_HEADERS = [
    "Timestamp", "Step_Count", "Device_Side", "Device_Address",
    "Speed_m/s", "Step_Length_L_cm", "Step_Length_R_cm",
    "Stride_Length_cm", "Stride_Time_s",
    "Stance_Duration_s", "Swing_Duration_s",
    "Cadence_steps/s",
    "Strike_Angle_L_deg", "Strike_Angle_R_deg",
    "Landing_Impact_m/s²", "Pronation_deg",
    "Propulsion", "Absorption", "Consistency", "Symmetry",
    "Gait_Type", "Direction",
    "Quat_W", "Quat_X", "Quat_Y", "Quat_Z",
    "X_Distance", "Y_Distance", "Z_Distance",
    "Phase", "Period", "Event"
]

SENSOR_HEADERS = [
    "Timestamp", "Device_Side", "Device_Address",
    "Sensor_Timestamp_ms", "Serial_Number", "Packet_Number",
    "Sensor_Type",
    "W", "X", "Y", "Z"
]

# (ไฟล์ CSV จะถูกสร้างหลังจากเชื่อมต่ออุปกรณ์สำเร็จใน main)

sensor_count = {'gyro': 0, 'quat': 0, 'acc': 0}
last_mongo_write_time = {}


# =============================================================================
# คลาส VideoRecorder — บันทึกวิดีโอจากกล้องแบบ Thread แยก
# =============================================================================
class VideoRecorder:
    def __init__(self, filename, camera_index=0, fps=30.0, resolution=(1280, 720)):
        self.filename = filename
        self.camera_index = camera_index
        self.fps = fps
        self.resolution = resolution
        self.running = False
        self.thread = None
        self.cap = None
        self.writer = None

    def start(self):
        if sys.platform == 'win32':
            self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        else:
            self.cap = cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            print(f"ไม่สามารถเปิดกล้อง (index={self.camera_index}) ได้ — ข้ามการบันทึกวิดีโอ")
            return False

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.resolution[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self.writer = cv2.VideoWriter(
            self.filename, fourcc, self.fps,
            (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
             int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        )
        if not self.writer.isOpened():
            print("ไม่สามารถสร้างไฟล์วิดีโอได้")
            return False

        self.running = True
        self.thread = threading.Thread(target=self._record_loop, daemon=True)
        self.thread.start()
        print(f"เริ่มบันทึกวิดีโอ → {self.filename}")
        return True

    def _record_loop(self):
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                print("อ่านเฟรมจากกล้องไม่ได้ — หยุดบันทึกวิดีโอ")
                break
            # ประทับเวลาบนเฟรม
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            cv2.putText(frame, ts, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            self.writer.write(frame)

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        if self.writer:
            self.writer.release()
        if self.cap:
            self.cap.release()
        print(f"บันทึกวิดีโอเสร็จสิ้น → {self.filename}")


# =============================================================================
# คลาส OrpheCore
# =============================================================================
class OrpheCore:
    def __init__(self, address, side=None):
        self.address = address
        self.side = side
        self.orphe = Orphe()
        self.gait_callback = None
        self.sensor_callback = None
        self.steps = {}
        self.emitted_steps = set()

    async def connect(self):
        addr = None if not self.address or self.address == "ใส่_ADDRESS_ของคุณตรงนี้" else self.address
        try:
            success = await self.orphe.connect(addr)
        except Exception as e:
            print(f"เชื่อมต่อ {self.address} ไม่ได้: {e}")
            success = False

        if not success and addr is not None:
            print("สแกนหาอุปกรณ์อัตโนมัติ...")
            try:
                success = await self.orphe.connect(None)
            except Exception as e:
                print(f"สแกนล้มเหลว: {e}")
                success = False

        if success:
            await self.orphe.read_device_information()
        return success

    def register_gait_callback(self, cb):
        self.gait_callback = cb
        self.orphe.set_got_gait_callback(self._add_gait)
        self.orphe.set_got_stride_callback(self._add_stride)
        self.orphe.set_got_pronation_callback(self._add_pronation)
        self.orphe.set_got_quat_distance_callback(self._add_quat_distance)

    def register_sensor_callback(self, cb):
        self.sensor_callback = cb
        self.orphe.set_got_converted_gyro_callback(self._on_gyro)
        self.orphe.set_got_quat_callback(self._on_quat)
        self.orphe.set_got_converted_acc_callback(self._on_acc)

    def _add_gait(self, gait):
        sc = gait.step_count
        self.steps.setdefault(sc, {})['gait'] = gait
        self._check_complete(sc)

    def _add_stride(self, stride):
        sc = stride.step_count
        self.steps.setdefault(sc, {})['stride'] = stride
        self._check_complete(sc)

    def _add_pronation(self, pronation):
        sc = pronation.step_count
        self.steps.setdefault(sc, {})['pronation'] = pronation
        self._check_complete(sc)

    def _add_quat_distance(self, qd):
        sc = qd.step_count
        self.steps.setdefault(sc, {})['quat_distance'] = qd
        self._check_complete(sc)

    def _check_complete(self, sc):
        step = self.steps[sc]
        if sc not in self.emitted_steps and all(k in step for k in ('gait', 'stride', 'pronation')):
            data = self._assemble(step['gait'], step['stride'], step['pronation'], step.get('quat_distance'))
            if self.gait_callback:
                self.gait_callback(data)
            self.emitted_steps.add(sc)
            self.steps = {k: v for k, v in self.steps.items() if k > sc}
            self.emitted_steps = {k for k in self.emitted_steps if k >= sc - 50}

    def _assemble(self, gait, stride, pronation, qd=None):
        side_name = self._get_side()
        is_left = side_name.lower() == "left"

        stride_len   = stride.y[0] * 100
        step_len     = stride_len / 2
        strike_angle = stride.foot_angle[0]
        stance_dur   = gait.standing_phase_duration[0]
        swing_dur    = gait.swing_phase_duration[0]
        stride_time  = stance_dur + swing_dur
        speed        = (stride_len / 100) / stride_time if stride_time > 0 else 0
        impact       = pronation.landing_impact[0] * 9.80665
        pronation_x  = pronation.x[0]
        cadence      = 1.0 / stride_time if stride_time > 0 else 0
        propulsion   = max(20, min(100, round((speed * 30 + cadence * 20) / 20) * 20))
        absorption   = max(20, min(100, round((120 - impact * 1.5) / 20) * 20))

        has_qd = qd is not None
        phase_names  = ["None", "Stance", "Swing"]
        period_names = ["None", "LoadingResponse", "MidStance", "TerminalStance",
                        "InitialSwing", "MidSwing", "TerminalSwing"]
        event_names  = ["None", "InitialContact", "FootFlat", "HeelRise",
                        "ToeOff", "FeetAdjacent", "TibiaVertical"]

        return {
            'step_count': gait.step_count,
            'side': side_name,
            'device_address': self.address or "Unknown",
            'speed': round(speed, 3),
            'step_length_l': round(step_len if is_left else 0, 1),
            'step_length_r': round(step_len if not is_left else 0, 1),
            'stride_length_cm': round(stride_len, 1),
            'stride_time_s': round(stride_time, 3),
            'stance_duration_s': round(stance_dur, 3),
            'swing_duration_s': round(swing_dur, 3),
            'cadence_steps_per_s': round(cadence, 2),
            'strike_angle_l': round(strike_angle if is_left else 0, 1),
            'strike_angle_r': round(strike_angle if not is_left else 0, 1),
            'landing_impact_ms2': round(impact, 2),
            'pronation_deg': round(pronation_x, 1),
            'propulsion': propulsion,
            'absorption': absorption,
            'consistency': 80,
            'symmetry': 100,
            'gait_type': gait.gait_type,
            'direction': gait.direction,
            'has_quat_distance': has_qd,
            'quat_w': round(qd.w[0], 4) if has_qd else None,
            'quat_x': round(qd.x[0], 4) if has_qd else None,
            'quat_y': round(qd.y[0], 4) if has_qd else None,
            'quat_z': round(qd.z[0], 4) if has_qd else None,
            'x_distance': round(qd.x_distance[0], 4) if has_qd else None,
            'y_distance': round(qd.y_distance[0], 4) if has_qd else None,
            'z_distance': round(qd.z_distance[0], 4) if has_qd else None,
            'phase':  (phase_names[qd.phase]   if qd.phase   < len(phase_names)   else str(qd.phase))   if has_qd else "",
            'period': (period_names[qd.period]  if qd.period  < len(period_names)  else str(qd.period))  if has_qd else "",
            'event':  (event_names[qd.event]    if qd.event   < len(event_names)   else str(qd.event))   if has_qd else "",
        }

    def _get_side(self):
        if self.side:
            return self.side
        lr = self.orphe.device_information.lr if self.orphe.device_information else 0
        return "Left" if lr == 0 else "Right"

    def _on_gyro(self, gyro):
        if self.sensor_callback:
            self.sensor_callback('gyro', {
                'device_side': self._get_side(), 'device_address': self.address or "Unknown",
                'x': round(gyro.x, 4), 'y': round(gyro.y, 4), 'z': round(gyro.z, 4),
                'timestamp': gyro.timestamp, 'serial_number': gyro.serial_number,
                'packet_number': gyro.packet_number,
            })

    def _on_quat(self, quat):
        if self.sensor_callback:
            self.sensor_callback('quat', {
                'device_side': self._get_side(), 'device_address': self.address or "Unknown",
                'w': round(quat.w, 4), 'x': round(quat.x, 4),
                'y': round(quat.y, 4), 'z': round(quat.z, 4),
                'timestamp': quat.timestamp, 'serial_number': quat.serial_number,
                'packet_number': quat.packet_number,
            })

    def _on_acc(self, acc):
        if self.sensor_callback:
            self.sensor_callback('acc', {
                'device_side': self._get_side(), 'device_address': self.address or "Unknown",
                'x': round(acc.x, 4), 'y': round(acc.y, 4), 'z': round(acc.z, 4),
                'timestamp': acc.timestamp, 'serial_number': acc.serial_number,
                'packet_number': acc.packet_number,
            })

    async def start_streaming(self):
        await self.orphe.start_step_analysis_notification()
        await self.orphe.start_sensor_values_notification()
        print(f"เริ่มสตรีม ({self._get_side()}) สำเร็จ")

    async def stop_streaming(self):
        for fn in [self.orphe.stop_step_analysis_notification,
                   self.orphe.stop_sensor_values_notification]:
            try:
                await fn()
            except Exception:
                pass

    def is_connected(self):
        return self.orphe.is_connected()

    async def disconnect(self):
        await self.orphe.disconnect()


# =============================================================================
# MongoWriter
# =============================================================================
class MongoWriter:
    def __init__(self, uri="mongodb://localhost:27017/", db_name="orphe_gait_db",
                 gait_coll="gait_analysis", sensor_coll="sensor_raw"):
        self.uri = uri; self.db_name = db_name
        self.gait_coll = gait_coll; self.sensor_coll = sensor_coll
        self.queue = queue.Queue(); self.running = False
        self.thread = None; self.client = None; self.db = None; self.enabled = False

    def connect(self):
        try:
            self.client = pymongo.MongoClient(self.uri, serverSelectionTimeoutMS=2000)
            self.client.server_info()
            self.db = self.client[self.db_name]
            existing = self.db.list_collection_names()
            for coll in [self.gait_coll, self.sensor_coll]:
                if coll not in existing:
                    self.db.create_collection(coll, timeseries={
                        "timeField": "timestamp", "metaField": "metadata", "granularity": "seconds"
                    })
                    print(f"สร้าง Time Series Collection: {coll}")
            self.enabled = True
            print("เชื่อมต่อ MongoDB สำเร็จ!")
        except Exception as e:
            print(f"MongoDB ไม่พร้อมใช้งาน ({e}) — บันทึก CSV เท่านั้น")
            self.enabled = False

    def start(self):
        if not self.enabled:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        bg, bs, sz, last = [], [], 100, datetime.now()
        while self.running or not self.queue.empty():
            try:
                coll, doc = self.queue.get(timeout=0.1)
                (bg if coll == self.gait_coll else bs).append(doc)
                self.queue.task_done()
            except queue.Empty:
                pass
            elapsed = (datetime.now() - last).total_seconds()
            for batch, coll in [(bg, self.gait_coll), (bs, self.sensor_coll)]:
                if batch and (len(batch) >= sz or elapsed >= 0.5):
                    try:
                        self.db[coll].insert_many(batch)
                    except Exception as e:
                        print(f"MongoDB write error ({coll}): {e}")
                    batch.clear()
            if elapsed >= 0.5:
                last = datetime.now()
        for batch, coll in [(bg, self.gait_coll), (bs, self.sensor_coll)]:
            if batch:
                try:
                    self.db[coll].insert_many(batch)
                except Exception as e:
                    print(f"MongoDB final write error ({coll}): {e}")

    def write_gait(self, doc):
        if self.enabled: self.queue.put((self.gait_coll, doc))

    def write_sensor(self, doc):
        if self.enabled: self.queue.put((self.sensor_coll, doc))

    def stop(self):
        self.running = False
        if self.thread: self.thread.join()
        if self.client:
            try: self.client.close()
            except Exception: pass


mongo_writer = MongoWriter()


# =============================================================================
# Callbacks
# =============================================================================
def handle_gait_analysis(data):
    try:
        now_dt = datetime.now()
        row = [
            now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            data['step_count'], data['side'], data['device_address'],
            data['speed'], data['step_length_l'], data['step_length_r'],
            data['stride_length_cm'], data['stride_time_s'],
            data['stance_duration_s'], data['swing_duration_s'],
            data['cadence_steps_per_s'],
            data['strike_angle_l'], data['strike_angle_r'],
            data['landing_impact_ms2'], data['pronation_deg'],
            data['propulsion'], data['absorption'], data['consistency'], data['symmetry'],
            data['gait_type'], data['direction'],
            data['quat_w'], data['quat_x'], data['quat_y'], data['quat_z'],
            data['x_distance'], data['y_distance'], data['z_distance'],
            data['phase'], data['period'], data['event'],
        ]
        with open(GAIT_CSV, mode='a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row)

        mongo_writer.write_gait({
            "timestamp": now_dt,
            "metadata": {"device_address": data['device_address'], "side": data['side']},
            **{k: data[k] for k in [
                'step_count', 'speed', 'step_length_l', 'step_length_r',
                'stride_length_cm', 'stride_time_s', 'stance_duration_s', 'swing_duration_s',
                'cadence_steps_per_s', 'strike_angle_l', 'strike_angle_r',
                'landing_impact_ms2', 'pronation_deg', 'propulsion', 'absorption',
                'consistency', 'symmetry', 'gait_type', 'direction', 'has_quat_distance',
                'quat_w', 'quat_x', 'quat_y', 'quat_z',
                'x_distance', 'y_distance', 'z_distance', 'phase', 'period', 'event'
            ]}
        })

        print(f"\n{'='*60}")
        print(f"  ก้าวที่ {data['step_count']} ({data['side']}) [{data['device_address']}]")
        print(f"  Speed: {data['speed']} m/s | Stride: {data['stride_length_cm']} cm")
        if data['has_quat_distance']:
            print(f"  Quat: W={data['quat_w']} X={data['quat_x']} Y={data['quat_y']} Z={data['quat_z']}")
            print(f"  Dist: X={data['x_distance']} Y={data['y_distance']} Z={data['z_distance']}")
            print(f"  Phase: {data['phase']} | Period: {data['period']} | Event: {data['event']}")
        print(f"  Impact: {data['landing_impact_ms2']} m/s² | Pronation: {data['pronation_deg']}°")
        print(f"{'='*60}")
    except Exception as e:
        print(f"Gait error: {e}")


def handle_sensor_data(sensor_type, data):
    try:
        now_dt = datetime.now()
        w_val = data.get('w', '')
        row = [
            now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            data['device_side'], data['device_address'],
            data['timestamp'], data['serial_number'], data['packet_number'],
            sensor_type.upper(), w_val, data['x'], data['y'], data['z'],
        ]
        with open(SENSOR_CSV, mode='a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row)

        sensor_count[sensor_type] += 1

        key = (data['device_side'], sensor_type.upper())
        last_t = last_mongo_write_time.get(key)
        if last_t is None or (now_dt - last_t).total_seconds() >= 5.0:
            doc = {
                "timestamp": now_dt,
                "metadata": {"device_address": data['device_address'],
                             "side": data['device_side'], "sensor_type": sensor_type.upper()},
                "sensor_timestamp_ms": data['timestamp'],
                "serial_number": data['serial_number'],
                "packet_number": data['packet_number'],
                "x": data['x'], "y": data['y'], "z": data['z'],
            }
            if w_val != '':
                doc['w'] = w_val
            mongo_writer.write_sensor(doc)
            last_mongo_write_time[key] = now_dt

        total = sum(sensor_count.values())
        if total % 200 == 0:
            mongo_status = " | MongoDB Active" if mongo_writer.enabled else " | MongoDB Offline"
            print(f"  [Sensor] Gyro:{sensor_count['gyro']} Quat:{sensor_count['quat']} Acc:{sensor_count['acc']}{mongo_status}")
    except Exception as e:
        print(f"Sensor error ({sensor_type}): {e}")


# =============================================================================
# Main
# =============================================================================
async def main():
    global GAIT_CSV, SENSOR_CSV, VIDEO_FILE
    devices = []
    recorder = None
    video_ok = False

    try:
        print(f"กำลังเชื่อมต่อ ORPHE CORE {len(DEVICE_ADDRESSES)} เครื่อง...")

        for i, addr in enumerate(DEVICE_ADDRESSES):
            side = "Left" if i == 0 else "Right"
            device = OrpheCore(addr, side=side)
            label = f"ตัวที่ {i+1} ({addr})"

            try:
                success = await device.connect()
            except Exception as e:
                print(f"เชื่อมต่อ {label} ไม่ได้: {e}")
                success = False

            if not success:
                print(f"สแกนหา {label} อัตโนมัติ...")
                device_scan = OrpheCore(None, side=side)
                try:
                    success = await device_scan.connect()
                    if success:
                        device = device_scan
                except Exception as e:
                    print(f"สแกนล้มเหลว: {e}")
                    success = False

            if not success:
                print(f"เชื่อมต่อ {label} ล้มเหลว!")
                return

            print(f"เชื่อมต่อ {label} สำเร็จ → {side} shoe")
            devices.append(device)

        # เมื่อเชื่อมต่อสำเร็จทุกเครื่องแล้ว ค่อยสร้างไฟล์และเริ่มบันทึก
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        GAIT_CSV    = f"gait_analysis_{current_time}.csv"
        SENSOR_CSV  = f"sensor_raw_{current_time}.csv"
        VIDEO_FILE  = f"recording_{current_time}.mp4"

        # สร้างไฟล์ CSV และเขียน header
        for fname, headers in [(GAIT_CSV, GAIT_HEADERS), (SENSOR_CSV, SENSOR_HEADERS)]:
            with open(fname, mode='w', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow(headers)
            print(f"สร้างไฟล์ {fname} เรียบร้อย ({len(headers)} คอลัมน์)")

        # เชื่อมต่อ MongoDB
        mongo_writer.connect()
        mongo_writer.start()

        # เริ่มบันทึกวิดีโอ
        recorder = VideoRecorder(VIDEO_FILE, camera_index=0, fps=30.0, resolution=(1280, 720))
        video_ok = recorder.start()

        for device in devices:
            device.register_gait_callback(handle_gait_analysis)
            device.register_sensor_callback(handle_sensor_data)

        print(f"\nไฟล์ก้าวเดิน : {GAIT_CSV}")
        print(f"ไฟล์เซนเซอร์ : {SENSOR_CSV}")
        print(f"ไฟล์วิดีโอ   : {VIDEO_FILE if video_ok else '(ไม่สามารถบันทึกได้)'}")
        print("กด Ctrl+C เพื่อหยุด\n")

        await asyncio.gather(*(device.start_streaming() for device in devices))

        while True:
            await asyncio.sleep(1)
            for i, device in enumerate(devices):
                if not device.is_connected():
                    print(f"\nอุปกรณ์ตัวที่ {i+1} หลุดการเชื่อมต่อ!")
                    return

    finally:
        print("\nกำลังตัดการเชื่อมต่อ...")
        for device in devices:
            if device.is_connected():
                try:
                    await device.stop_streaming()
                except Exception:
                    pass
                await device.disconnect()

        mongo_writer.stop()
        if recorder:
            recorder.stop()

        print(f"\n{'='*60}")
        print(f"  สรุปผล ({len(devices)} เครื่อง)")
        print(f"  Gyro: {sensor_count['gyro']} | Quat: {sensor_count['quat']} | Acc: {sensor_count['acc']} แพ็กเก็ต")
        if mongo_writer.enabled:
            print("  MongoDB: บันทึกแบบ Time Series เสร็จสิ้น")
        print(f"  ไฟล์ก้าวเดิน : {GAIT_CSV}")
        print(f"  ไฟล์เซนเซอร์ : {SENSOR_CSV}")
        print(f"  ไฟล์วิดีโอ   : {VIDEO_FILE if video_ok else '(ไม่ได้บันทึก)'}")
        print(f"{'='*60}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\nหยุดระบบ")
        print(f"  ไฟล์ก้าวเดิน : {GAIT_CSV}")
        print(f"  ไฟล์เซนเซอร์ : {SENSOR_CSV}")
        print(f"  ไฟล์วิดีโอ   : {VIDEO_FILE}")
