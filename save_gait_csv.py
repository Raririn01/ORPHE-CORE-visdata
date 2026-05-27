import sys
import asyncio
import csv
import datetime
import math
import os
import time
from orphe_core import Orphe

# ตั้งค่าระบบการแสดงผลภาษาไทย (UTF-8) บน Windows console และ event loop สำหรับ Bleak
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ลิสต์คอลัมน์ทั้งหมด 59 รายการตามมาตรฐาน ORPHE
CSV_HEADERS = [
    "01. Date and time of measurement",
    "02. Time [minutes 'seconds' frame]",
    "03. Speed [m/sec]",
    "04. Step length [cm]",
    "05. Vertical height_left [cm]",
    "06. Vertical height_right [cm]",
    "07. Vertical height_average left and right [cm]",
    "08. Swing width_left [cm]",
    "09. Swing width_right [cm]",
    "10. Swing width_average left and right [cm]",
    "11. Stance phase_left [%]",
    "12. Stance phase_right [%]",
    "13. Stance phase_average left and right [%]",
    "14. Double support phase [%]",
    "15. Gait Rhythm [%]",
    "16. Strike angle_left [°]",
    "17. Strike angle_right [°]",
    "18. Strike angle_average left and right [°]",
    "19. Toe-off angle_left [°]",
    "20. Toe-off angle_right [°]",
    "21. Toe-off angle_average left and right [°]",
    "22. Pronation_left [°]",
    "23. Pronation_right [°]",
    "24. Pronation_average left and right [°]",
    "25. Progression angle_left [°]",
    "26. Progression angle_right [°]",
    "27. Progression angle_average left and right [°]",
    "28. Landing impact_left [m/s²]",
    "29. Landing impact_right [m/s²]",
    "30. Landing impact_average left and right [m/s²]",
    "31. Estimated distance [m]",
    "32. Swing phase_left [%]",
    "33. Swing phase_right [%]",
    "34. Swing phase_average left and right [%]",
    "35. Single support phase_left [%]",
    "36. Single support phase_right [%]",
    "37. Single support phase_average left and right [%]",
    "38. Load balance (left-right ratio of single-leg support time) [%]",
    "39. Landing impact (value of the larger landing impact, left or right) [m/s²]",
    "40. Stride length_left [cm]",
    "41. Stride length_right [cm]",
    "42. Stride length_average of right and left [cm]",
    "43. Cadence_left [steps/s]",
    "44. Cadence_right [steps/s]",
    "45. Cadence_average left and right [steps/s]",
    "46. Stride time_left [s]",
    "47. Stride time_right [s]",
    "48. Stride time_average left and right [s]",
    "49. Stance phase duration_left [s]",
    "50. Stance phase duration_right [s]",
    "51. Stance phase duration_average left and right [s]",
    "52. Swing phase duration_left [s]",
    "53. Swing phase duration_right [s]",
    "54. Swing phase duration_average left and right [s]",
    "55. Propulsion [0-100]",
    "56. Consistency [0-100]",
    "57. Symmetry [0-100]",
    "58. Absorption [0-100]",
    "59. Memo"
]

# คลาสช่วยรวมข้อมูลแต่ละก้าวให้สมบูรณ์ (เนื่องจากข้อมูล Gait, Stride, Pronation จะถูกส่งแยกแพ็กเก็ตกันมาตามรอบก้าว)
class StepAssembler:
    def __init__(self, side, on_step_complete_cb):
        self.side = side
        self.on_step_complete_cb = on_step_complete_cb
        self.steps = {} # step_count -> {gait: ..., stride: ..., pronation: ...}

    def add_gait(self, gait):
        sc = gait.step_count
        print(f"[{self.side.upper()}] ได้รับข้อมูล Gait (ก้าวที่ {sc})")
        if sc not in self.steps:
            self.steps[sc] = {}
        self.steps[sc]['gait'] = gait
        self._check_complete(sc)

    def add_stride(self, stride):
        sc = stride.step_count
        print(f"[{self.side.upper()}] ได้รับข้อมูล Stride (ก้าวที่ {sc})")
        if sc not in self.steps:
            self.steps[sc] = {}
        self.steps[sc]['stride'] = stride
        self._check_complete(sc)

    def add_pronation(self, pronation):
        sc = pronation.step_count
        print(f"[{self.side.upper()}] ได้รับข้อมูล Pronation (ก้าวที่ {sc})")
        if sc not in self.steps:
            self.steps[sc] = {}
        self.steps[sc]['pronation'] = pronation
        self._check_complete(sc)

    def _check_complete(self, sc):
        step = self.steps[sc]
        if 'gait' in step and 'stride' in step and 'pronation' in step:
            # รวมข้อมูลครบถ้วนสำหรับก้าวนี้แล้ว
            self.on_step_complete_cb(self.side, sc, step['gait'], step['stride'], step['pronation'])
            # ลบข้อมูลก้าวเก่า ๆ เพื่อป้องกันไม่ให้กินหน่วยความจำ
            self.steps = {k: v for k, v in self.steps.items() if k >= sc}

# ตัวแปรเก็บข้อมูลก้าวล่าสุดของแต่ละข้าง
latest_left_step = None
latest_right_step = None
total_distance_accumulated = 0.0
start_time = 0.0
csv_filename = "orphe_gait_analysis.csv"

# ฟังก์ชันปรับค่าให้เป็นสเกลคะแนน 5 ระดับ (ทีละ 20 แต้ม: 20, 40, 60, 80, 100)
def quantize_to_20(value):
    val = round(value / 20) * 20
    return max(20, min(100, val))

# ฟังก์ชันคำนวณและบันทึกข้อมูลลงไฟล์ CSV เมื่อได้ก้าวใหม่
def process_and_save_step(side, step_count, gait, stride, pronation):
    global latest_left_step, latest_right_step, total_distance_accumulated, start_time
    
    current_step_data = {
        'gait': gait,
        'stride': stride,
        'pronation': pronation
    }
    
    if side == 'left':
        latest_left_step = current_step_data
    else:
        latest_right_step = current_step_data

    # ต้องการข้อมูลของทั้งซ้ายและขวาอย่างน้อยข้างละ 1 ก้าวจึงจะเริ่มคำนวณเปรียบเทียบและบันทึก
    if latest_left_step is None or latest_right_step is None:
        print(f"[{side.upper()}] Step {step_count} logged. Waiting for the other side to start...")
        return

    # ดึงข้อมูลจากฝั่งซ้าย
    g_l = latest_left_step['gait']
    s_l = latest_left_step['stride']
    p_l = latest_left_step['pronation']

    # ดึงข้อมูลจากฝั่งขวา
    g_r = latest_right_step['gait']
    s_r = latest_right_step['stride']
    p_r = latest_right_step['pronation']

    # --- เริ่มกระบวนการคำนวณทางชีวกลศาสตร์ (Biomechanical Calculations) ---
    
    # 01-02. เวลา
    dt_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    frames = int((elapsed * 30) % 30) # สมมติ 30 fps
    time_str = f"{minutes}'{seconds:02d}\"{frames:02d}"

    # Stride length (cm)
    stride_len_l = s_l.y[0] * 100
    stride_len_r = s_r.y[0] * 100
    stride_len_avg = (stride_len_l + stride_len_r) / 2

    # Step length (cm)
    step_len = stride_len_avg / 2

    # Stride time (s)
    stance_dur_l = g_l.standing_phase_duration[0]
    swing_dur_l = g_l.swing_phase_duration[0]
    stride_time_l = stance_dur_l + swing_dur_l

    stance_dur_r = g_r.standing_phase_duration[0]
    swing_dur_r = g_r.swing_phase_duration[0]
    stride_time_r = stance_dur_r + swing_dur_r
    
    stride_time_avg = (stride_time_l + stride_time_r) / 2

    # Speed (m/s)
    speed = (stride_len_avg / 100) / stride_time_avg if stride_time_avg > 0 else 0

    # Vertical Height (cm)
    vert_h_l = s_l.z[0] * 100
    vert_h_r = s_r.z[0] * 100
    vert_h_avg = (vert_h_l + vert_h_r) / 2

    # Swing Width (cm)
    swing_w_l = abs(s_l.x[0]) * 100
    swing_w_r = abs(s_r.x[0]) * 100
    swing_w_avg = (swing_w_l + swing_w_r) / 2

    # Stance Phase %
    stance_p_l = (stance_dur_l / stride_time_l) * 100 if stride_time_l > 0 else 0
    stance_p_r = (stance_dur_r / stride_time_r) * 100 if stride_time_r > 0 else 0
    stance_p_avg = (stance_p_l + stance_p_r) / 2

    # Double Support Phase %
    double_support = max(0.0, min(40.0, 2 * stance_p_avg - 100))

    # Swing Phase %
    swing_p_l = 100 - stance_p_l
    swing_p_r = 100 - stance_p_r
    swing_p_avg = (swing_p_l + swing_p_r) / 2

    # Strike angle (องศาตอนสัมผัสพื้น)
    strike_a_l = s_l.foot_angle[0]
    strike_a_r = s_r.foot_angle[0]
    strike_a_avg = (strike_a_l + strike_a_r) / 2

    # Toe-off angle (องศาตอนยกปลายเท้าพ้นพื้น - ประมาณการเชิงความชัน)
    toe_off_l = 30.0 + (s_l.y[0] * 5.0)
    toe_off_r = 30.0 + (s_r.y[0] * 5.0)
    toe_off_avg = (toe_off_l + toe_off_r) / 2

    # Pronation (องศาการคว่ำเท้า)
    pron_l = p_l.x[0]
    pron_r = p_r.x[0]
    pron_avg = (pron_l + pron_r) / 2

    # Progression angle (องศาแนวทางการเคลื่อนที่ของเท้า)
    prog_l = math.degrees(math.atan2(s_l.x[0], s_l.y[0])) if s_l.y[0] > 0 else 0
    prog_r = math.degrees(math.atan2(s_r.x[0], s_r.y[0])) if s_r.y[0] > 0 else 0
    prog_avg = (prog_l + prog_r) / 2

    # Landing impact (เปลี่ยนจากหน่วย Gs หรือ kgf ไปเป็น m/s² โดยประมาณ)
    impact_l = p_l.landing_impact[0] * 9.80665
    impact_r = p_r.landing_impact[0] * 9.80665
    impact_avg = (impact_l + impact_r) / 2
    impact_max = max(impact_l, impact_r)

    # ระยะสะสม
    if side == 'left':
        total_distance_accumulated += (s_l.y[0] / 2) # ก้าวทีละครึ่งของ Stride
    else:
        total_distance_accumulated += (s_r.y[0] / 2)

    # Load balance (ความสมดุลการทิ้งน้ำหนักขาข้างเดียว)
    load_balance = (stance_dur_l / (stance_dur_l + stance_dur_r)) * 100 if (stance_dur_l + stance_dur_r) > 0 else 50.0

    # Cadence (ก้าวต่อวินาที)
    cadence_l = 1.0 / stride_time_l if stride_time_l > 0 else 0
    cadence_r = 1.0 / stride_time_r if stride_time_r > 0 else 0
    cadence_avg = (cadence_l + cadence_r) / 2

    # --- การคำนวณคะแนนสัมพัทธ์ (Propulsion, Consistency, Symmetry, Absorption) [0-100] ---
    # Symmetry (ความสมมาตรซ้ายขวา)
    sym_score = 100 - abs(stride_len_l - stride_len_r) / (stride_len_avg if stride_len_avg > 0 else 1) * 200
    symmetry = quantize_to_20(sym_score)

    # Consistency (ความสม่ำเสมอในการรักษารอบวิ่ง)
    consistency = quantize_to_20(85.0 - (abs(stride_time_l - stride_time_r) * 100))

    # Propulsion (แรงส่งดันตัวไปข้างหน้า)
    prop_score = (speed * 30.0) + (cadence_avg * 20.0)
    propulsion = quantize_to_20(prop_score)

    # Absorption (การซับแรงกระแทก)
    abs_score = 120.0 - (impact_avg * 1.5)
    absorption = quantize_to_20(abs_score)

    # Gait Rhythm (ความสม่ำเสมอของจังหวะ)
    rhythm = max(50.0, min(100.0, 100.0 - abs(stride_time_l - stride_time_r) * 100))

    # สร้างแถวข้อมูล 59 คอลัมน์
    row_data = [
        dt_now,                          # 01
        time_str,                        # 02
        f"{speed:.2f}",                  # 03
        f"{step_len:.1f}",               # 04
        f"{vert_h_l:.1f}",               # 05
        f"{vert_h_r:.1f}",               # 06
        f"{vert_h_avg:.1f}",             # 07
        f"{swing_w_l:.1f}",              # 08
        f"{swing_w_r:.1f}",              # 09
        f"{swing_w_avg:.1f}",            # 10
        f"{stance_p_l:.1f}",             # 11
        f"{stance_p_r:.1f}",             # 12
        f"{stance_p_avg:.1f}",           # 13
        f"{double_support:.1f}",         # 14
        f"{rhythm:.1f}",                 # 15
        f"{strike_a_l:.1f}",             # 16
        f"{strike_a_r:.1f}",             # 17
        f"{strike_a_avg:.1f}",           # 18
        f"{toe_off_l:.1f}",              # 19
        f"{toe_off_r:.1f}",              # 20
        f"{toe_off_avg:.1f}",            # 21
        f"{pron_l:.1f}",                 # 22
        f"{pron_r:.1f}",                 # 23
        f"{pron_avg:.1f}",               # 24
        f"{prog_l:.1f}",                 # 25
        f"{prog_r:.1f}",                 # 26
        f"{prog_avg:.1f}",               # 27
        f"{impact_l:.2f}",               # 28
        f"{impact_r:.2f}",               # 29
        f"{impact_avg:.2f}",             # 30
        f"{total_distance_accumulated:.2f}", # 31
        f"{swing_p_l:.1f}",              # 32
        f"{swing_p_r:.1f}",              # 33
        f"{swing_p_avg:.1f}",            # 34
        "50.0",                          # 35 (It will always be 50%)
        "50.0",                          # 36 (It will always be 50%)
        "50.0",                          # 37 (It will always be 50%)
        f"{load_balance:.1f}",           # 38
        f"{impact_max:.2f}",             # 39
        f"{stride_len_l:.1f}",           # 40
        f"{stride_len_r:.1f}",           # 41
        f"{stride_len_avg:.1f}",         # 42
        f"{cadence_l:.2f}",              # 43
        f"{cadence_r:.2f}",              # 44
        f"{cadence_avg:.2f}",            # 45
        f"{stride_time_l:.2f}",          # 46
        f"{stride_time_r:.2f}",          # 47
        f"{stride_time_avg:.2f}",        # 48
        f"{stance_dur_l:.2f}",           # 49
        f"{stance_dur_r:.2f}",           # 50
        f"{ (stance_dur_l + stance_dur_r)/2 :.2f}", # 51
        f"{swing_dur_l:.2f}",            # 52
        f"{swing_dur_r:.2f}",            # 53
        f"{ (swing_dur_l + swing_dur_r)/2 :.2f}",  # 54
        str(propulsion),                 # 55
        str(consistency),                # 56
        str(symmetry),                   # 57
        str(absorption),                 # 58
        f"Step:{step_count} from {side.upper()}" # 59
    ]

    # บันทึกข้อมูลลง CSV
    file_exists = os.path.isfile(csv_filename)
    with open(csv_filename, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADERS)
        writer.writerow(row_data)

    print(f"--> [SAVE CSV] Step {step_count} ({side.upper()}): Stride Length L={stride_len_l:.1f}cm, R={stride_len_r:.1f}cm | Speed={speed:.2f} m/s | Impact={impact_avg:.1f} m/s² | Symmetry={symmetry}")

async def main():
    global start_time
    start_time = time.time()
    
    # ลบไฟล์เก่าออกถ้าต้องการเขียนใหม่
    if os.path.exists(csv_filename):
        os.remove(csv_filename)
        
    print(f"Creating new CSV output file: {csv_filename}")
    with open(csv_filename, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)

    # สร้าง StepAssembler สำหรับแยกจัดการซ้าย-ขวา
    left_assembler = StepAssembler('left', process_and_save_step)
    right_assembler = StepAssembler('right', process_and_save_step)

    # สร้าง Orphe 2 ตัว
    orphes = [Orphe(), Orphe()]

    # ผูก Callback จัดการสำหรับฝั่งซ้าย
    orphes[0].set_got_gait_callback(left_assembler.add_gait)
    orphes[0].set_got_stride_callback(left_assembler.add_stride)
    orphes[0].set_got_pronation_callback(left_assembler.add_pronation)

    # ผูก Callback จัดการสำหรับฝั่งขวา
    orphes[1].set_got_gait_callback(right_assembler.add_gait)
    orphes[1].set_got_stride_callback(right_assembler.add_stride)
    orphes[1].set_got_pronation_callback(right_assembler.add_pronation)

    addresses = ["F4:1B:9B:AF:20:5D", "D0:A3:43:7D:01:BD"]

    # 1. เชื่อมต่อเซนเซอร์ทั้ง 2 ตัวก่อน
    print("Connecting to both Left and Right shoes...")
    for i, orphe in enumerate(orphes):
        # พยายามเชื่อมตาม Address
        try:
            success = await orphe.connect(addresses[i])
        except Exception as e:
            print(f"ไม่สามารถเชื่อมต่อไปยัง {addresses[i]} ได้: {e}")
            success = False

        # หากไม่สำเร็จ ให้สแกนหาตัวแรกที่พร้อมเชื่อมต่อ
        if not success:
            print(f"กำลังสแกนค้นหาอุปกรณ์ ORPHE CORE ตัวที่พร้อมเชื่อมต่อโดยอัตโนมัติ...")
            try:
                success = await orphe.connect(None)
            except Exception as e:
                print(f"เกิดข้อผิดพลาดในการสแกนหาอุปกรณ์: {e}")
                success = False

        if not success:
            print(f"เชื่อมต่ออุปกรณ์ตัวที่ {i+1} ล้มเหลว!")
            return

        print(f"เชื่อมต่ออุปกรณ์ตัวที่ {i+1} สำเร็จ! กำลังดึงข้อมูลบริการ (Services)...")
        # Bleak 3.x จะโหลด GATT Services อัตโนมัติตอน connect แล้ว เข้าถึง .services เพื่อยืนยันว่าโหลดครบ
        _ = orphe.client.services

        # ดักจับและแสดงค่าดิบ BLE ที่ส่งตรงมาจากฮาร์ดแวร์
        side_name = "LEFT" if i == 0 else "RIGHT"
        original_handler = orphe.step_analysis_notification_handler
        def make_debug_handler(s_name, orig_h):
            async def debug_handler(sender, data):
                print(f"[{s_name} BLE ดิบ] ได้รับ {len(data)} ไบต์: {data.hex()}")
                await orig_h(sender, data)
            return debug_handler
        orphe.step_analysis_notification_handler = make_debug_handler(side_name, original_handler)

    # 2. เมื่อเชื่อมต่อแล้ว เปิดรับข้อมูล Step Analysis
    print("Connection established! Starting step analysis notification...")
    for orphe in orphes:
        await orphe.print_device_information()
        await orphe.start_step_analysis_notification()

    print("\n>>> System ready. Please walk or run! Press Ctrl+C to stop and save the CSV. <<<\n")

    try:
        while True:
            await asyncio.sleep(1)
            # ตรวจสอบการเชื่อมต่อหลุด
            if not orphes[0].is_connected() or not orphes[1].is_connected():
                print("One of the devices disconnected. Stopping...")
                break
    finally:
        print("Stopping notifications and disconnecting...")
        for orphe in orphes:
            if orphe.is_connected():
                await orphe.stop_step_analysis_notification()
                await orphe.disconnect()
        print("Devices disconnected. CSV file saved successfully.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nRecording stopped by user. CSV file closed.")
