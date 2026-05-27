import sys
import asyncio
import csv
import os
import math
from datetime import datetime
import queue
import threading
import pymongo

# ตั้งค่าการแสดงผลภาษาไทย (UTF-8) บน Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from orphe_core import Orphe

# =============================================================================
# คลาส OrpheCore — Wrapper ที่รวมข้อมูล Gait + Quat(Distance) + Gyro + Quat(Raw)
# =============================================================================
class OrpheCore:
    """
    Wrapper class ที่รวม 2 สตรีมข้อมูลจาก ORPHE CORE:
    
    1. Step Analysis (BLE Characteristic: step_analysis)
       - Gait Overview: ข้อมูลก้าวเดิน (step_count, stance/swing duration, calorie, distance)
       - Stride: ระยะก้าว (foot_angle, stride XYZ)
       - Pronation: การคว่ำเท้า (landing_impact, pronation XYZ)
       - QuatDistance (sub-header 4): Quaternion ของก้าว + ระยะทาง XYZ ต่อก้าว
         → ค่านี้ส่งมาเป็นรายก้าว (ทุกครั้งที่ก้าวเท้า)
    
    2. Sensor Values (BLE Characteristic: sensor_values)
       - Acc: ค่าดิบจากตัวตรวจจับความเร่ง (Accelerometer)
       - Gyro: ค่าดิบจากตัวตรวจจับความเร็วเชิงมุม (Gyroscope) — deg/s
       - Quat(Raw): ค่า Quaternion ดิบจากเซนเซอร์ (w, x, y, z)
         → ค่าพวกนี้ส่งมาด้วยอัตราสูง (50Hz หรือ 200Hz)
    """

    def __init__(self, address, side=None):
        self.address = address
        self.side = side  # "Left" หรือ "Right"
        self.orphe = Orphe()
        self.gait_callback = None
        self.sensor_callback = None
        self.steps = {}  # step_count -> {gait, stride, pronation, quat_distance}
        self.emitted_steps = set()

    async def connect(self):
        addr = None if self.address == "ใส่_ADDRESS_ของคุณตรงนี้" or not self.address else self.address
        try:
            success = await self.orphe.connect(addr)
        except Exception as e:
            print(f"ไม่สามารถเชื่อมต่อไปยังที่อยู่ {self.address} ได้: {e}")
            success = False

        if not success and addr is not None:
            print("กำลังพยายามสแกนค้นหาอุปกรณ์ ORPHE CORE รอบตัวคุณเพื่อเชื่อมต่ออัตโนมัติ...")
            try:
                success = await self.orphe.connect(None)
            except Exception as e:
                print(f"เกิดข้อผิดพลาดขณะสแกนค้นหาอุปกรณ์: {e}")
                success = False

        if success:
            await self.orphe.read_device_information()
        return success

    # ---- ลงทะเบียน Callback สำหรับข้อมูลก้าวเดิน (Step Analysis) ----
    def register_gait_callback(self, callback):
        """ลงทะเบียนฟังก์ชัน callback สำหรับข้อมูลก้าวเดิน (Gait + Stride + Pronation + QuatDistance)"""
        self.gait_callback = callback
        self.orphe.set_got_gait_callback(self._add_gait)
        self.orphe.set_got_stride_callback(self._add_stride)
        self.orphe.set_got_pronation_callback(self._add_pronation)
        self.orphe.set_got_quat_distance_callback(self._add_quat_distance)

    # ---- ลงทะเบียน Callback สำหรับเซนเซอร์ดิบ (Sensor Values) ----
    def register_sensor_callback(self, callback):
        """ลงทะเบียนฟังก์ชัน callback สำหรับข้อมูลเซนเซอร์ดิบ (Gyro + Quat + Acc)"""
        self.sensor_callback = callback
        self.orphe.set_got_converted_gyro_callback(self._on_gyro)
        self.orphe.set_got_quat_callback(self._on_quat)
        self.orphe.set_got_converted_acc_callback(self._on_acc)

    # ---- Internal: รวบรวมข้อมูลก้าวเดิน ----
    def _add_gait(self, gait):
        sc = gait.step_count
        if sc not in self.steps:
            self.steps[sc] = {}
        self.steps[sc]['gait'] = gait
        self._check_complete(sc)

    def _add_stride(self, stride):
        sc = stride.step_count
        if sc not in self.steps:
            self.steps[sc] = {}
        self.steps[sc]['stride'] = stride
        self._check_complete(sc)

    def _add_pronation(self, pronation):
        sc = pronation.step_count
        if sc not in self.steps:
            self.steps[sc] = {}
        self.steps[sc]['pronation'] = pronation
        self._check_complete(sc)

    def _add_quat_distance(self, quat_dist):
        sc = quat_dist.step_count
        if sc not in self.steps:
            self.steps[sc] = {}
        self.steps[sc]['quat_distance'] = quat_dist
        self._check_complete(sc)

    def _check_complete(self, sc):
        step = self.steps[sc]
        # Store one gait document per device side. QuatDistance can arrive late or
        # be missing on one shoe, so do not block MongoDB writes on that packet.
        if sc not in self.emitted_steps and all(k in step for k in ('gait', 'stride', 'pronation')):
            data = self._assemble_gait_data(
                step['gait'], step['stride'], step['pronation'], step.get('quat_distance')
            )
            if self.gait_callback:
                self.gait_callback(data)
            self.emitted_steps.add(sc)
            # ลบก้าวเก่า
            self.steps = {k: v for k, v in self.steps.items() if k > sc}
            self.emitted_steps = {k for k in self.emitted_steps if k >= sc - 50}

    def _assemble_gait_data(self, gait, stride, pronation, quat_dist=None):
        lr = self.orphe.device_information.lr if self.orphe.device_information else 0

        # ---- Stride ----
        stride_len = stride.y[0] * 100  # cm
        step_len = stride_len / 2
        strike_angle = stride.foot_angle[0]

        # ---- Gait Timing ----
        stance_dur = gait.standing_phase_duration[0]
        swing_dur = gait.swing_phase_duration[0]
        stride_time = stance_dur + swing_dur
        speed = (stride_len / 100) / stride_time if stride_time > 0 else 0

        # ---- Pronation & Impact ----
        impact = pronation.landing_impact[0] * 9.80665  # m/s²
        pronation_x = pronation.x[0]  # deg

        # ---- Scores ----
        cadence = 1.0 / stride_time if stride_time > 0 else 0
        prop_score = (speed * 30.0) + (cadence * 20.0)
        propulsion = max(20, min(100, round(prop_score / 20) * 20))
        abs_score = 120.0 - (impact * 1.5)
        absorption = max(20, min(100, round(abs_score / 20) * 20))

        side_name = self._get_device_side()
        is_left = (side_name.lower() == "left")
        device_addr = self.address or "Unknown"
        step_length_l = step_len if is_left else 0
        step_length_r = step_len if not is_left else 0
        strike_angle_l = strike_angle if is_left else 0
        strike_angle_r = strike_angle if not is_left else 0

        # ---- QuatDistance (Quaternion ของก้าวเดิน) ----
        # ค่านี้มาจาก sub-header 4 ของ Step Analysis
        # เป็น Quaternion ที่แสดงทิศทางเท้าขณะก้าว + ระยะเคลื่อนที่ต่อก้าว (XYZ distance)
        has_quat_distance = quat_dist is not None
        if has_quat_distance:
            qd_w = quat_dist.w[0]
            qd_x = quat_dist.x[0]
            qd_y = quat_dist.y[0]
            qd_z = quat_dist.z[0]
            qd_x_dist = quat_dist.x_distance[0]
            qd_y_dist = quat_dist.y_distance[0]
            qd_z_dist = quat_dist.z_distance[0]
        else:
            qd_w = qd_x = qd_y = qd_z = None
            qd_x_dist = qd_y_dist = qd_z_dist = None

        # ---- Gait Phase Info ----
        phase_names = ["None", "Stance", "Swing"]
        period_names = ["None", "LoadingResponse", "MidStance", "TerminalStance",
                        "InitialSwing", "MidSwing", "TerminalSwing"]
        event_names = ["None", "InitialContact", "FootFlat", "HeelRise",
                       "ToeOff", "FeetAdjacent", "TibiaVertical"]

        if has_quat_distance:
            phase = phase_names[quat_dist.phase] if quat_dist.phase < len(phase_names) else str(quat_dist.phase)
            period = period_names[quat_dist.period] if quat_dist.period < len(period_names) else str(quat_dist.period)
            event = event_names[quat_dist.event] if quat_dist.event < len(event_names) else str(quat_dist.event)
        else:
            phase = ""
            period = ""
            event = ""

        return {
            'step_count': gait.step_count,
            'side': side_name,
            'propulsion': propulsion,
            'consistency': 80,
            'symmetry': 100,
            'absorption': absorption,
            'speed': round(speed, 3),
            'device_address': device_addr,
            'step_length_l': round(step_length_l, 1),
            'step_length_r': round(step_length_r, 1),
            'strike_angle_l': round(strike_angle_l, 1),
            'strike_angle_r': round(strike_angle_r, 1),
            'stride_length_cm': round(stride_len, 1),
            'stance_duration_s': round(stance_dur, 3),
            'swing_duration_s': round(swing_dur, 3),
            'stride_time_s': round(stride_time, 3),
            'cadence_steps_per_s': round(cadence, 2),
            'landing_impact_ms2': round(impact, 2),
            'pronation_deg': round(pronation_x, 1),
            'gait_type': gait.gait_type,
            'direction': gait.direction,
            'has_quat_distance': has_quat_distance,
            # --- Quaternion ของก้าว (จาก Step Analysis sub-header 4) ---
            'quat_w': round(qd_w, 4) if has_quat_distance else None,
            'quat_x': round(qd_x, 4) if has_quat_distance else None,
            'quat_y': round(qd_y, 4) if has_quat_distance else None,
            'quat_z': round(qd_z, 4) if has_quat_distance else None,
            'x_distance': round(qd_x_dist, 4) if has_quat_distance else None,
            'y_distance': round(qd_y_dist, 4) if has_quat_distance else None,
            'z_distance': round(qd_z_dist, 4) if has_quat_distance else None,
            'phase': phase,
            'period': period,
            'event': event,
        }

    # ---- Helper: ดึงข้อมูลอุปกรณ์ ----
    def _get_device_side(self):
        if self.side is not None:
            return self.side
        lr = self.orphe.device_information.lr if self.orphe.device_information else 0
        return "Left" if lr == 0 else "Right"

    def _get_device_address(self):
        return self.address or "Unknown"

    # ---- Internal: ส่งข้อมูลเซนเซอร์ดิบ (Gyro, Quat, Acc) ----
    def _on_gyro(self, gyro):
        if self.sensor_callback:
            self.sensor_callback('gyro', {
                'device_side': self._get_device_side(),
                'device_address': self._get_device_address(),
                'x': round(gyro.x, 4),
                'y': round(gyro.y, 4),
                'z': round(gyro.z, 4),
                'timestamp': gyro.timestamp,
                'serial_number': gyro.serial_number,
                'packet_number': gyro.packet_number,
            })

    def _on_quat(self, quat):
        if self.sensor_callback:
            self.sensor_callback('quat', {
                'device_side': self._get_device_side(),
                'device_address': self._get_device_address(),
                'w': round(quat.w, 4),
                'x': round(quat.x, 4),
                'y': round(quat.y, 4),
                'z': round(quat.z, 4),
                'timestamp': quat.timestamp,
                'serial_number': quat.serial_number,
                'packet_number': quat.packet_number,
            })

    def _on_acc(self, acc):
        if self.sensor_callback:
            self.sensor_callback('acc', {
                'device_side': self._get_device_side(),
                'device_address': self._get_device_address(),
                'x': round(acc.x, 4),
                'y': round(acc.y, 4),
                'z': round(acc.z, 4),
                'timestamp': acc.timestamp,
                'serial_number': acc.serial_number,
                'packet_number': acc.packet_number,
            })

    # ---- เริ่ม/หยุดสตรีม ----
    async def start_streaming(self):
        """เริ่มทั้ง Step Analysis + Sensor Values Notification พร้อมกัน"""
        await self.orphe.start_step_analysis_notification()
        await self.orphe.start_sensor_values_notification()
        print("เริ่มสตรีมข้อมูลทั้ง Gait Analysis + Sensor Values (Gyro/Quat/Acc) แล้ว!")

    async def stop_streaming(self):
        """หยุดทั้ง 2 notification"""
        try:
            await self.orphe.stop_step_analysis_notification()
        except Exception:
            pass
        try:
            await self.orphe.stop_sensor_values_notification()
        except Exception:
            pass

    def is_connected(self):
        return self.orphe.is_connected()

    async def disconnect(self):
        await self.orphe.disconnect()


# =============================================================================
# ตั้งค่าอุปกรณ์ทั้ง 2 ข้าง (Left + Right)
# =============================================================================
DEVICE_ADDRESSES = [
    "F4:1B:9B:AF:20:5D",   # อุปกรณ์ตัวที่ 1
    "D0:A3:43:7D:01:BD",   # อุปกรณ์ตัวที่ 2
]

current_time = datetime.now().strftime("%Y%m%d_%H%M%S")

# ไฟล์ที่ 1: ข้อมูลก้าวเดิน (รายก้าว) — ประกอบด้วย Gait + Quat(Distance)
GAIT_CSV = f"gait_analysis_{current_time}.csv"
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
    # --- Quaternion ของก้าว (มาจาก Step Analysis sub-header 4) ---
    "Quat_W", "Quat_X", "Quat_Y", "Quat_Z",
    "X_Distance", "Y_Distance", "Z_Distance",
    "Phase", "Period", "Event"
]

# ไฟล์ที่ 2: ข้อมูลเซนเซอร์ดิบ (ความถี่สูง 50-200Hz) — Gyro + Quat(Raw) + Acc
SENSOR_CSV = f"sensor_raw_{current_time}.csv"
SENSOR_HEADERS = [
    "Timestamp", "Device_Side", "Device_Address",
    "Sensor_Timestamp_ms", "Serial_Number", "Packet_Number",
    "Sensor_Type",
    "W", "X", "Y", "Z"
]

# สร้างไฟล์และเขียน Header ทั้ง 2 ไฟล์
for fname, headers in [(GAIT_CSV, GAIT_HEADERS), (SENSOR_CSV, SENSOR_HEADERS)]:
    with open(fname, mode='w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(headers)
    print(f"สร้างไฟล์ {fname} เรียบร้อย ({len(headers)} คอลัมน์)")

# ตัวนับสำหรับแสดงสถานะ (รวมทั้ง 2 ข้าง)
sensor_count = {'gyro': 0, 'quat': 0, 'acc': 0}

# ตัวแปรเก็บเวลาล่าสุดที่บันทึกข้อมูลดิบเซนเซอร์แต่ละประเภทลง MongoDB (เพื่อเก็บทุกๆ 5 วินาที)
last_mongo_write_time = {}


# =============================================================================
# คลาส MongoWriter — สำหรับบันทึกข้อมูลลง MongoDB แบบ Time Series อย่างมีประสิทธิภาพ
# =============================================================================
class MongoWriter:
    def __init__(self, uri="mongodb://localhost:27017/", db_name="orphe_gait_db", 
                 gait_coll="gait_analysis", sensor_coll="sensor_raw"):
        self.uri = uri
        self.db_name = db_name
        self.gait_coll = gait_coll
        self.sensor_coll = sensor_coll
        self.queue = queue.Queue()
        self.running = False
        self.thread = None
        self.client = None
        self.db = None
        self.enabled = False

    def connect(self):
        try:
            # กำหนด serverSelectionTimeoutMS เพื่อไม่ให้รอนานหาก MongoDB ไม่ได้เปิดอยู่
            self.client = pymongo.MongoClient(self.uri, serverSelectionTimeoutMS=2000)
            self.client.server_info()  # ทดสอบการเชื่อมต่อ
            self.db = self.client[self.db_name]
            
            existing = self.db.list_collection_names()
            
            # สร้าง Time Series Collection หากยังไม่มี
            if self.gait_coll not in existing:
                self.db.create_collection(
                    self.gait_coll,
                    timeseries={
                        "timeField": "timestamp",
                        "metaField": "metadata",
                        "granularity": "seconds"
                    }
                )
                print(f"สร้าง Time Series Collection: {self.gait_coll} สำเร็จ")
                
            if self.sensor_coll not in existing:
                self.db.create_collection(
                    self.sensor_coll,
                    timeseries={
                        "timeField": "timestamp",
                        "metaField": "metadata",
                        "granularity": "seconds"
                    }
                )
                print(f"สร้าง Time Series Collection: {self.sensor_coll} สำเร็จ")
            
            self.enabled = True
            print("เชื่อมต่อ MongoDB สำเร็จ! บันทึกข้อมูลแบบ Time Series พร้อมทำงาน")
        except Exception as e:
            print(f"ไม่สามารถเชื่อมต่อ MongoDB ได้ ({e}) จะบันทึกไฟล์ CSV เท่านั้น")
            self.enabled = False

    def start(self):
        if not self.enabled:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        batch_gait = []
        batch_sensor = []
        batch_size = 100
        last_flush = datetime.now()

        while self.running or not self.queue.empty():
            try:
                item = self.queue.get(timeout=0.1)
            except queue.Empty:
                item = None

            if item:
                coll_name, doc = item
                if coll_name == self.gait_coll:
                    batch_gait.append(doc)
                elif coll_name == self.sensor_coll:
                    batch_sensor.append(doc)
                self.queue.task_done()

            # บันทึกลงฐานข้อมูลเมื่อมีข้อมูลครบจำนวน หรือเกินเวลาที่กำหนด (0.5 วินาที)
            time_since_flush = (datetime.now() - last_flush).total_seconds()
            
            if len(batch_gait) >= batch_size or time_since_flush >= 0.5:
                if batch_gait:
                    try:
                        self.db[self.gait_coll].insert_many(batch_gait)
                    except Exception as e:
                        print(f"เกิดข้อผิดพลาดขณะบันทึกข้อมูลลง MongoDB ({self.gait_coll}): {e}")
                    batch_gait.clear()

            if len(batch_sensor) >= batch_size or time_since_flush >= 0.5:
                if batch_sensor:
                    try:
                        self.db[self.sensor_coll].insert_many(batch_sensor)
                    except Exception as e:
                        print(f"เกิดข้อผิดพลาดขณะบันทึกข้อมูลลง MongoDB ({self.sensor_coll}): {e}")
                    batch_sensor.clear()

            if time_since_flush >= 0.5:
                last_flush = datetime.now()

        # บันทึกข้อมูลที่เหลือค้างในคิวหลังจากหยุดสตรีม
        if batch_gait:
            try:
                self.db[self.gait_coll].insert_many(batch_gait)
            except Exception as e:
                print(f"เกิดข้อผิดพลาดขณะบันทึกข้อมูลช่วงสุดท้ายลง MongoDB ({self.gait_coll}): {e}")
        if batch_sensor:
            try:
                self.db[self.sensor_coll].insert_many(batch_sensor)
            except Exception as e:
                print(f"เกิดข้อผิดพลาดขณะบันทึกข้อมูลช่วงสุดท้ายลง MongoDB ({self.sensor_coll}): {e}")

    def write_gait(self, doc):
        if self.enabled:
            self.queue.put((self.gait_coll, doc))

    def write_sensor(self, doc):
        if self.enabled:
            self.queue.put((self.sensor_coll, doc))

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass


# เริ่มต้นอินสแตนซ์สำหรับ MongoDB Writer
mongo_writer = MongoWriter()


# =============================================================================
# Callback: บันทึกข้อมูลก้าวเดิน (รายก้าว)
# =============================================================================
def handle_gait_analysis(data):
    try:
        now_dt = datetime.now()
        row = [
            now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            data['step_count'],
            data['side'],
            data['device_address'],
            data['speed'],
            data['step_length_l'],
            data['step_length_r'],
            data['stride_length_cm'],
            data['stride_time_s'],
            data['stance_duration_s'],
            data['swing_duration_s'],
            data['cadence_steps_per_s'],
            data['strike_angle_l'],
            data['strike_angle_r'],
            data['landing_impact_ms2'],
            data['pronation_deg'],
            data['propulsion'],
            data['absorption'],
            data['consistency'],
            data['symmetry'],
            data['gait_type'],
            data['direction'],
            data['quat_w'],
            data['quat_x'],
            data['quat_y'],
            data['quat_z'],
            data['x_distance'],
            data['y_distance'],
            data['z_distance'],
            data['phase'],
            data['period'],
            data['event'],
        ]

        with open(GAIT_CSV, mode='a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row)

        # บันทึกลง MongoDB แบบ Time Series
        gait_doc = {
            "timestamp": now_dt,
            "metadata": {
                "device_address": data['device_address'],
                "side": data['side']
            },
            "step_count": data['step_count'],
            "speed_m_s": data['speed'],
            "step_length_l_cm": data['step_length_l'],
            "step_length_r_cm": data['step_length_r'],
            "stride_length_cm": data['stride_length_cm'],
            "stride_time_s": data['stride_time_s'],
            "stance_duration_s": data['stance_duration_s'],
            "swing_duration_s": data['swing_duration_s'],
            "cadence_steps_per_s": data['cadence_steps_per_s'],
            "strike_angle_l_deg": data['strike_angle_l'],
            "strike_angle_r_deg": data['strike_angle_r'],
            "landing_impact_m_s2": data['landing_impact_ms2'],
            "pronation_deg": data['pronation_deg'],
            "propulsion": data['propulsion'],
            "absorption": data['absorption'],
            "consistency": data['consistency'],
            "symmetry": data['symmetry'],
            "gait_type": data['gait_type'],
            "direction": data['direction'],
            "has_quat_distance": data['has_quat_distance'],
            "quat_w": data['quat_w'],
            "quat_x": data['quat_x'],
            "quat_y": data['quat_y'],
            "quat_z": data['quat_z'],
            "x_distance": data['x_distance'],
            "y_distance": data['y_distance'],
            "z_distance": data['z_distance'],
            "phase": data['phase'],
            "period": data['period'],
            "event": data['event']
        }
        mongo_writer.write_gait(gait_doc)

        print(f"\n{'='*70}")
        print(f"  ก้าวที่ {data['step_count']} ({data['side']}) [{data['device_address']}]")
        print(f"  Speed: {data['speed']} m/s | Stride: {data['stride_length_cm']} cm")
        if data['has_quat_distance']:
            print(f"  Quat(Step): W={data['quat_w']}, X={data['quat_x']}, Y={data['quat_y']}, Z={data['quat_z']}")
            print(f"  Distance: X={data['x_distance']}, Y={data['y_distance']}, Z={data['z_distance']}")
            print(f"  Phase: {data['phase']} | Period: {data['period']} | Event: {data['event']}")
        else:
            print("  Quat(Step): ยังไม่ได้รับ quat_distance สำหรับก้าวนี้")
        print(f"  Impact: {data['landing_impact_ms2']} m/s² | Pronation: {data['pronation_deg']}°")
        if mongo_writer.enabled:
            print("  [MongoDB] บันทึกลง Time Series Collection สำเร็จ")
        print(f"{'='*70}")

    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการบันทึก Gait: {e}")


# =============================================================================
# Callback: บันทึกข้อมูลเซนเซอร์ดิบ (ความถี่สูง)
# =============================================================================
def handle_sensor_data(sensor_type, data):
    """
    sensor_type: 'gyro', 'quat', หรือ 'acc'
    data: dict ที่มี x, y, z (และ w สำหรับ quat), timestamp, serial_number, packet_number
    """
    try:
        now_dt = datetime.now()
        w_val = data.get('w', '')  # มีเฉพาะใน quat เท่านั้น
        row = [
            now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            data['device_side'],
            data['device_address'],
            data['timestamp'],
            data['serial_number'],
            data['packet_number'],
            sensor_type.upper(),
            w_val,
            data['x'],
            data['y'],
            data['z'],
        ]

        with open(SENSOR_CSV, mode='a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row)

        sensor_count[sensor_type] += 1

        # บันทึกลง MongoDB แบบ Time Series (เก็บทุกๆ 5 วินาทีต่อประเภทเซนเซอร์ของแต่ละข้าง)
        key = (data['device_side'], sensor_type.upper())
        last_time = last_mongo_write_time.get(key)
        if last_time is None or (now_dt - last_time).total_seconds() >= 5.0:
            sensor_doc = {
                "timestamp": now_dt,
                "metadata": {
                    "device_address": data['device_address'],
                    "side": data['device_side'],
                    "sensor_type": sensor_type.upper()
                },
                "sensor_timestamp_ms": data['timestamp'],
                "serial_number": data['serial_number'],
                "packet_number": data['packet_number'],
                "x": data['x'],
                "y": data['y'],
                "z": data['z']
            }
            if w_val != '':
                sensor_doc["w"] = w_val
            
            mongo_writer.write_sensor(sensor_doc)
            last_mongo_write_time[key] = now_dt

        # แสดงสถานะทุก 200 แพ็กเก็ต เพื่อไม่ให้คอนโซลท่วม
        total = sum(sensor_count.values())
        if total % 200 == 0:
            mongo_status = " | MongoDB Active" if mongo_writer.enabled else " | MongoDB Offline"
            print(f"  [Sensor ทั้ง 2 ข้าง] Gyro: {sensor_count['gyro']} | "
                  f"Quat: {sensor_count['quat']} | "
                  f"Acc: {sensor_count['acc']} แพ็กเก็ต{mongo_status}")

    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการบันทึก Sensor ({sensor_type}): {e}")


# =============================================================================
# Main — เชื่อมต่อ ORPHE CORE 2 ข้าง (Left + Right) พร้อมกัน
# =============================================================================
async def main():
    devices = []

    # เริ่มต้นเชื่อมต่อ MongoDB และรัน thread writer
    mongo_writer.connect()
    mongo_writer.start()

    try:
        print(f"กำลังเชื่อมต่อไปยัง ORPHE CORE ทั้ง {len(DEVICE_ADDRESSES)} เครื่อง...")

        for i, addr in enumerate(DEVICE_ADDRESSES):
            side = "Left" if i == 0 else "Right"
            device = OrpheCore(addr, side=side)
            label = f"ตัวที่ {i+1} ({addr})"

            try:
                success = await device.connect()
            except Exception as e:
                print(f"ไม่สามารถเชื่อมต่อ {label} ได้: {e}")
                success = False

            # หากเชื่อมต่อด้วย address ตรงไม่สำเร็จ ให้สแกนหาอุปกรณ์อัตโนมัติ
            if not success:
                print(f"กำลังสแกนค้นหาอุปกรณ์ {label} โดยอัตโนมัติ...")
                device_scan = OrpheCore(None, side=side)
                try:
                    success = await device_scan.connect()
                except Exception as e:
                    print(f"เกิดข้อผิดพลาดขณะสแกน: {e}")
                    success = False
                if success:
                    device = device_scan

            if not success:
                print(f"เชื่อมต่อ {label} ล้มเหลว!")
                return

            print(f"เชื่อมต่อ {label} สำเร็จ! → {side} shoe")
            devices.append(device)

        # ลงทะเบียน callback ให้ทุกอุปกรณ์ (ข้อมูลจะถูกรวมลงไฟล์เดียวกัน แยกด้วย Device_Side)
        for device in devices:
            device.register_gait_callback(handle_gait_analysis)
            device.register_sensor_callback(handle_sensor_data)

        print(f"\nไฟล์ข้อมูลก้าวเดิน (รายก้าว): {GAIT_CSV}")
        print(f"ไฟล์เซนเซอร์ดิบ (ความถี่สูง):  {SENSOR_CSV}")
        print(f"\nเชื่อมต่อสำเร็จทั้ง {len(devices)} เครื่อง!")
        print("เริ่มดึงค่าจากเซนเซอร์... กรุณาสวมรองเท้าแล้วลองเดิน/วิ่ง")
        print("กด Ctrl+C เพื่อหยุดและบันทึกไฟล์\n")

        # เริ่มสตรีมทุกเครื่องพร้อมกัน
        await asyncio.gather(*(device.start_streaming() for device in devices))

        while True:
            await asyncio.sleep(1)
            # ตรวจสอบว่าอุปกรณ์ทุกตัวยังเชื่อมต่ออยู่
            for i, device in enumerate(devices):
                if not device.is_connected():
                    print(f"\nอุปกรณ์ตัวที่ {i+1} หลุดการเชื่อมต่อ!")
                    return  # จะไปทำงานใน finally block เพื่อทำความสะอาด
    finally:
        print("\nกำลังตัดการเชื่อมต่อทุกเครื่อง...")
        for device in devices:
            if device.is_connected():
                try:
                    await device.stop_streaming()
                except Exception as e:
                    print(f"  ไม่สามารถหยุด streaming: {e}")
                await device.disconnect()

        # หยุดการเชื่อมต่อและเขียนข้อมูลของ MongoDB
        mongo_writer.stop()

        print(f"\n{'='*70}")
        print(f"  สรุปผลการเก็บข้อมูล (ทั้ง {len(devices)} เครื่อง)")
        print(f"  Gyro: {sensor_count['gyro']} แพ็กเก็ต")
        print(f"  Quat: {sensor_count['quat']} แพ็กเก็ต")
        print(f"  Acc:  {sensor_count['acc']} แพ็กเก็ต")
        if mongo_writer.enabled:
            print("  MongoDB: บันทึกข้อมูลแบบ Time Series เสร็จสิ้น")
        print(f"  บันทึกลง: {GAIT_CSV} และ {SENSOR_CSV}")
        print(f"{'='*70}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\nหยุดระบบ บันทึกไฟล์เสร็จสิ้น")
        print(f"  ไฟล์ก้าวเดิน: {GAIT_CSV}")
        print(f"  ไฟล์เซนเซอร์: {SENSOR_CSV}")
