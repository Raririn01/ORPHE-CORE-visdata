import asyncio
from orphe_core import Orphe  # orphe_core.pyからOrpheクラスをインポート


def lost_data1(serial_number_prev, serial_number):
    print(
        f"[1] Data loss detected. {serial_number_prev} <-> {serial_number}")


def lost_data2(serial_number_prev, serial_number):
    print(
        f"[2] Data loss detected. {serial_number_prev} <-> {serial_number}")


# Callback สำหรับรับค่าความเร่ง (Accelerometer) ของอุปกรณ์แต่ละข้าง
def got_acc_left(acc):
    print(f"[LEFT] Acc X: {acc.x:.2f}, Y: {acc.y:.2f}, Z: {acc.z:.2f}")


def got_acc_right(acc):
    print(f"[RIGHT] Acc X: {acc.x:.2f}, Y: {acc.y:.2f}, Z: {acc.z:.2f}")


async def main():
    # orphe = Orphe()
    orphes = [Orphe(), Orphe()]

    orphes[0].set_lost_data_callback(lost_data1)
    orphes[1].set_lost_data_callback(lost_data2)
    
    # ลงทะเบียน callback เพื่อดึงค่าของแต่ละข้าง
    orphes[0].set_got_converted_acc_callback(got_acc_left)
    orphes[1].set_got_converted_acc_callback(got_acc_right)

    # 接続するデバイスを指定する場合はコアモジュールのaddressをconnect()の引数に文字列として渡してください。addressを知りたい場合はコアモジュールに接続するとコンソールに表示されます。文字列が空の場合はSERVICE UUIDがORPHEと合致する最初に見つかったデバイスに接続します。

    #

    addresses = ["F4:1B:9B:AF:20:5D", "D0:A3:43:7D:01:BD"]
    
    try:
        # 1. Connect to all devices first
        for i, orphe in enumerate(orphes):
            if not await orphe.connect(addresses[i]):
                print(f"Failed to connect to device: {addresses[i]}")
                return

        # 2. Once all connected, print info and start notifications
        for orphe in orphes:
            await orphe.print_device_information()
            await orphe.set_led_brightness(255)
            await orphe.start_sensor_values_notification()

        while True:
            await orphes[0].set_led(1, 0)
            await orphes[1].set_led(0, 0)
            await asyncio.sleep(1)
            await orphes[0].set_led(0, 0)
            await orphes[1].set_led(1, 0)
            await asyncio.sleep(1)
    finally:
        print("Disconnecting from all connected devices...")
        for orphe in orphes:
            if orphe.is_connected():
                await orphe.disconnect()

# 色々記述していますが，Ctrl+Cでプログラムを終了した場合にきれいに終了処理するためのものです．最悪 asyncio.run(main()) だけでも動きます．
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_task = loop.create_task(main())

    try:
        loop.run_until_complete(main_task)
    except KeyboardInterrupt:
        print("KeyboardInterrupt(Ctrl+C) received. Canceling the main task...")
        main_task.cancel()
        try:
            loop.run_until_complete(main_task)
        except asyncio.CancelledError:
            pass
    finally:
        loop.close()
        print("Event loop closed.")
